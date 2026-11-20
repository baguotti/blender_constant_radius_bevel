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


# ---------------------------------------------------------------- Phase 2: vertex classification

FALLBACK, TERMINAL, PIPE, JOINT, CORNER = range(5)
MODE_NAMES = ["fallback", "terminal", "pipe", "joint", "corner"]


def _line_dist(p, a, u):
    w = p - a
    return (w - u * w.dot(u)).length


def classify_vertices(bm, frames, r, gap_tol=1e-3):
    """Independent re-implementation of the node group's vertex modes.

    Returns {vertex index: dict(mode=..., centre=Vector or None, end_n=..., u=...)}
    for every vertex touched by a selected edge.
    """
    out = {}
    for v in bm.verts:
        es = [e.index for e in v.link_edges if e.index in frames]
        if not es:
            continue
        lines = []
        for ei in es:
            f = frames[ei]
            off = f["a0"] - bm.edges[ei].verts[0].co      # same offset at both ends
            lines.append((v.co + off, f["u"], f["s"]))
        info = dict(mode=FALLBACK, centre=None, end_n=None, u=lines[0][1], edges=es)
        nf = len(v.link_faces)
        if len(es) == 1 and nf == 3:
            u = lines[0][1]
            info["end_n"] = max((f.normal for f in v.link_faces), key=lambda n: abs(n.dot(u))).copy()
            info["mode"] = TERMINAL
        elif len(es) in (2, 3):
            (a0, u0, s0), (a1, u1, s1) = lines[:2]
            b = u0.dot(u1)
            den = 1 - b * b
            if den < 1e-8:
                if len(es) == 2 and _line_dist(a1, a0, u0) < gap_tol * r:
                    info["mode"] = PIPE
            else:
                w = a0 - a1
                t0 = (b * u1.dot(w) - u0.dot(w)) / den
                t1 = (u1.dot(w) - b * u0.dot(w)) / den
                p0, p1 = a0 + u0 * t0, a1 + u1 * t1
                meet = (p0 + p1) / 2
                ok = (p0 - p1).length < gap_tol * r and s0 == s1 and nf == 3
                if len(es) == 2 and ok:
                    info.update(mode=JOINT, centre=meet)
                elif len(es) == 3 and ok and lines[2][2] == s0 and _line_dist(meet, lines[2][0], lines[2][1]) < gap_tol * r:
                    info.update(mode=CORNER, centre=meet)
        out[v.index] = info
    return out


def exact_error(me, bm, frames, modes, r):
    """Per bevel vertex: distance to the exact surface of its original vertex.

    Returns dict mode_name -> [max_err/r, n], plus 'terminal_plane' (end-cap
    vertices' distance from the end face) and 'joint_seam_end' (seam vertices on
    an original face; the one next to the unbeveled edge cannot be exact on the
    native topology when the two edges have different angles), and
    the output vertex indices owned by fallback vertices. JOINT seam vertices
    must lie on BOTH cylinders.
    """
    on = face_flag_vertices(me, "cr_bevel_face")
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    touches_original = np.zeros(len(me.vertices), bool)
    for poly in me.polygons:
        if not flags[poly.index]:
            touches_original[list(poly.vertices)] = True
    orig = np.array([v.co[:] for v in bm.verts])
    touched = np.array(sorted(modes))
    stats = {m: [0.0, 0] for m in MODE_NAMES}
    stats["terminal_plane"] = [0.0, 0]
    fallback_out = []
    for i in np.nonzero(on)[0]:
        p = Vector(me.vertices[i].co)
        vi = int(touched[np.argmin(np.linalg.norm(orig[touched] - np.array(p[:]), axis=1))])
        info = modes[vi]
        m = info["mode"]
        if m == FALLBACK:
            fallback_out.append(int(i))
            continue
        if m == CORNER:
            err = abs((p - info["centre"]).length - r)
        elif m == JOINT:
            err = max(abs(_line_dist(p, frames[ei]["a0"], frames[ei]["u"]) - r) for ei in info["edges"])
            if touches_original[i]:
                st = stats.setdefault("joint_seam_end", [0.0, 0])
                st[0], st[1] = max(st[0], err / r), st[1] + 1
                continue
        else:
            err = min(abs(_line_dist(p, frames[ei]["a0"], frames[ei]["u"]) - r) for ei in info["edges"])
        st = stats[MODE_NAMES[m]]
        st[0], st[1] = max(st[0], err / r), st[1] + 1
        if m == TERMINAL:
            pe = abs((p - bm.verts[vi].co).dot(info["end_n"]))
            stp = stats["terminal_plane"]
            stp[0], stp[1] = max(stp[0], pe / r), stp[1] + 1
    return stats, fallback_out


# ---------------------------------------------------------------- Phase 3: validity and shading


def inverted_faces(me, bm):
    """Count output faces whose orientation is wrong.

    - Original-face remnants: must face the same way as the original face whose
      plane they lie in.
    - Bevel faces: geometric normal must agree with the mean of their corner
      normals. Use it on a result without custom normals (e.g. the native
      bevel), where corner normals are Blender's own smooth normals.
    """
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    planes = [(f.normal.copy(), f.verts[0].co.copy()) for f in bm.faces]
    cn = me.corner_normals
    bad = 0
    for p in me.polygons:
        if p.area < 1e-12:
            continue
        n = Vector(p.normal)
        if flags[p.index]:
            mean = sum((Vector(cn[li].vector) for li in p.loop_indices), Vector())
            bad += mean.dot(n) <= 0
        else:
            c = Vector(p.center)
            same = [pn.dot(n) for pn, p0 in planes if abs((c - p0).dot(pn)) < 1e-5 and abs(abs(pn.dot(n)) - 1) < 1e-3]
            bad += bool(same) and max(same) < 0
    return int(bad)


def normal_errors(me, bm, frames, modes, r):
    """Max angle (rad) between custom corner normals and the exact surface normal.

    Returns (bevel corners at exact vertices, flat corners, n_bevel, n_flat).
    JOINT seam vertices are skipped (the exact surface has a crease there).
    """
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    orig = np.array([v.co[:] for v in bm.verts])
    touched = np.array(sorted(modes))
    cn = me.corner_normals
    worst_b = worst_f = 0.0
    nb = nf = 0
    for p in me.polygons:
        for li in p.loop_indices:
            n = Vector(cn[li].vector)
            if not flags[p.index]:
                worst_f, nf = max(worst_f, n.angle(p.normal)), nf + 1
                continue
            v = Vector(me.vertices[me.loops[li].vertex_index].co)
            vi = int(touched[np.argmin(np.linalg.norm(orig[touched] - np.array(v[:]), axis=1))])
            info = modes[vi]
            m = info["mode"]
            if m in (FALLBACK, JOINT):
                continue
            s = frames[info["edges"][0]]["s"]
            if m == CORNER and abs((v - info["centre"]).length - r) < 1e-4 * r and \
                    min(abs(_line_dist(v, frames[e]["a0"], frames[e]["u"]) - r) for e in info["edges"]) > 1e-4 * r:
                exact = (v - info["centre"]).normalized() * s
            else:
                e = min(info["edges"], key=lambda e: abs(_line_dist(v, frames[e]["a0"], frames[e]["u"]) - r))
                w = v - frames[e]["a0"]
                exact = (w - frames[e]["u"] * w.dot(frames[e]["u"])).normalized() * s
            worst_b, nb = max(worst_b, n.angle(exact)), nb + 1
    return worst_b, worst_f, nb, nf


def count_flips(me_a, me_b):
    """Faces whose orientation differs between two results of the same bevel.

    Faces are matched by their vertex index sets: the Mesh Bevel node keeps the
    vertex order between runs but not always the face order. Returns
    (flipped, unmatched); unmatched > 0 means the two results don't line up.
    """
    if len(me_a.vertices) != len(me_b.vertices):
        return -1, len(me_a.polygons)
    normals_b = {frozenset(p.vertices): p.normal.copy() for p in me_b.polygons}
    flipped = unmatched = 0
    for p in me_a.polygons:
        n = normals_b.get(frozenset(p.vertices))
        if n is None:
            unmatched += 1
        elif p.normal.dot(n) < 0:
            flipped += 1
    return flipped, unmatched
