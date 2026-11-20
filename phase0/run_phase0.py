import json
import math
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase0lib as P  # noqa: E402

R = 0.2
SEG = 8
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT, exist_ok=True)
results = {"blender": bpy.app.version_string, "radius": R, "segments": SEG, "tests": []}


def extrude_polygon(name, pts2d, h=1.0):
    n = len(pts2d)
    verts = [(x, y, 0) for x, y in pts2d] + [(x, y, h) for x, y in pts2d]
    faces = [list(range(n))[::-1], list(range(n, 2 * n))]
    faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return P.new_object(name, verts, faces)


def wedge(alpha_deg, L=2.0):
    """Polygon with one corner of interior angle alpha at the origin (convex)."""
    a = math.radians(alpha_deg)
    p0 = (0.0, 0.0)
    p1 = (L, 0.0)
    p2 = (L * math.cos(a), L * math.sin(a))
    return [p0, p1, p2]


def run(tag, obj, select_fn, ng, extra=None):
    bm, frames, selected, centers = P.prepare(obj, R, select_fn)
    ev, me, dt = P.apply_probe(obj, ng)
    res = P.measure(me, frames, selected, centers, R, tag)
    w, info = P.outer_offset_check(me, frames, selected, bm, R)
    res["outer_offset_rel_err_max"] = w
    res["outer_offset_worst"] = None if not info else dict(edge=info[1], measured=info[2], expected=info[3])
    res["eval_seconds"] = dt
    res["selected_edges"] = len(selected)
    res["corner_centers"] = len(centers)
    if extra:
        res.update(extra(me, bm, frames, selected))
    results["tests"].append(res)
    print("RESULT", json.dumps(res, default=float))
    return me, ev


ng = P.build_node_group(segments=SEG)

# 1. Angle sweep: single convex vertical edge of a wedge prism ---------------
for alpha in (30, 45, 60, 90, 120, 135, 150, 165):
    ob = extrude_polygon(f"wedge_{alpha}", wedge(alpha))
    def sel(e, f, ob=ob):  # the vertical edge at the origin
        a, b = e.verts[0].co, e.verts[1].co
        return abs(a.x) < 1e-6 and abs(a.y) < 1e-6 and abs(b.x) < 1e-6 and abs(b.y) < 1e-6
    run(f"wedge_convex_interior_{alpha}deg", ob, sel, ng, extra=lambda me, bm, fr, s: {"phi_deg": math.degrees(fr[s[0]]["phi"])})

# 2. Concave edge: L-shaped prism, inner vertical edge only -------------------
L_pts = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
ob = extrude_polygon("L_concave", L_pts)
run("L_concave_inner_edge", ob,
    lambda e, f: f["s"] < 0, ng)

# 3. Skewed box (all 12 edges, 3-valent corners, non-90 angles) --------------
sk = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0),
      (0.2, 0.1, 1.5), (2.1, 0.3, 1.4), (2.3, 1.5, 1.6), (0.5, 1.6, 1.5)]
# make top face planar: use a sheared copy of bottom instead
sk = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
sk += [(x + 0.5, y + 0.2, 1.5) for x, y, _ in sk[:4]]
faces = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
ob = P.new_object("skew_box", sk, faces)
run("skew_box_all_edges", ob, lambda e, f: True, ng)

# 4. Cube: all edges (90 deg reference) ----------------------------------------
cv = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
cf = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [1, 3, 7, 5], [3, 2, 6, 7], [2, 0, 4, 6]]
ob = P.new_object("cube_all", [tuple(2 * c for c in v) for v in cv], cf)
run("cube_all_edges", ob, lambda e, f: True, ng)

# 5. Cube: single edge (terminal ends) -----------------------------------------
ob = P.new_object("cube_one", [tuple(2 * c for c in v) for v in cv], cf)
run("cube_single_edge_terminal", ob,
    lambda e, f: {v.index for v in e.verts} == {0, 1}, ng)

# 6. Tapered prism, top loop only (chain + unbeveled edges between) -----------
base = [(0, 0), (3, 0), (3.5, 2), (1, 3), (-0.5, 1.5)]
cx, cy = 1.4, 1.3
top = [(cx + 0.6 * (x - cx), cy + 0.6 * (y - cy)) for x, y in base]
n = len(base)
verts = [(x, y, 0) for x, y in base] + [(x, y, 1.2) for x, y in top]
faces = [list(range(n))[::-1], list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
ob = P.new_object("taper_top_loop", verts, faces)
run("tapered_prism_top_loop_chain", ob,
    lambda e, f: all(v.co.z > 1.0 for v in e.verts), ng,
    extra=lambda me, bm, fr, s: {"phi_deg_per_edge": sorted(round(math.degrees(fr[i]["phi"]), 2) for i in s)})


# 6b. L-shape, all edges (mixed convex/concave corners) ------------------------
ob = extrude_polygon("L_all", L_pts)
run("L_all_edges_mixed_corners", ob, lambda e, f: True, ng)

# 6c. Pentagonal pyramid, all edges (5-valent apex) ------------------------------
pv = [(math.cos(2 * math.pi * i / 5), math.sin(2 * math.pi * i / 5), 0) for i in range(5)] + [(0, 0, 1.2)]
pf = [[4, 3, 2, 1, 0]] + [[i, (i + 1) % 5, 5] for i in range(5)]
ob = P.new_object("pyramid5", pv, pf)
run("pyramid5_all_edges_apex", ob, lambda e, f: True, ng)

# 7. Attribute survival --------------------------------------------------------
def survival(me, bm, fr, s):
    nv = len(me.vertices)
    of = P.attr_np(me, "oid_f", len(me.polygons), "int")
    vface = P.attr_np(me, "o_vface", len(me.polygons))
    eface = P.attr_np(me, "o_eface", len(me.polygons))
    face_info = {
        "oid_f_on_edge_faces": sorted(set(of[eface].tolist())) if of is not None else None,
        "oid_f_on_vertex_faces": sorted(set(of[vface].tolist())) if of is not None else None,
    }
    ov = P.attr_np(me, "oid_v", nv, "int")
    ne = len(me.edges)
    oe = P.attr_np(me, "oid_e", ne, "int")
    return {
        "oid_v_present": ov is not None,
        "oid_v_unique_values_sample": sorted(set(ov.tolist()))[:20] if ov is not None else None,
        "oid_v_count_zero": int((ov == 0).sum()) if ov is not None else None,
        "oid_e_present": oe is not None,
        "oid_e_unique_values_sample": sorted(set(oe.tolist()))[:30] if oe is not None else None,
        "oid_e_count_zero": int((oe == 0).sum()) if oe is not None else None,
        "orig_verts": len(bm.verts), "orig_edges": len(bm.edges),
        **face_info,
    }


ob = P.new_object("cube_attr", [tuple(2 * c for c in v) for v in cv], cf)
_fa = ob.data.attributes.new("oid_f", "INT", "FACE")
for _i in range(len(ob.data.polygons)):
    _fa.data[_i].value = _i + 2000
run("attribute_survival_cube", ob, lambda e, f: True, ng, extra=survival)

# 8. Performance: grid of cubes, all edges --------------------------------------
import bmesh  # noqa: E402
bm = bmesh.new()
k = 0
for i in range(30):
    for j in range(30):
        for kk in range(15):
            ret = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.translate(bm, vec=(i * 2.0, j * 2.0, kk * 2.0), verts=ret["verts"])
me = bpy.data.meshes.new("perf")
bm.to_mesh(me)
bm.free()
ob = bpy.data.objects.new("perf", me)
bpy.context.scene.collection.objects.link(ob)
sel = me.attributes.new("sel", "BOOLEAN", "EDGE")
dd = me.attributes.new("d", "FLOAT", "EDGE")
sel.data.foreach_set("value", np.ones(len(me.edges), bool))
dd.data.foreach_set("value", np.full(len(me.edges), R * 0.5, np.float32))
for seg in (1, 4, 8):
    ngp = P.build_node_group(f"perf_{seg}", segments=seg)
    for m in list(ob.modifiers):
        ob.modifiers.remove(m)
    ev, me_out, dt = P.apply_probe(ob, ngp)
    r = dict(tag=f"perf_cubes_seg{seg}", in_faces=len(me.polygons), out_faces=len(me_out.polygons), eval_seconds=dt)
    results["tests"].append(r)
    print("RESULT", json.dumps(r))
    ev.to_mesh_clear()
ob.modifiers.clear()

with open(os.path.join(OUT, "phase0_results.json"), "w") as fh:
    json.dump(results, fh, indent=2, default=float)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "phase0_tests.blend"))
print("DONE")
