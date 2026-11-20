"""Phase 1 tests: exact cylindrical edge strips.

Run:  blender -b --factory-startup --python tests/test_phase1.py
Writes tests/results/phase1_results.json and tests/results/phase1_tests.blend.
Exit code is non-zero if any check fails.
"""
import json
import math
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel  # noqa: E402

R = 0.2
SEG = 8
TOL = 1e-5          # relative to r; float32 positions give ~1e-7
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)

ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "radius": R, "segments": SEG, "tolerance": TOL, "checks": []}
failures = []


def check(name, value, limit, **extra):
    ok = bool(value <= limit)
    report["checks"].append(dict(name=name, value=float(value), limit=limit, ok=ok, **extra))
    print(("PASS " if ok else "FAIL ") + f"{name}: {value:.3e} (limit {limit:.0e})")
    if not ok:
        failures.append(name)


def vertical(e):
    a, b = e.verts[0].co, e.verts[1].co
    return abs(a.x - b.x) < 1e-9 and abs(a.y - b.y) < 1e-9


# ---------------------------------------------------------------- 1. Fillet Curve ground truth

POLY = [(0.0, 0.0), (3.0, -0.4), (3.6, 1.2), (2.2, 1.0), (2.6, 2.6), (0.4, 3.0), (-0.6, 1.4)]


def fillet_reference(pts, r, count=16):
    """Run Blender's Fillet Curve node on the polygon; return evaluated 2D points."""
    n = len(pts)
    ob = A.new_object("fillet_ref", [(x, y, 0) for x, y in pts], [], [(i, (i + 1) % n) for i in range(n)])
    g = bpy.data.node_groups.new("FilletRef", "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
    m2c = g.nodes.new("GeometryNodeMeshToCurve")
    fil = g.nodes.new("GeometryNodeFilletCurve")
    fil.inputs["Radius"].default_value = r
    fil.inputs["Mode"].default_value = "Poly"
    fil.inputs["Count"].default_value = count
    c2m = g.nodes.new("GeometryNodeCurveToMesh")
    g.links.new(gi.outputs[0], m2c.inputs["Mesh"])
    g.links.new(m2c.outputs[0], fil.inputs["Curve"])
    g.links.new(fil.outputs[0], c2m.inputs["Curve"])
    g.links.new(c2m.outputs[0], go.inputs[0])
    mod = ob.modifiers.new("fillet", "NODES")
    mod.node_group = g
    me = A.evaluate(ob)
    return np.array([v.co[:2] for v in me.vertices])


def fit_circle(p):
    """Algebraic least-squares circle through 2D points."""
    M = np.c_[2 * p, np.ones(len(p))]
    sol, *_ = np.linalg.lstsq(M, (p ** 2).sum(1), rcond=None)
    c = sol[:2]
    return c, math.sqrt(sol[2] + c @ c)


ref = fillet_reference(POLY, R)
corners = np.array(POLY)
owner = np.argmin(np.linalg.norm(ref[:, None, :] - corners[None], axis=2), axis=1)
circles = {}
for i in range(len(POLY)):
    pts = ref[owner == i]
    c, rad = fit_circle(pts)
    ang = np.arctan2(*(pts - c).T[::-1])
    mid = math.atan2(*(pts.mean(0) - c)[::-1])
    rel = (ang - mid + math.pi) % (2 * math.pi) - math.pi
    circles[i] = dict(c=c, R=rad, lo=rel.min(), hi=rel.max(), mid=mid)
check("fillet_reference_radius", max(abs(v["R"] - R) / R for v in circles.values()), TOL)

ob = A.extrude_polygon("prism_fillet", POLY, h=1.0)
A.set_edge_selection(ob, vertical)
A.add_cr_bevel(ob, ng, R, SEG, selection_attr="sel")
me = A.evaluate(ob)
on_strip = A.face_flag_vertices(me, "cr_bevel_face")
xy = np.array([v.co[:2] for v in me.vertices])[on_strip]
own = np.argmin(np.linalg.norm(xy[:, None, :] - corners[None], axis=2), axis=1)
rad_err, span_err = 0.0, 0.0
for p, i in zip(xy, own):
    C = circles[i]
    rad_err = max(rad_err, abs(np.linalg.norm(p - C["c"]) - C["R"]) / R)
    a = math.atan2(*(p - C["c"])[::-1])
    rel = (a - C["mid"] + math.pi) % (2 * math.pi) - math.pi
    span_err = max(span_err, max(0.0, C["lo"] - rel, rel - C["hi"]))
check("prism_vs_fillet_curve_radial", rad_err, TOL, vertices=int(on_strip.sum()))
check("prism_vs_fillet_curve_outside_arc_span_rad", span_err, 1e-5)
# Tangent points: our extreme vertices per corner must coincide with the fillet arc ends.
tan_err = 0.0
for i in range(len(POLY)):
    mine = xy[own == i]
    C = circles[i]
    for bound in ("lo", "hi"):
        ang = C["mid"] + C[bound]
        end_pt = C["c"] + C["R"] * np.array([math.cos(ang), math.sin(ang)])
        tan_err = max(tan_err, float(np.min(np.linalg.norm(mine - end_pt, axis=1))) / R)
check("prism_tangent_points_match_fillet", tan_err, TOL)
report["prism_concave_corners"] = sum(1 for i in range(len(POLY))
                                      if np.cross(corners[i] - corners[i - 1], corners[(i + 1) % len(POLY)] - corners[i]) < 0)

# Original (non-bevel) faces must stay planar.
bevel_face = np.zeros(len(me.polygons), bool)
me.attributes["cr_bevel_face"].data.foreach_get("value", bevel_face)
plan = 0.0
for p in me.polygons:
    if bevel_face[p.index]:
        continue
    c, n = Vector(p.center), Vector(p.normal)
    plan = max(plan, max(abs((Vector(me.vertices[v].co) - c).dot(n)) for v in p.vertices))
check("original_faces_stay_planar", plan / R, TOL)

# Every segment count gives exact arcs.
seg_worst = 0.0
for seg in (1, 2, 3, 16):
    ob = A.extrude_polygon(f"prism_seg{seg}", POLY, h=1.0)
    A.set_edge_selection(ob, vertical)
    A.add_cr_bevel(ob, ng, R, seg, selection_attr="sel")
    me_s = A.evaluate(ob)
    pts = np.array([v.co[:2] for v in me_s.vertices])[A.face_flag_vertices(me_s, "cr_bevel_face")]
    o = np.argmin(np.linalg.norm(pts[:, None, :] - corners[None], axis=2), axis=1)
    for p, i in zip(pts, o):
        seg_worst = max(seg_worst, abs(np.linalg.norm(p - circles[i]["c"]) - circles[i]["R"]) / R)
check("segments_1_2_3_16_vs_fillet_curve", seg_worst, TOL)

# ---------------------------------------------------------------- 2. angle sweep, convex + concave


def wedge(alpha_deg, L=2.0):
    a = math.radians(alpha_deg)
    return [(0.0, 0.0), (L, 0.0), (L * math.cos(a), L * math.sin(a))]


sweep = {}
for alpha in (20, 30, 45, 60, 90, 120, 135, 150, 165, 175):
    for concave in (False, True):
        pts = wedge(alpha)
        if concave:  # notch: a reflex corner at the origin, interior angle 360 - alpha
            a = math.radians(alpha)
            pts = [(0.0, 0.0), (math.cos(a), math.sin(a)), (-3.0, 3.0), (-3.0, -3.0), (3.0, -3.0), (3.0, 0.0)]
        ob = A.extrude_polygon(f"wedge_{alpha}_{'cc' if concave else 'cv'}", pts)
        A.set_edge_selection(ob, lambda e: vertical(e) and e.verts[0].co.xy.length < 1e-9)
        A.add_cr_bevel(ob, ng, R, SEG, selection_attr="sel")
        me = A.evaluate(ob)
        bm = A.original_bmesh(ob)
        sel = A.selected_edges(ob)
        frames = A.edge_frames(bm, R, sel)
        mx, mean, n = A.strip_error(me, frames, R, A.face_flag_vertices(me, "cr_bevel_face"))
        sweep[f"{'concave' if concave else 'convex'}_{alpha}"] = dict(max=mx, mean=mean, n=n, phi_deg=math.degrees(frames[sel[0]]["phi"]))
        bm.free()
worst = max(v["max"] for v in sweep.values())
report["angle_sweep"] = sweep
check("angle_sweep_max_strip_error", worst, TOL, cases=len(sweep))

# ---------------------------------------------------------------- 3. untouched geometry + tidy output

ob = A.new_object("cube_one", [(x, y, z) for z in (0, 2) for y in (0, 2) for x in (0, 2)],
                  [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [1, 3, 7, 5], [3, 2, 6, 7], [2, 0, 4, 6]])
A.set_edge_selection(ob, lambda e: {v.index for v in e.verts} == {0, 1})
A.add_cr_bevel(ob, ng, R, SEG, selection_attr="sel")
me = A.evaluate(ob)
bm = A.original_bmesh(ob)
check("unselected_vertices_unchanged", A.untouched_error(me, bm, {0, 1}), 1e-7)
leftovers = A.has_internal_attributes(me)
check("no_internal_attributes_left", len(leftovers), 0, names=leftovers)
check("output_has_cr_bevel_face", 0 if me.attributes.get("cr_bevel_face") else 1, 0)
bm.free()

# Exact Profile off must reproduce the native node (non-zero error at 60 deg).
ob = A.extrude_polygon("wedge60_native", wedge(60))
A.set_edge_selection(ob, lambda e: vertical(e) and e.verts[0].co.xy.length < 1e-9)
A.add_cr_bevel(ob, ng, R, SEG, selection_attr="sel", exact=False)
me = A.evaluate(ob)
bm = A.original_bmesh(ob)
mx, _, _ = A.strip_error(me, A.edge_frames(bm, R, A.selected_edges(ob)), R, A.face_flag_vertices(me, "cr_bevel_face"))
report["native_profile_60deg_error"] = mx
check("exact_profile_off_matches_native_12pct", abs(mx - 0.1213), 2e-3)
bm.free()

# ---------------------------------------------------------------- 4. robustness: must not crash

robust = {}
cases = {
    "radius_zero": (A.extrude_polygon("rz", wedge(60)), dict(radius=0.0)),
    "no_selection": (A.extrude_polygon("ns", wedge(60)), dict(selection_attr="none_sel")),
    "loose_edges_only": (A.new_object("le", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1), (1, 2)]), {}),
    "single_plane": (A.new_object("pl", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [[0, 1, 2, 3]]), {}),
    "flat_grid_angle_limit": (A.new_object("fg", [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)], [[0, 1, 4, 3], [1, 2, 5, 4]]), {}),
}
for name, (ob, kw) in cases.items():
    if kw.get("selection_attr") == "none_sel":
        A.set_edge_selection(ob, lambda e: False, "none_sel")
    nv_in = len(ob.data.vertices)
    A.add_cr_bevel(ob, ng, kw.get("radius", R), SEG, angle_limit=math.radians(30), selection_attr=kw.get("selection_attr"))
    try:
        me = A.evaluate(ob)
        robust[name] = dict(ok=True, verts_in=nv_in, verts_out=len(me.vertices))
    except Exception as exc:  # pragma: no cover
        robust[name] = dict(ok=False, error=str(exc))
report["robustness"] = robust
check("robustness_cases_unchanged_topology",
      sum(1 for v in robust.values() if not v["ok"] or v["verts_in"] != v["verts_out"]), 0)

# ---------------------------------------------------------------- 5. performance

import bmesh  # noqa: E402
bm = bmesh.new()
for i in range(30):
    for j in range(30):
        for k in range(15):
            ret = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.translate(bm, vec=(i * 2.0, j * 2.0, k * 2.0), verts=ret["verts"])
pm = bpy.data.meshes.new("perf")
bm.to_mesh(pm)
bm.free()
perf = bpy.data.objects.new("perf", pm)
bpy.context.scene.collection.objects.link(perf)
report["performance"] = []
for seg in (1, 4, 8):
    perf.modifiers.clear()
    A.add_cr_bevel(perf, ng, 0.1, seg)
    t0 = time.perf_counter()
    me = A.evaluate(perf)
    dt = time.perf_counter() - t0
    report["performance"].append(dict(segments=seg, in_faces=len(pm.polygons), out_faces=len(me.polygons), seconds=dt))
    print(f"PERF seg={seg} out_faces={len(me.polygons)} {dt:.2f}s")
perf.modifiers.clear()
bpy.data.objects.remove(perf)

# ---------------------------------------------------------------- write

report["failures"] = failures
with open(os.path.join(OUT, "phase1_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=float)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "phase1_tests.blend"), compress=True)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
