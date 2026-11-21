# SPDX-License-Identifier: CC0-1.0
"""Phase 0 harness: run Blender's Mesh Bevel node with constant-radius offsets
and measure the result against the analytic rolling-ball surface.

Run inside Blender:  blender -b --factory-startup --python run_phase0.py
"""
import math
import time

import bmesh
import bpy
import numpy as np
from mathutils import Vector

# ---------------------------------------------------------------- node group


def build_node_group(name="CRBevelProbe", segments=8, shape=0.5):
    """GN group: Mesh Bevel driven by edge attributes 'sel' (bool) and 'd' (float).

    Optional per-side attributes 'dsl','dsr','del','der' override 'd' when present
    (not used yet). Node outputs are stored as attributes for inspection.
    """
    ng = bpy.data.node_groups.get(name)
    if ng:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")

    sel = N.new("GeometryNodeInputNamedAttribute")
    sel.data_type = "BOOLEAN"
    sel.inputs["Name"].default_value = "sel"
    d = N.new("GeometryNodeInputNamedAttribute")
    d.data_type = "FLOAT"
    d.inputs["Name"].default_value = "d"

    bev = N.new("GeometryNodeMeshBevel")
    bev.inputs["Segments"].default_value = segments
    bev.inputs["Shape"].default_value = shape
    L.new(gi.outputs[0], bev.inputs["Mesh"])
    L.new(sel.outputs["Attribute"], bev.inputs["Selection"])
    for s in ("Start Left Offset", "Start Right Offset", "End Left Offset", "End Right Offset"):
        L.new(d.outputs["Attribute"], bev.inputs[s])

    geo = bev.outputs["Mesh"]
    for out_name, attr, domain in (
        ("Vertex Face", "o_vface", "FACE"),
        ("Edge Face", "o_eface", "FACE"),
        ("Outer Edge", "o_outer", "EDGE"),
        ("Mid Edge", "o_mid", "EDGE"),
    ):
        st = N.new("GeometryNodeStoreNamedAttribute")
        st.data_type = "BOOLEAN"
        st.domain = domain
        st.inputs["Name"].default_value = attr
        L.new(geo, st.inputs["Geometry"])
        L.new(bev.outputs[out_name], st.inputs["Value"])
        geo = st.outputs["Geometry"]
    L.new(geo, go.inputs[0])
    return ng


# ---------------------------------------------------------------- geometry


def edge_frames(bm, r):
    """Per selected manifold edge: phi, convex sign, offset d, axis point/dir."""
    out = {}
    for e in bm.edges:
        if len(e.link_faces) != 2:
            continue
        f1, f2 = e.link_faces
        n1, n2 = f1.normal.copy(), f2.normal.copy()
        phi = n1.angle(n2)
        if phi < 1e-4:
            continue
        lp = next(l for l in e.link_loops if l.face == f1)
        dvec = lp.link_loop_next.vert.co - lp.vert.co
        convex = n1.cross(n2).dot(dvec) > 0
        s = 1.0 if convex else -1.0
        bis = (n1 + n2).normalized()
        a0 = e.verts[0].co - s * r / math.cos(phi / 2) * bis
        u = (e.verts[1].co - e.verts[0].co).normalized()
        out[e.index] = dict(
            phi=phi, s=s, d=r * math.tan(phi / 2), a0=a0, u=u,
            v=(e.verts[0].index, e.verts[1].index), faces=(f1.index, f2.index),
            n=(n1, n2), length=e.calc_length(),
        )
    return out


def corner_centers(bm, frames, selected, r):
    """Ball centre for vertices where exactly 3 selected edges of equal sign meet
    and the vertex has exactly 3 faces."""
    cs = {}
    for v in bm.verts:
        es = [e.index for e in v.link_edges if e.index in selected]
        if len(es) != 3 or len(v.link_faces) != 3:
            continue
        signs = {frames[i]["s"] for i in es}
        if len(signs) != 1:
            continue
        s = signs.pop()
        A = np.array([f.normal[:] for f in v.link_faces])
        b = np.array([f.normal.dot(v.co) - s * r for f in v.link_faces])
        cs[v.index] = Vector(np.linalg.solve(A, b))
    return cs


# ---------------------------------------------------------------- run + measure


def apply_probe(obj, ng):
    mod = obj.modifiers.new("probe", "NODES")
    mod.node_group = ng
    t0 = time.perf_counter()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    dt = time.perf_counter() - t0
    return ev, me, dt


def attr_np(me, name, domain_len, kind="bool"):
    a = me.attributes.get(name)
    if a is None:
        return None
    if kind == "bool":
        arr = np.zeros(domain_len, dtype=bool)
    elif kind == "int":
        arr = np.zeros(domain_len, dtype=np.int32)
    else:
        arr = np.zeros(domain_len, dtype=np.float32)
    a.data.foreach_get("value", arr)
    return arr


def dist_to_axis(p, f):
    """Distance from p to the axis segment of edge frame f (clamped to segment)."""
    w = p - f["a0"]
    t = max(0.0, min(f["length"], w.dot(f["u"])))
    q = f["a0"] + f["u"] * t
    return (p - q).length, t


def measure(me, frames, selected, centers, r, tag):
    """Classify output vertices and return error statistics (in units of r)."""
    nv = len(me.vertices)
    co = [Vector(v.co) for v in me.vertices]
    vface = attr_np(me, "o_vface", len(me.polygons))
    eface = attr_np(me, "o_eface", len(me.polygons))
    in_v = np.zeros(nv, bool)
    in_e = np.zeros(nv, bool)
    for p in me.polygons:
        if vface[p.index]:
            in_v[list(p.vertices)] = True
        if eface[p.index]:
            in_e[list(p.vertices)] = True

    strip_err, corner_err, other_corner = [], [], 0
    for i in range(nv):
        p = co[i]
        if in_e[i]:
            best = min((dist_to_axis(p, frames[e]) + (e,) for e in selected), key=lambda x: abs(x[0] - r))
            strip_err.append(abs(best[0] - r) / r)
        elif in_v[i]:
            c = min(centers.values(), key=lambda c: (p - c).length) if centers else None
            if c is not None and (p - c).length < 3 * r:
                corner_err.append(abs((p - c).length - r) / r)
            else:
                other_corner += 1
    res = dict(
        tag=tag, verts=nv, faces=len(me.polygons),
        strip_n=len(strip_err), strip_max=max(strip_err, default=0), strip_mean=float(np.mean(strip_err)) if strip_err else 0,
        corner_n=len(corner_err), corner_max=max(corner_err, default=0),
        corner_mean=float(np.mean(corner_err)) if corner_err else 0, corner_verts_without_exact_center=other_corner,
    )
    return res


def outer_offset_check(me, frames, selected, bm_orig, r):
    """For output edges flagged Outer Edge: their vertices should lie on the tangent
    lines, i.e. at perpendicular in-face distance d from the original edge.
    Returns the worst relative error |dist - d| / d over all outer vertices,
    matched to the nearest selected original edge lying in the same face plane."""
    outer = attr_np(me, "o_outer", len(me.edges))
    vids = set()
    for e in me.edges:
        if outer[e.index]:
            vids.update(e.vertices)
    worst = 0.0
    worst_info = None
    for vi in vids:
        p = Vector(me.vertices[vi].co)
        best = None
        for ei in selected:
            f = frames[ei]
            e = bm_orig.edges[ei]
            a, b = e.verts[0].co, e.verts[1].co
            u = (b - a).normalized()
            t = (p - a).dot(u)
            if t < -1e-6 or t > (b - a).length + 1e-6:
                continue
            for n in f["n"]:
                off_plane = abs((p - a).dot(n))
                if off_plane > 1e-4:
                    continue
                perp = ((p - a) - u * t).length
                if perp > 1.5 * f["d"] + 1e-6:
                    continue
                err = abs(perp - f["d"]) / f["d"]
                if best is None or err > best[0]:
                    best = (err, ei, perp, f["d"])
        if best and best[0] > worst:
            worst, worst_info = best[0], best
    return worst, worst_info


def prepare(obj, r, select_fn):
    """Compute frames on the original mesh, write 'sel' and 'd' edge attributes."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    frames = edge_frames(bm, r)
    selected = [i for i in frames if select_fn(bm.edges[i], frames[i])]
    sel = me.attributes.new("sel", "BOOLEAN", "EDGE")
    dd = me.attributes.new("d", "FLOAT", "EDGE")
    oid_v = me.attributes.new("oid_v", "INT", "POINT")
    oid_e = me.attributes.new("oid_e", "INT", "EDGE")
    for i in range(len(me.edges)):
        sel.data[i].value = i in selected
        dd.data[i].value = frames[i]["d"] if i in frames else 0.0
        oid_e.data[i].value = i + 1000
    for i in range(len(me.vertices)):
        oid_v.data[i].value = i + 1000
    centers = corner_centers(bm, frames, set(selected), r)
    return bm, frames, selected, centers


def new_object(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob
