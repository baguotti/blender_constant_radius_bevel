"""Analytic reference geometry and measurements for CR Bevel tests.

Everything here works on the ORIGINAL mesh (bmesh) to compute where the exact
rolling-ball surface is, and on the EVALUATED mesh to measure how far the
modifier result is from it. Errors are reported relative to the radius r.
"""
import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector


# ---------------------------------------------------------------- scene helpers


def new_object(name, verts, faces, edges=()):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, list(edges), faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def extrude_polygon(name, pts2d, h=1.0):
    n = len(pts2d)
    verts = [(x, y, 0) for x, y in pts2d] + [(x, y, h) for x, y in pts2d]
    faces = [list(range(n))[::-1], list(range(n, 2 * n))]
    faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return new_object(name, verts, faces)


def socket_ids(ng):
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, "in_out", None) == "INPUT"}


def add_cr_bevel(obj, ng, radius, segments=8, angle_limit=0.0, selection_attr=None, exact=True):
    mod = obj.modifiers.new("CR Bevel", "NODES")
    mod.node_group = ng
    set_input(mod, "Radius", radius)
    set_input(mod, "Segments", segments)
    set_input(mod, "Angle Limit", angle_limit)
    set_input(mod, "Exact Profile", exact)
    if selection_attr:
        set_input(mod, "Selection", attribute=selection_attr)
    return mod


def set_input(mod, name, value=None, attribute=None):
    """Set a Geometry Nodes modifier input by its interface name (Blender 5.x API)."""
    item = getattr(mod.properties.inputs, socket_ids(mod.node_group)[name])
    if attribute is not None:
        item.type = "ATTRIBUTE"
        item.attribute_name = attribute
    else:
        item.value = value


def evaluate(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    return ev.to_mesh()


def set_edge_selection(obj, fn, name="sel"):
    """fn(bm_edge) -> bool. Writes a boolean edge attribute."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.edges.ensure_lookup_table()
    a = me.attributes.new(name, "BOOLEAN", "EDGE")
    for e in bm.edges:
        a.data[e.index].value = bool(fn(e))
    bm.free()


# ---------------------------------------------------------------- analytic frames


def original_bmesh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
    bm.normal_update()
    return bm


def edge_frames(bm, r, selected):
    """Exact cylinder for each selected manifold edge."""
    out = {}
    for ei in selected:
        e = bm.edges[ei]
        f1, f2 = e.link_faces
        n1, n2 = f1.normal.copy(), f2.normal.copy()
        phi = n1.angle(n2)
        lp = next(l for l in e.link_loops if l.face == f1)
        convex = n1.cross(n2).dot(lp.link_loop_next.vert.co - lp.vert.co) > 0
        s = 1.0 if convex else -1.0
        bis = (n1 + n2).normalized()
        a0 = e.verts[0].co - s * r / math.cos(phi / 2) * bis
        out[ei] = dict(
            phi=phi, s=s, d=r * math.tan(phi / 2), a0=a0,
            u=(e.verts[1].co - e.verts[0].co).normalized(), length=e.calc_length(),
            v=(e.verts[0].index, e.verts[1].index), n=(n1, n2),
        )
    return out


def selected_edges(obj, attr="sel"):
    a = obj.data.attributes.get(attr)
    vals = np.zeros(len(obj.data.edges), bool)
    a.data.foreach_get("value", vals)
    return [int(i) for i in np.nonzero(vals)[0]]


# ---------------------------------------------------------------- measurements


def face_flag_vertices(me, name):
    a = me.attributes.get(name)
    flags = np.zeros(len(me.polygons), bool)
    a.data.foreach_get("value", flags)
    on = np.zeros(len(me.vertices), bool)
    for p in me.polygons:
        if flags[p.index]:
            on[list(p.vertices)] = True
    return on


def strip_error(me, frames, r, vertex_mask):
    """Max/mean |dist(vertex, nearest axis) - r| / r over masked vertices."""
    errs = []
    for i in np.nonzero(vertex_mask)[0]:
        p = Vector(me.vertices[i].co)
        best = None
        for f in frames.values():
            w = p - f["a0"]
            t = max(0.0, min(f["length"], w.dot(f["u"])))
            dist = (p - (f["a0"] + f["u"] * t)).length
            e = abs(dist - r) / r
            if best is None or e < best:
                best = e
        errs.append(best)
    return (max(errs) if errs else 0.0, float(np.mean(errs)) if errs else 0.0, len(errs))


def untouched_error(me_out, bm_orig, touched_vids):
    """Original vertices that should not move: max distance to the nearest output vertex."""
    out = np.array([v.co[:] for v in me_out.vertices])
    worst = 0.0
    for v in bm_orig.verts:
        if v.index in touched_vids:
            continue
        dmin = np.min(np.linalg.norm(out - np.array(v.co[:]), axis=1))
        worst = max(worst, float(dmin))
    return worst


def has_internal_attributes(me):
    return sorted(a.name for a in me.attributes if a.name.startswith("_cr_"))
