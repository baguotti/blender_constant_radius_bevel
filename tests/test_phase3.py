# SPDX-License-Identifier: CC0-1.0
"""Phase 3 tests: clamp, exact normals, bevel material, UVs, modifier stacking.

Run:  blender -b --factory-startup --python tests/test_phase3.py
Writes tests/results/phase3_results.json. Exit code is non-zero on any failure.
Shading renders are made separately by tests/render_phase3.py.
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
report = {"blender": bpy.app.version_string, "checks": [], "cases": {}}
failures = []


def check(name, value, limit):
    ok = bool(value <= limit)
    report["checks"].append(dict(name=name, value=float(value), limit=limit, ok=ok))
    print(("PASS " if ok else "FAIL ") + f"{name}: {value:.3e} (limit {limit:.0e})")
    if not ok:
        failures.append(name)


def cube(name, size=2.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def monkey():
    bpy.ops.mesh.primitive_monkey_add()
    return bpy.context.object


def run(ob, radius, segments=4, clamp="Uniform", angle=math.radians(30), **kw):
    mod = A.add_cr_bevel(ob, ng, radius, segments, angle_limit=angle, **kw)
    A.set_input(mod, "Clamp", clamp)
    me = A.evaluate(ob)
    return mod, me


# ---------------------------------------------------------------- 1. clamp

# Cube of size 2, all edges: the exact limit is r = 1 (strips from both sides meet),
# so Uniform must give 0.95 (CLAMP_SHARE) and still be exact at that radius.
for clamp in ("Uniform", "Per Edge"):
    ob = cube(f"cube_{clamp}")
    mod, me = run(ob, 1.5, clamp=clamp)
    msgs = [w.message for w in mod.node_warnings]
    bm = A.original_bmesh(ob)
    A.set_edge_selection(ob, lambda e: True)
    frames = A.edge_frames(bm, 0.95, A.selected_edges(ob))
    modes = A.classify_vertices(bm, frames, 0.95)
    stats, _ = A.exact_error(me, bm, frames, modes, 0.95)
    err = max(v[0] for v in stats.values() if v[1])
    report["cases"][f"cube_r1.5_{clamp}"] = dict(warnings=msgs, exact_error_at_0_95=err, inverted=A.inverted_faces(me, bm))
    check(f"cube r=1.5 {clamp}: exact at the clamped radius 0.95", err, 1e-5)
    check(f"cube r=1.5 {clamp}: warning reports 12 edges and 0.9500",
          0 if msgs and msgs[0].startswith("12 edge") and msgs[0].endswith("0.9500") else 1, 0)
    check(f"cube r=1.5 {clamp}: no inverted faces", A.inverted_faces(me, bm), 0)
    bm.free()

# Suzanne at r = 0.05 had 4 flipped faces in Phase 2 (no clamp). Overlap is a
# property of the native construction, so it is checked on the native result at
# the clamped radius; the exact result must then not flip any face against it.
for clamp in ("Uniform", "Per Edge", "None"):
    native_ob, exact_ob = monkey(), monkey()
    mod_n = A.add_cr_bevel(native_ob, ng, 0.05, 4, angle_limit=math.radians(30), exact=False)
    A.set_input(mod_n, "Analytic Normals", False)
    A.set_input(mod_n, "Clamp", clamp)
    mod, me = run(exact_ob, 0.05, clamp=clamp)
    me_n = A.evaluate(native_ob)
    bad = A.inverted_faces(me_n, A.original_bmesh(native_ob))
    flips = sum(A.count_flips(me, me_n))
    msgs = [w.message for w in mod.node_warnings]
    report["cases"][f"suzanne_r0.05_{clamp}"] = dict(native_inverted=bad, exact_vs_native_flips=flips, warnings=msgs)
    print(f"SUZANNE {clamp}: native inverted={bad} exact-vs-native flips={flips} warnings={msgs}")
    check(f"suzanne r=0.05 {clamp}: exact result flips no face against native", flips, 0)
    if clamp != "None":
        check(f"suzanne r=0.05 {clamp}: no inverted faces after clamping", bad, 0)
        check(f"suzanne r=0.05 {clamp}: warning shown", 0 if msgs else 1, 0)
    else:
        check("suzanne r=0.05 None: overlaps really happen without clamp (inverted faces > 0)", 0 if bad > 0 else 1, 0)

# Boolean-cut block: thin n-gons with reflex corners around the hole. Per Edge
# used to make 5-unit spikes here; no vertex may leave the original bounding box.


def boolean_block():
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(1, 1, 1))
    ob = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=3.0, vertices=48, location=(1.2, 1.1, 1.0),
                                        rotation=(math.radians(90), 0, math.radians(20)))
    cutter = bpy.context.object
    mod = ob.modifiers.new("cut", "BOOLEAN")
    mod.object, mod.solver = cutter, "EXACT"
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter)
    return ob


for clamp in ("Uniform", "Per Edge"):
    for radius in (0.3, 0.1):
        ob = boolean_block()
        pts = np.array([v.co[:] for v in ob.data.vertices])
        lo, hi = pts.min(0), pts.max(0)
        native = boolean_block()
        mod_n = A.add_cr_bevel(native, ng, radius, 8, angle_limit=math.radians(30), exact=False)
        A.set_input(mod_n, "Analytic Normals", False)
        A.set_input(mod_n, "Clamp", clamp)
        mod, me = run(ob, radius, segments=8, clamp=clamp)
        me_n = A.evaluate(native)
        P = np.array([v.co[:] for v in me.vertices])
        outside = float(np.maximum(P - hi, lo - P).max())
        flips = sum(A.count_flips(me, me_n))
        bad = A.inverted_faces(me_n, A.original_bmesh(native))
        report["cases"][f"boolean_block_r{radius}_{clamp}"] = dict(outside_bbox=outside, native_inverted=bad, flips=flips,
                                                                     warnings=[w.message for w in mod.node_warnings])
        check(f"boolean block r={radius} {clamp}: stays inside the original bounding box", outside, 1e-5)
        check(f"boolean block r={radius} {clamp}: no inverted faces, no flips vs native", bad + flips, 0)

# No warning when nothing needs clamping (cylinder rim, Phase 2 radius).
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=1.5)
ob = bpy.context.object
mod, me = run(ob, 0.15)
check("cylinder rim r=0.15: no clamp warning", len(mod.node_warnings), 0)

# ---------------------------------------------------------------- 2. exact normals


def skew_box():
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object("skew_box", v, f)


def notch_wedge():
    a = math.radians(60)
    return A.extrude_polygon("notch", [(0, 0), (3 * math.cos(a), 3 * math.sin(a)), (-6, 6), (-6, -6), (6, -6), (6, 0)])


R = 0.15
for name, make in (("cube", lambda: cube("cube_n")), ("skew_box", skew_box), ("concave_notch", notch_wedge)):
    ob = make()
    sharp = lambda e: len(e.link_faces) == 2 and e.link_faces[0].normal.angle(e.link_faces[1].normal) > math.radians(30)  # noqa: E731
    A.set_edge_selection(ob, sharp)
    mod, me = run(ob, R, segments=6, clamp="None", angle=0.0, selection_attr="sel")
    bm = A.original_bmesh(ob)
    frames = A.edge_frames(bm, R, A.selected_edges(ob))
    modes = A.classify_vertices(bm, frames, R)
    wb, wf, nb, nf = A.normal_errors(me, bm, frames, modes, R)
    report["cases"][f"normals_{name}"] = dict(bevel_max_rad=wb, flat_max_rad=wf, bevel_corners=nb, flat_corners=nf)
    check(f"normals {name}: bevel corners match the exact normal (rad, n={nb})", wb, 1e-4)
    # Limits: tangent-space custom normals are stored quantized (~5e-5 rad).
    check(f"normals {name}: original faces shade flat (rad, n={nf})", wf, 1e-4)
    check(f"normals {name}: has custom normals", 0 if me.has_custom_normals else 1, 0)
    bm.free()

ob = cube("cube_plain_normals")
mod = A.add_cr_bevel(ob, ng, R, 4)
A.set_input(mod, "Analytic Normals", False)
me = A.evaluate(ob)
check("Analytic Normals off: no custom normals written", 1 if me.has_custom_normals else 0, 0)

# Smooth-shaded input: original faces must keep smooth shading (not be flattened).
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=1.5)
ob = bpy.context.object
for poly in ob.data.polygons:
    poly.use_smooth = True
mod, me = run(ob, R, clamp="None")
flags = np.zeros(len(me.polygons), bool)
me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
# Side faces of the cylinder are flat quads; smooth shading means their corner
# normals tilt away from the quad's own normal (by up to half the 11.25 deg step).
deviation = max(A.angle_between(me.corner_normals[li].vector, p.normal)
                for p in me.polygons if not flags[p.index] and abs(p.normal.z) < 0.5 for li in p.loop_indices)
check("smooth input: cylinder side corners are smooth (differ from their flat face normal)", 0 if deviation > 0.01 else 1, 0)

# ---------------------------------------------------------------- 3. material, UVs, stacking

ob = cube("cube_mat")
base, bev_mat = bpy.data.materials.new("Base"), bpy.data.materials.new("Bevel")
ob.data.materials.append(base)
mod = A.add_cr_bevel(ob, ng, R, 4)
A.set_input(mod, "Set Bevel Material", True)
A.set_input(mod, "Bevel Material", bev_mat)
me = A.evaluate(ob)
flags = np.zeros(len(me.polygons), bool)
me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
mats = [me.materials[p.material_index].name if me.materials else None for p in me.polygons]
wrong = sum(1 for f, m in zip(flags, mats) if (m != "Bevel") == bool(f))
check("bevel material only on bevel faces", wrong, 0)

ob = cube("cube_uv")
uv = ob.data.uv_layers.new(name="UVMap")
for i, loop in enumerate(uv.data):
    loop.uv = ((i % 4) / 3.0, (i // 4 % 6) / 5.0)
A.add_cr_bevel(ob, ng, R, 4)
me = A.evaluate(ob)
uvs = me.uv_layers.get("UVMap")
arr = np.zeros(len(me.loops) * 2)
if uvs:
    uvs.data.foreach_get("uv", arr)
check("UV map kept and finite", 0 if (uvs and np.isfinite(arr).all()) else 1, 0)

ob = cube("cube_stack")
A.add_cr_bevel(ob, ng, R, 4)
twist = ob.modifiers.new("twist", "SIMPLE_DEFORM")
twist.deform_method, twist.angle = "TWIST", math.radians(30)
me = A.evaluate(ob)
check("deform modifier after CR Bevel evaluates (finite, custom normals kept)",
      0 if (np.isfinite(np.array([v.co[:] for v in me.vertices])).all() and me.has_custom_normals) else 1, 0)

report["failures"] = failures
with open(os.path.join(OUT, "phase3_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=float)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
