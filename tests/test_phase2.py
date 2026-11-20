"""Phase 2 tests: corners, chain joints, straight continuations, slanted end caps.

Run:  blender -b --factory-startup --python tests/test_phase2.py
Writes tests/results/phase2_results.json and tests/results/phase2_tests.blend.
Exit code is non-zero if any check fails.

Every case is evaluated twice, with Exact Profile on and off. The "off" run is
the native Mesh Bevel result on the same topology, which lets us check that
fallback geometry is left untouched and that no face flips.
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

R = 0.15
SEG = 6
TOL = 1e-5
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "radius": R, "segments": SEG, "tolerance": TOL, "cases": {}, "checks": []}
failures = []


def check(name, value, limit):
    ok = bool(value <= limit)
    report["checks"].append(dict(name=name, value=float(value), limit=limit, ok=ok))
    print(("PASS " if ok else "FAIL ") + f"{name}: {value:.3e} (limit {limit:.0e})")
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- models


def obj_from_bm(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def skew_box():
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object("skew_box", v, f)


def tapered_prism(name):
    base = [(0, 0), (3, 0), (3.5, 2), (1, 3), (-0.5, 1.5)]
    cx, cy = 1.4, 1.3
    top = [(cx + 0.6 * (x - cx), cy + 0.6 * (y - cy)) for x, y in base]
    n = len(base)
    v = [(x, y, 0) for x, y in base] + [(x, y, 1.2) for x, y in top]
    f = [list(range(n))[::-1], list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return A.new_object(name, v, f)


def oblique_wedge():
    pts = [(0.0, 0.0), (2.0, 0.0), (0.6, 1.9)]
    v = [(x, y, 0) for x, y in pts] + [(x, y, 1.0 + 0.35 * x - 0.2 * y) for x, y in pts]
    f = [[2, 1, 0], [3, 4, 5], [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5]]
    return A.new_object("oblique_wedge", v, f)


def subdivided_cube():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=2, use_grid_fill=True)
    return obj_from_bm("subdivided_cube", bm)


def notch_cube():
    """2x2x2 block of unit cubes with one corner cube removed (inside corner)."""
    bm = bmesh.new()
    for x in (0, 1):
        for y in (0, 1):
            for z in (0, 1):
                if (x, y, z) == (1, 1, 1):
                    continue
                ret = bmesh.ops.create_cube(bm, size=1.0)
                bmesh.ops.translate(bm, vec=(x + 0.5, y + 0.5, z + 0.5), verts=ret["verts"])
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
    seen = {}
    for f in bm.faces:
        seen.setdefault(frozenset(v.index for v in f.verts), []).append(f)
    bmesh.ops.delete(bm, geom=[f for fs in seen.values() if len(fs) > 1 for f in fs], context="FACES_ONLY")
    bmesh.ops.dissolve_limit(bm, angle_limit=1e-3, verts=bm.verts[:], edges=bm.edges[:])
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_edges], context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return obj_from_bm("notch_cube", bm)


def l_prism():
    pts = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    return A.extrude_polygon("L_prism", pts)


def pyramid5():
    v = [(math.cos(2 * math.pi * i / 5), math.sin(2 * math.pi * i / 5), 0) for i in range(5)] + [(0, 0, 1.2)]
    f = [[4, 3, 2, 1, 0]] + [[i, (i + 1) % 5, 5] for i in range(5)]
    return A.new_object("pyramid5", v, f)


def cylinder(n):
    bpy.ops.mesh.primitive_cylinder_add(vertices=n, radius=1.0, depth=1.5)
    return bpy.context.object


def sharp(e):
    return len(e.link_faces) == 2 and e.link_faces[0].normal.angle(e.link_faces[1].normal) > math.radians(30)


CASES = [
    # name, builder, edge selection, modes that MUST appear
    ("cube_all_edges", lambda: A.new_object("cube", [(x, y, z) for z in (0, 2) for y in (0, 2) for x in (0, 2)],
                                            [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [1, 3, 7, 5], [3, 2, 6, 7], [2, 0, 4, 6]]),
     sharp, {"corner"}),
    ("skew_box_all_edges", skew_box, sharp, {"corner"}),
    ("tapered_prism_top_loop", lambda: tapered_prism("taper_top"),
     lambda e: sharp(e) and all(v.co.z > 1.0 for v in e.verts), {"joint"}),
    ("tapered_prism_top_and_sides", lambda: tapered_prism("taper_top_sides"),
     lambda e: sharp(e) and not all(v.co.z < 0.1 for v in e.verts), {"corner", "terminal"}),
    ("oblique_end_cap", oblique_wedge,
     lambda e: all(abs(v.co.x) < 1e-9 and abs(v.co.y) < 1e-9 for v in e.verts), {"terminal"}),
    ("subdivided_cube_pipes", subdivided_cube, sharp, {"corner", "pipe"}),
    ("notch_cube_concave_corner", notch_cube, sharp, {"corner"}),
    ("cylinder_rim_joints", lambda: cylinder(32), lambda e: sharp(e) and all(v.co.z > 0 for v in e.verts), {"joint"}),
    ("cylinder12_all_sharp_edges", lambda: cylinder(12), sharp, {"joint"}),
    ("L_prism_mixed_corners", l_prism, sharp, set()),
    ("pyramid5_apex", pyramid5, sharp, {"corner"}),
]

all_worst = 0.0
for name, make, sel_fn, must in CASES:
    ob_on, ob_off = make(), make()
    for ob in (ob_on, ob_off):
        A.set_edge_selection(ob, sel_fn)
    A.add_cr_bevel(ob_on, ng, R, SEG, selection_attr="sel")
    A.add_cr_bevel(ob_off, ng, R, SEG, selection_attr="sel", exact=False)
    me_on, me_off = A.evaluate(ob_on), A.evaluate(ob_off)
    bm = A.original_bmesh(ob_on)
    frames = A.edge_frames(bm, R, A.selected_edges(ob_on))
    modes = A.classify_vertices(bm, frames, R)
    stats, fallback_out = A.exact_error(me_on, bm, frames, modes, R)

    on = np.array([v.co[:] for v in me_on.vertices])
    off = np.array([v.co[:] for v in me_off.vertices])
    same_topology = len(on) == len(off) and len(me_on.polygons) == len(me_off.polygons)
    moved = np.linalg.norm(on - off, axis=1) if same_topology else np.array([np.inf])
    fallback_moved = float(moved[fallback_out].max()) if fallback_out and same_topology else 0.0
    flips = sum(1 for a, b in zip(me_on.polygons, me_off.polygons) if a.normal.dot(b.normal) < 0) if same_topology else -1
    finite = bool(np.isfinite(on).all())
    present = {A.MODE_NAMES[i["mode"]] for i in modes.values()}
    mode_counts = {m: sum(1 for i in modes.values() if A.MODE_NAMES[i["mode"]] == m) for m in A.MODE_NAMES}

    worst = max(v[0] for k, v in stats.items() if k != "joint_seam_end")
    all_worst = max(all_worst, worst)
    report["cases"][name] = dict(
        mode_counts=mode_counts, errors={k: dict(max=v[0], n=v[1]) for k, v in stats.items() if v[1]},
        fallback_vertices_out=len(fallback_out), fallback_moved=fallback_moved,
        max_move_vs_native_over_r=float(moved.max() / R), flipped_faces=flips,
        verts=len(on), faces=len(me_on.polygons), finite=finite,
    )
    print(f"CASE {name}: modes={mode_counts} errors=" +
          ", ".join(f"{k}={v[0]:.1e}(n={v[1]})" for k, v in stats.items() if v[1]))
    check(f"{name}: exact error", worst, TOL)
    if "joint_seam_end" in stats:   # known limitation: reported, bounded loosely
        check(f"{name}: joint seam end (native, known limit) below 0.1 r", stats["joint_seam_end"][0], 0.1)
    check(f"{name}: expected modes present", len(must - present), 0)
    check(f"{name}: fallback vertices untouched", fallback_moved, 1e-7)
    check(f"{name}: same topology as native", 0 if same_topology else 1, 0)
    check(f"{name}: no flipped faces", flips if flips >= 0 else 1, 0)
    check(f"{name}: no NaN/inf", 0 if finite else 1, 0)
    check(f"{name}: move vs native below 0.4 r", float(moved.max() / R), 0.4)
    bm.free()

# ---------------------------------------------------------------- smoke tests on messy meshes


def boolean_block():
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    block = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=3.0, vertices=24, location=(0.3, 0.2, 0))
    cutter = bpy.context.object
    mod = block.modifiers.new("cut", "BOOLEAN")
    mod.object, mod.solver = cutter, "EXACT"
    bpy.context.view_layer.objects.active = block
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter)
    return block


def monkey():
    bpy.ops.mesh.primitive_monkey_add()
    return bpy.context.object


smoke = {}
# Suzanne uses a small radius: at larger ones its bevels overlap (no clamp until Phase 3).
for name, make, radius in (("boolean_block", boolean_block, 0.05), ("suzanne", monkey, 0.005)):
    ob_on, ob_off = make(), make()
    A.add_cr_bevel(ob_on, ng, radius, 4, angle_limit=math.radians(30))
    A.add_cr_bevel(ob_off, ng, radius, 4, angle_limit=math.radians(30), exact=False)
    me_on, me_off = A.evaluate(ob_on), A.evaluate(ob_off)
    on = np.array([v.co[:] for v in me_on.vertices])
    same = len(me_on.vertices) == len(me_off.vertices) and len(me_on.polygons) == len(me_off.polygons)
    flips = sum(1 for a, b in zip(me_on.polygons, me_off.polygons) if a.normal.dot(b.normal) < 0) if same else -1
    smoke[name] = dict(verts=len(on), finite=bool(np.isfinite(on).all()), same_topology=same, flipped_faces=flips)
    print("SMOKE", name, smoke[name])
    check(f"smoke {name}: finite, same topology, no flips",
          0 if (smoke[name]["finite"] and same and flips == 0) else 1, 0)
report["smoke"] = smoke

# ---------------------------------------------------------------- GN classifier agrees with Python


def gn_modes(ob):
    """Run the group with debug_output='skeleton' to read the GN vertex modes."""
    g = build_cr_bevel.build("CR Bevel debug", debug_output="skeleton")
    m = ob.modifiers.new("dbg", "NODES")
    m.node_group = g
    A.set_input(m, "Radius", R)
    A.set_input(m, "Angle Limit", 0.0)
    A.set_input(m, "Selection", attribute="sel")
    me = A.evaluate(ob)
    vid = np.zeros(len(me.vertices), np.int32)
    md = np.zeros(len(me.vertices), np.int32)
    me.attributes["_cr_vid"].data.foreach_get("value", vid)
    me.attributes["_cr_mode"].data.foreach_get("value", md)
    ob.modifiers.remove(m)
    return dict(zip(vid.tolist(), md.tolist()))


disagree = 0
for name, make, sel_fn, _ in CASES:
    ob = make()
    A.set_edge_selection(ob, sel_fn)
    bm = A.original_bmesh(ob)
    py = A.classify_vertices(bm, A.edge_frames(bm, R, A.selected_edges(ob)), R)
    gn = gn_modes(ob)
    bad = {v: (py[v]["mode"], gn.get(v)) for v in py if gn.get(v) != py[v]["mode"]}
    if bad:
        print("MODE MISMATCH", name, bad)
    disagree += len(bad)
    bm.free()
check("GN vertex modes match the Python reference", disagree, 0)

report["worst_exact_error"] = all_worst
report["failures"] = failures
with open(os.path.join(OUT, "phase2_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=float)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "phase2_tests.blend"), compress=True)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
