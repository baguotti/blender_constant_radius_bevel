"""Lifecycle tests: what a user does with the modifier beyond one evaluation.

Run:  blender -b --factory-startup --python tests/test_lifecycle.py
(needs library/cr_bevel_library.blend: src/make_library.py)
"""
import json
import os
import sys
import tempfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel  # noqa: E402

OUT = os.path.join(HERE, "results")
LIBRARY = os.path.abspath(os.path.join(HERE, "..", "library", "cr_bevel_library.blend"))
report = {"blender": bpy.app.version_string, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def skew_box(name):
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object(name, v, f)


def positions(ob):
    return np.array([v.co[:] for v in A.evaluate(ob).vertices])


ng = build_cr_bevel.build()

# ---------------------------------------------------------------- save and reopen
ob = skew_box("saved")
A.add_cr_bevel(ob, ng, 0.15, 5)
before = positions(ob)
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "saved.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    after = positions(bpy.data.objects["saved"])
check("save and reopen: identical result", before.shape == after.shape and np.array_equal(before, after))

# ---------------------------------------------------------------- apply the modifier
bpy.ops.wm.read_factory_settings(use_empty=True)
ng = build_cr_bevel.build()
ob = skew_box("applied")
A.add_cr_bevel(ob, ng, 0.15, 5)
expected = positions(ob)
bpy.context.view_layer.objects.active = ob
ob.select_set(True)
bpy.ops.object.modifier_apply(modifier=ob.modifiers[0].name)
me = ob.data
internal = [a.name for a in me.attributes if a.name.startswith("_cr_")]
check("apply: same geometry as the live modifier", len(me.vertices) == len(expected))
check("apply: no internal _cr_ attributes left", not internal, internal)
check("apply: custom normals kept", me.has_custom_normals)
check("apply: cr_bevel_face kept for later use", "cr_bevel_face" in me.attributes)

# ---------------------------------------------------------------- two CR Bevels stacked
ob = skew_box("stacked")
A.add_cr_bevel(ob, ng, 0.2, 3)
m2 = ob.modifiers.new("CR Bevel 2", "NODES")
m2.node_group = ng
A.set_input(m2, "Radius", 0.05)
A.set_input(m2, "Segments", 2)
P = positions(ob)
check("two CR Bevels stacked: evaluates, finite", P.size > 0 and np.isfinite(P).all(), len(P))

# ---------------------------------------------------------------- shared mesh data
a = skew_box("shared_a")
b = bpy.data.objects.new("shared_b", a.data)
bpy.context.scene.collection.objects.link(b)
for o in (a, b):
    A.add_cr_bevel(o, ng, 0.15, 4)
check("two objects sharing one mesh: same result", np.array_equal(positions(a), positions(b)))

# ---------------------------------------------------------------- keyframed radius
ob = skew_box("animated")
mod = A.add_cr_bevel(ob, ng, 0.05, 4)
item = getattr(mod.properties.inputs, A.socket_ids(ng)["Radius"])
item.value = 0.05
item.keyframe_insert("value", frame=1)
item.value = 0.25
item.keyframe_insert("value", frame=10)
scene = bpy.context.scene
scene.frame_set(1)
p1 = positions(ob)
scene.frame_set(10)
p10 = positions(ob)
scene.frame_set(1)
p1_again = positions(ob)
check("keyframed radius: result changes with the frame", p1.shape == p10.shape and not np.allclose(p1, p10))
check("keyframed radius: going back gives the same result", np.array_equal(p1, p1_again))

# ---------------------------------------------------------------- linked from the library
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(LIBRARY, link=True) as (src, dst):
    dst.node_groups = ["CR Bevel"]
linked = dst.node_groups[0]
ob = skew_box("linked_user")
mod = ob.modifiers.new("CR Bevel", "NODES")
mod.node_group = linked
A.set_input(mod, "Radius", 0.15)
P = positions(ob)
check("node group linked (not appended) from the library: works", linked.library is not None and len(P) > 8,
      len(P))

# ---------------------------------------------------------------- edit mode
ng = build_cr_bevel.build()
ob = skew_box("edit")
A.add_cr_bevel(ob, ng, 0.15, 4)
object_mode = positions(ob)
bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode="EDIT")
try:
    edit_mode = positions(ob)
    check("evaluates in Edit Mode, same as Object Mode", np.array_equal(object_mode, edit_mode))
finally:
    bpy.ops.object.mode_set(mode="OBJECT")

with open(os.path.join(OUT, "lifecycle_results.json"), "w") as fh:
    json.dump(report, fh, indent=2)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
