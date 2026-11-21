# SPDX-License-Identifier: CC0-1.0
"""Phase 4 tests: the installable extension.

Run (after src/make_extension.py):
    blender -b --factory-startup --python tests/test_phase4.py
Installs dist/cr_bevel-*.zip into a temporary Blender user folder (your real
configuration is not touched), then checks it in a fresh Blender process.
Exit code is non-zero on any failure.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
BLENDER = bpy.app.binary_path

CHECK_SCRIPT = r'''
import json, bpy
res = {}
mod_name = "bl_ext.user_default.cr_bevel"
res["enabled"] = mod_name in bpy.context.preferences.addons
menu = getattr(bpy.types, "OBJECT_MT_modifier_add_generate", None)
funcs = menu._dyn_ui_initialize() if menu else []
res["menu_entry"] = any(getattr(f, "__name__", "") == "menu_entry" for f in funcs)
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
bpy.ops.mesh.primitive_cube_add()
a = bpy.context.object
res["operator"] = list(bpy.ops.object.cr_bevel_add())
bpy.ops.mesh.primitive_cylinder_add(location=(4, 0, 0))
b = bpy.context.object
bpy.ops.object.cr_bevel_add()
res["groups_named_cr_bevel"] = len([g for g in bpy.data.node_groups if g.name.startswith("CR Bevel")])
bpy.ops.mesh.primitive_cube_add(location=(8, 0, 0))
c = bpy.context.object
c.scale = (1.0, 2.0, 0.5)
res["scene_operator"] = list(bpy.ops.object.cr_bevel_add(scene_units=True))
res["groups"] = sorted(g.name for g in bpy.data.node_groups if g.name.startswith("CR Bevel"))
dg = bpy.context.evaluated_depsgraph_get()
for key, ob in (("cube", a), ("cylinder", b), ("scaled_cube", c)):
    me = ob.evaluated_get(dg).to_mesh()
    flags = [d.value for d in me.attributes["cr_bevel_face"].data] if "cr_bevel_face" in me.attributes else []
    res[key] = dict(modifier=ob.modifiers[-1].node_group.name if ob.modifiers else None,
                    bevel_faces=sum(flags), verts=len(me.vertices))
print("RESULT " + json.dumps(res))
'''

report = {"blender": bpy.app.version_string, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=detail))
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


zips = sorted(glob.glob(os.path.join(ROOT, "dist", "cr_bevel-*.zip")))
check("extension zip exists (run src/make_extension.py first)", bool(zips))
if zips:
    with tempfile.TemporaryDirectory() as user:
        env = dict(os.environ, BLENDER_USER_RESOURCES=user)
        inst = subprocess.run([BLENDER, "--factory-startup", "--command", "extension", "install-file",
                               "-r", "user_default", "-e", zips[-1]], env=env, capture_output=True, text=True)
        check("installs with `extension install-file`", inst.returncode == 0, inst.stderr.strip()[-200:])
        script = os.path.join(user, "check.py")
        with open(script, "w") as fh:
            fh.write(CHECK_SCRIPT)
        run = subprocess.run([BLENDER, "-b", "--python", script], env=env, capture_output=True, text=True)
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        res = json.loads(line[7:]) if line else {}
        report["result"] = res
        check("add-on is enabled after install", res.get("enabled"))
        check("menu entry in Add Modifier > Generate", res.get("menu_entry"))
        check("operator finishes", res.get("operator") == ["FINISHED"], str(res.get("operator")))
        check("cube gets a working CR Bevel", (res.get("cube") or {}).get("bevel_faces", 0) > 0, str(res.get("cube")))
        check("cylinder gets a working CR Bevel", (res.get("cylinder") or {}).get("bevel_faces", 0) > 0,
              str(res.get("cylinder")))
        check("second use reuses the node group (no duplicates)", res.get("groups_named_cr_bevel") == 1,
              str(res.get("groups_named_cr_bevel")))
        check("Scene Units entry: its own group, works on a non-uniformly scaled cube",
              res.get("scene_operator") == ["FINISHED"]
              and res.get("groups") == ["CR Bevel", "CR Bevel (Scene Units)"]
              and (res.get("scaled_cube") or {}).get("modifier") == "CR Bevel (Scene Units)"
              and (res.get("scaled_cube") or {}).get("bevel_faces", 0) > 0, str((res.get("groups"), res.get("scaled_cube"))))
        if not line:
            print(run.stdout[-2000:], run.stderr[-2000:])

report["failures"] = failures
with open(os.path.join(OUT, "phase4_results.json"), "w") as fh:
    json.dump(report, fh, indent=2)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
