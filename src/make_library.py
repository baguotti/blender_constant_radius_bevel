"""Build library/cr_bevel_library.blend: the CR Bevel group, marked as a modifier asset.

Run:  blender -b --factory-startup --python src/make_library.py

To use it: Preferences > File Paths > Asset Libraries > add the repo's library/
folder. "CR Bevel" then appears under Add Modifier in any file.
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_cr_bevel  # noqa: E402

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
ng = build_cr_bevel.build()
ng.use_fake_user = True
ng.asset_mark()
ng.asset_data.description = ng.description
ng.asset_data.author = "blender_constant_radius_bevel"
out_dir = os.path.join(HERE, "..", "library")
os.makedirs(out_dir, exist_ok=True)
path = os.path.abspath(os.path.join(out_dir, "cr_bevel_library.blend"))
bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
print("WROTE", path)
