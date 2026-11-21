# SPDX-License-Identifier: CC0-1.0
"""Shading renders: native Mesh Bevel (top row) vs CR Bevel (bottom row).

Run:  blender -b --factory-startup --python tests/render_phase3.py
Writes tests/results/phase3_zebra.png and tests/results/phase3_closeup.png.

Both rows use the same normal handling (flat original faces, smooth bevel),
so the only difference is the bevel geometry and its exact normals. A glossy
material reflects a striped environment ("zebra" test): stripes stay smooth
and evenly spaced on a true arc and wobble where the curvature is uneven.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel  # noqa: E402

OUT = os.path.join(HERE, "results")
RADIUS, SEGMENTS = 0.3, 8

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
ng = build_cr_bevel.build()

# ---------------------------------------------------------------- materials and world
mat = bpy.data.materials.new("Zebra")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.05, 0.05, 0.05, 1)
bsdf.inputs["Metallic"].default_value = 1.0
bsdf.inputs["Roughness"].default_value = 0.04

world = bpy.data.worlds.new("Stripes")
bpy.context.scene.world = world
world.use_nodes = True
wn, wl = world.node_tree.nodes, world.node_tree.links
coord = wn.new("ShaderNodeTexCoord")
wave = wn.new("ShaderNodeTexWave")
wave.wave_type, wave.bands_direction = "BANDS", "Z"
wave.inputs["Scale"].default_value = 2.5
wave.inputs["Distortion"].default_value = 0.0
ramp = wn.new("ShaderNodeValToRGB")
ramp.color_ramp.interpolation = "CONSTANT"
ramp.color_ramp.elements[1].position = 0.5
bg = wn["Background"]
wl.new(coord.outputs["Generated"], wave.inputs["Vector"])
wl.new(wave.outputs["Fac"], ramp.inputs["Fac"])
wl.new(ramp.outputs["Color"], bg.inputs["Color"])
bg.inputs["Strength"].default_value = 1.2

# ---------------------------------------------------------------- shapes


def skew_box():
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object("skew_box", v, f)


def tapered():
    base = [(0, 0), (3, 0), (3.5, 2), (1, 3), (-0.5, 1.5)]
    top = [(1.4 + 0.6 * (x - 1.4), 1.3 + 0.6 * (y - 1.3)) for x, y in base]
    v = [(x * 0.7, y * 0.7, 0) for x, y in base] + [(x * 0.7, y * 0.7, 1.4) for x, y in top]
    f = [[4, 3, 2, 1, 0], [5, 6, 7, 8, 9]] + [[i, (i + 1) % 5, 5 + (i + 1) % 5, 5 + i] for i in range(5)]
    return A.new_object("tapered", v, f)


def cylinder():
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1.0, depth=1.6, location=(1.0, 1.0, 0.8))
    ob = bpy.context.object
    for poly in ob.data.polygons:      # smooth-shaded sides, as a user would set them
        poly.use_smooth = abs(poly.normal.z) < 0.5
    return ob


def block():
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


SHAPES = (skew_box, tapered, cylinder, block)
for col, make in enumerate(SHAPES):
    for row, exact in enumerate((False, True)):
        ob = make()
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.translate(bm, vec=(-1.0, -1.0, 0.0), verts=bm.verts[:])
        bm.to_mesh(ob.data)
        bm.free()
        ob.location = (col * 3.4 - 5.1, 0.0, (1 - row) * 2.6)
        ob.rotation_euler = (0, 0, math.radians(25))
        ob.data.materials.clear()
        ob.data.materials.append(mat)
        mod = A.add_cr_bevel(ob, ng, RADIUS, SEGMENTS, angle_limit=math.radians(30), exact=exact)
        A.set_input(mod, "Clamp", "Per Edge")
        A.set_input(mod, "Analytic Normals", True)   # off-exact => smooth bevel, flat faces

# ---------------------------------------------------------------- camera, render


def camera(name, loc, target, lens):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    bpy.context.scene.collection.objects.link(cam)
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens
    return cam


scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 24
scene.cycles.use_denoising = False
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"

scene.camera = camera("wide", (0.0, -14.0, 6.5), (0.0, 0.0, 1.6), 38)
scene.render.resolution_x, scene.render.resolution_y = 1800, 900
scene.render.filepath = os.path.join(OUT, "phase3_zebra.png")
bpy.ops.render.render(write_still=True)

# Close-up of the skew box corners (acute and obtuse edges), both rows.
scene.camera = camera("close", (-5.6, -5.0, 3.6), (-5.0, 0.0, 1.6), 55)
scene.render.resolution_x, scene.render.resolution_y = 900, 900
scene.render.filepath = os.path.join(OUT, "phase3_closeup.png")
bpy.ops.render.render(write_still=True)
print("RENDERED")
