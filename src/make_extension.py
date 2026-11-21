# SPDX-License-Identifier: CC0-1.0
"""Build the installable extension: dist/cr_bevel-<version>.zip

Run:  blender -b --factory-startup --python src/make_extension.py
Rebuilds library/cr_bevel_library.blend, copies it into extension/, then runs
Blender's own `extension validate` and `extension build`.
"""
import os
import shutil
import subprocess
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
EXT = os.path.join(ROOT, "extension")
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, HERE)
from make_library import build_library  # noqa: E402

shutil.copy(build_library(), os.path.join(EXT, "cr_bevel_library.blend"))
os.makedirs(DIST, exist_ok=True)
for cmd in (["validate", EXT], ["build", "--source-dir", EXT, "--output-dir", DIST]):
    out = subprocess.run([bpy.app.binary_path, "--factory-startup", "--command", "extension", *cmd],
                         capture_output=True, text=True)
    print(out.stdout.strip(), out.stderr.strip())
    if out.returncode:
        sys.exit(out.returncode)
print("EXTENSION BUILT")
