# SPDX-License-Identifier: CC0-1.0
"""Build library/cr_bevel_library.blend: the CR Bevel group, marked as a modifier asset.

Run:  blender -b --factory-startup --python src/make_library.py

To use it: Preferences > File Paths > Asset Libraries > add the repo's library/
folder. "CR Bevel" and "CR Bevel (Scene Units)" then appear under Add Modifier in any file.
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY = os.path.abspath(os.path.join(HERE, "..", "library", "cr_bevel_library.blend"))
sys.path.insert(0, HERE)
import build_cr_bevel  # noqa: E402


def build_library(path=LIBRARY):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for scene_units in (False, True):
        ng = build_cr_bevel.build(scene_units=scene_units)
        ng.use_fake_user = True
        ng.asset_mark()
        ng.asset_data.description = ng.description
        ng.asset_data.license = "CC0-1.0"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    backup = path + "1"
    if os.path.exists(backup):
        os.remove(backup)
    return path


if __name__ == "__main__":
    print("WROTE", build_library())
