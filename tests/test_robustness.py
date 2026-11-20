"""Robustness tests: awkward inputs the phase tests don't cover.

Run:  blender -b --factory-startup --python tests/test_robustness.py
Writes tests/results/robustness_results.json. Exit code is non-zero on failure.

For every case: no crash, no NaN/inf, same topology as the native bevel (Exact
Profile off) and no face flipped against it. Where a case should stay exact,
the exact error is checked too.
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel  # noqa: E402

OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "cases": {}, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def from_bm(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def skew_box(name, scale=1.0, offset=(0.0, 0.0, 0.0)):
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    v = [tuple(c * scale + o for c, o in zip(p, offset)) for p in v]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object(name, v, f)


def cube_bm(size=2.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    return bm


def run_case(name, make, radius, segments=4, clamp="Uniform", angle=math.radians(30), exact_r=None,
             selection=None, prepare=None, prepare_native=True):
    """prepare_native=False: the native reference skips `prepare`; its vertex order
    then differs, so orientation is checked against the exact normals instead."""
    """Evaluate exact and native on two copies; return the exact mesh and stats."""
    obs = []
    for exact in (True, False):
        ob = make()
        if prepare and (exact or prepare_native):
            prepare(ob)
        if selection:
            A.set_edge_selection(ob, selection)
        mod = A.add_cr_bevel(ob, ng, radius, segments, angle_limit=angle, exact=exact,
                             selection_attr="sel" if selection else None)
        A.set_input(mod, "Clamp", clamp)
        if not exact:
            A.set_input(mod, "Analytic Normals", False)
        obs.append((ob, mod))
    try:
        me, me_n = A.evaluate(obs[0][0]), A.evaluate(obs[1][0])
    except Exception as exc:  # pragma: no cover
        check(f"{name}: evaluates", False, exc)
        return None
    P = np.array([v.co[:] for v in me.vertices]) if len(me.vertices) else np.zeros((0, 3))
    same = len(me.vertices) == len(me_n.vertices) and len(me.polygons) == len(me_n.polygons)
    if prepare_native or not prepare:
        flips = sum(A.count_flips(me, me_n)) if same else -1
    else:
        flips = A.inverted_faces(me, A.original_bmesh(obs[0][0]))
    stats = dict(verts=len(me.vertices), faces=len(me.polygons), finite=bool(np.isfinite(P).all()),
                 same_topology=same, flips=flips, warnings=[w.message for w in obs[0][1].node_warnings])
    check(f"{name}: finite, same topology as native, no flips", stats["finite"] and same and flips == 0, stats)
    if exact_r is not None:
        ob = obs[0][0]
        bm = A.original_bmesh(ob)
        sel = [e.index for e in bm.edges if len(e.link_faces) == 2 and
               e.link_faces[0].normal.angle(e.link_faces[1].normal) > angle] if not selection else A.selected_edges(ob)
        frames = A.edge_frames(bm, exact_r, sel)
        modes = A.classify_vertices(bm, frames, exact_r)
        errs, fb = A.exact_error(me, bm, frames, modes, exact_r)
        worst = max((v[0] for k, v in errs.items() if v[1] and k != "joint_seam_end"), default=0.0)
        stats["exact_error"] = worst
        stats["mode_counts"] = {m: sum(1 for i in modes.values() if A.MODE_NAMES[i["mode"]] == m) for m in A.MODE_NAMES}
        coord = float(np.abs(np.array([v.co[:] for v in ob.data.vertices])).max())
        tol = max(1e-4, 16 * np.spacing(np.float32(coord)) / exact_r)   # float32 limit far from origin
        check(f"{name}: exact (error {worst:.1e} r, tolerance {tol:.0e})", worst < tol, stats["mode_counts"])
        bm.free()
    report["cases"][name] = stats
    return me, stats


# ---------------------------------------------------------------- scale and position

_, base = run_case("skew box at origin", lambda: skew_box("sb0"), 0.15, exact_r=0.15)
_, far = run_case("skew box 1000 units from origin", lambda: skew_box("sb1", offset=(1000, 1000, 1000)), 0.15,
                  exact_r=0.15)
check("far from origin: same vertex modes as at origin", far.get("mode_counts") == base.get("mode_counts"),
      (far.get("mode_counts"), base.get("mode_counts")))
run_case("skew box scaled 0.001", lambda: skew_box("sbs", scale=0.001), 0.00015, exact_r=0.00015)
run_case("skew box scaled 1000", lambda: skew_box("sbl", scale=1000.0), 150.0, exact_r=150.0)

# ---------------------------------------------------------------- broken input normals


def flip_one_face(ob):
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
    bm.to_mesh(me)
    bm.free()


def flip_all_faces(ob):
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()


def same_points(me_a, me_b):
    """Largest distance from a vertex of one mesh to the nearest vertex of the other."""
    a = np.array([v.co[:] for v in me_a.vertices])
    b = np.array([v.co[:] for v in me_b.vertices])
    if not len(a) or not len(b):
        return float("inf")
    d_ab = max(np.linalg.norm(b - p, axis=1).min() for p in a)
    d_ba = max(np.linalg.norm(a - p, axis=1).min() for p in b)
    return float(max(d_ab, d_ba))


good, _ = run_case("skew box, consistent normals (reference)", lambda: skew_box("sbgood"), 0.15)
for label, prep in (("one face flipped", flip_one_face), ("inside out", flip_all_faces)):
    me, _ = run_case(f"skew box, {label}", lambda: skew_box("sbflip"), 0.15, prepare=prep)
    if me is not None:
        dev = same_points(me, good)
        report["cases"][f"skew box, {label}"]["deviation_from_consistent"] = dev
        if label == "inside out":   # consistently flipped: still a valid solid, same result
            check(f"skew box, {label}: same shape as with consistent normals (max dev {dev:.1e})", dev < 1e-5)
        else:                       # inconsistent: its edges are skipped and a warning explains why
            warns = report["cases"][f"skew box, {label}"]["warnings"]
            check(f"skew box, {label}: warns about flipped faces", any("wrong way" in w for w in warns), warns)

# ---------------------------------------------------------------- topology


def triangulated_cube():
    bm = cube_bm()
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    return from_bm("tri_cube", bm)


def open_box():
    bm = cube_bm()
    bm.faces.ensure_lookup_table()
    top = [f for f in bm.faces if f.normal.z > 0.9]
    bmesh.ops.delete(bm, geom=top, context="FACES_ONLY")
    return from_bm("open_box", bm)


def cube_with_duplicate_vertex():
    bm = cube_bm()
    bm.verts.ensure_lookup_table()
    corner = bm.verts[0].co.copy()
    edge = bm.verts[0].link_edges[0]
    ret = bmesh.ops.subdivide_edges(bm, edges=[edge], cuts=1)
    new = [g for g in ret["geom_inner"] if isinstance(g, bmesh.types.BMVert)][0]
    new.co = corner             # zero-length edge between the corner and the new vertex
    return from_bm("dup_vert", bm)


def cube_with_loose_parts():
    bm = cube_bm()
    a = bm.verts.new((5, 0, 0))
    b = bm.verts.new((6, 0, 0))
    bm.edges.new((a, b))
    bm.verts.new((7, 7, 7))
    return from_bm("loose", bm)


run_case("triangulated cube", triangulated_cube, 0.15)
run_case("open box (boundary edges)", open_box, 0.15)
run_case("cube with a zero-length edge", cube_with_duplicate_vertex, 0.15)
run_case("cube with loose vertex and edge", cube_with_loose_parts, 0.15, exact_r=0.15)

# ---------------------------------------------------------------- angles


def sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    return bpy.context.object


def knife_wedge(deg):
    a = math.radians(deg)          # interior angle -> phi = 180 - deg
    pts = [(0.0, 0.0), (4.0, 0.0), (4 * math.cos(a), 4 * math.sin(a))]
    return A.extrude_polygon("knife", pts)


run_case("UV sphere, Angle Limit 0", sphere, 0.02, angle=0.0)
for deg in (2, 3, 5):   # 2 deg is past MAX_EDGE_ANGLE (skipped); 3 and 5 are beveled
    run_case(f"knife edge ({deg} deg between faces)", lambda: knife_wedge(deg), 0.05, segments=16)

# ---------------------------------------------------------------- segments and selection

for seg in (1, 64):
    run_case(f"skew box, {seg} segments", lambda: skew_box(f"sbseg{seg}"), 0.15, segments=seg, exact_r=0.15)
run_case("skew box, partial selection, Per Edge", lambda: skew_box("sbsel"), 0.15, clamp="Per Edge",
         selection=lambda e: any(v.co.z > 1.0 for v in e.verts), exact_r=0.15)

# ---------------------------------------------------------------- inherited data


def with_custom_normals(ob):
    ob.data.normals_split_custom_set_from_vertices([(0.0, 0.0, 1.0)] * len(ob.data.vertices))


def with_cr_attribute(ob):
    a = ob.data.attributes.new("cr_bevel_face", "BOOLEAN", "FACE")
    for d in a.data:
        d.value = True


# The native node bends its profile according to custom normals, so the native
# reference is made without them (CR Bevel itself must be exact either way).
run_case("input already has custom normals", lambda: skew_box("sbcn"), 0.15, prepare=with_custom_normals,
         prepare_native=False, exact_r=0.15)
me, _ = run_case("input already has a cr_bevel_face attribute", lambda: skew_box("sbattr"), 0.15,
                 prepare=with_cr_attribute, exact_r=0.15)
if me is not None:
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    check("cr_bevel_face is recomputed (not inherited)", 0 < flags.sum() < len(flags), int(flags.sum()))

# Smooth/flat flags and sharp edges of the input survive (with Analytic Normals
# off they are all that decides shading).
bpy.ops.mesh.primitive_cylinder_add(vertices=16)
ob = bpy.context.object
for poly in ob.data.polygons:
    poly.use_smooth = abs(poly.normal.z) < 0.5      # smooth sides, flat caps
mod = A.add_cr_bevel(ob, ng, 0.1, 4)
A.set_input(mod, "Analytic Normals", False)
me = A.evaluate(ob)
flags = np.zeros(len(me.polygons), bool)
me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
orig = [p for p in me.polygons if not flags[p.index]]
check("input smooth/flat face flags survive",
      all(p.use_smooth == (abs(p.normal.z) < 0.5) for p in orig), len(orig))

report["failures"] = failures
with open(os.path.join(OUT, "robustness_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=str)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
