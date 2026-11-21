# SPDX-License-Identifier: CC0-1.0
"""Performance profile of CR Bevel: total time per configuration and time per stage.

Run:  blender -b --factory-startup --python tests/profile_cr_bevel.py [-- quick]
Writes tests/results/profile.json. Timings are the best of REPEAT runs (shared
machines vary by about +-25% between runs).

Stage times come from cutting the node group after each stage (the stored
attributes force everything before the cut to be computed), so each figure is
cumulative; the difference between rows is that stage's cost.
"""
import json
import math
import os
import sys
import time

import bmesh
import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel as CB  # noqa: E402

QUICK = "quick" in sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else False
REPEAT = 2 if QUICK else 3
OUT = os.path.join(HERE, "results")


def build_cut(cut):
    """Like build_cr_bevel.build, but the group outputs the geometry after stage `cut`."""
    ng = bpy.data.node_groups.new(f"CR cut {cut}", "GeometryNodeTree")
    ng.is_modifier = True
    CB._interface(ng)
    B = CB.Builder(ng)
    gi = B.node("NodeGroupInput")
    geo_in = CB._scene_scale(B, gi)[0]
    rule = CB._edge_rule(B, gi, geo_in)
    solid, wire_curves, wire_limit = CB._wires(B, gi, geo_in, rule)
    stages = {"0 wires split": solid}
    prepared, warnings, uniform_limit = CB._edge_data(B, gi, solid, wire_limit, rule)
    stages["1-3 clamp + edge data"] = prepared
    prepared = CB._vertex_modes(B, prepared)
    stages["4-5 vertex modes"] = prepared
    skeleton = CB._skeleton(B, gi, prepared)
    beveled = CB._bevel(B, gi, prepared)
    stages["7 native bevel"] = beveled
    snapped = CB._snap(B, gi, beveled, skeleton)
    stages["6+8 skeleton + snap"] = snapped
    finished = CB._finish(B, gi, snapped, warnings)
    stages["9-10 normals, material"] = finished
    go = B.node("NodeGroupOutput")
    B.link(stages[cut], go.inputs["Geometry"])
    CB.set_menu_defaults(ng)
    return ng, list(stages)


def native_group(offset):
    """The bare Mesh Bevel node with one constant offset: the floor we compare to."""
    ng = bpy.data.node_groups.new("native", "GeometryNodeTree")
    ng.is_modifier = True
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    bev = ng.nodes.new("GeometryNodeMeshBevel")
    for s in ("Start Left Offset", "Start Right Offset", "End Left Offset", "End Right Offset"):
        bev.inputs[s].default_value = offset
    bev.inputs["Segments"].default_value = 4
    ng.links.new(gi.outputs[0], bev.inputs["Mesh"])
    ng.links.new(bev.outputs["Mesh"], go.inputs[0])
    return ng


def time_eval(ob, setup):
    best = math.inf
    for _ in range(REPEAT):
        ob.modifiers.clear()
        setup(ob)
        t0 = time.perf_counter()
        me = A.evaluate(ob)
        best = min(best, time.perf_counter() - t0)
        faces = len(me.polygons)
    ob.modifiers.clear()
    return best, faces


def cube_grid(n):
    bm = bmesh.new()
    for i in range(n):
        for j in range(n):
            for k in range(n // 2):
                ret = bmesh.ops.create_cube(bm, size=1.0)
                bmesh.ops.translate(bm, vec=(i * 2.0, j * 2.0, k * 2.0), verts=ret["verts"])
    return A_from_bm("cubes", bm)


def smooth_model(levels):
    """Subdivided Suzanne: many edges, few of them sharp enough to bevel."""
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    mod = ob.modifiers.new("s", "SUBSURF")
    mod.levels = levels
    bpy.ops.object.modifier_apply(modifier="s")
    return ob


def A_from_bm(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def main():
    ng = CB.build()
    report = {"blender": bpy.app.version_string, "repeat": REPEAT, "models": {}}
    models = [("cube grid (all edges beveled)", cube_grid(16 if QUICK else 30), 0.0),
              ("subdivided Suzanne (angle limit 30 deg)", smooth_model(2 if QUICK else 3), math.radians(30))]
    for label, ob, angle in models:
        rows = {}
        nf = len(ob.data.polygons)
        print(f"\n== {label}: {nf} faces")

        def cr(**inputs):
            def setup(o):
                mod = A.add_cr_bevel(o, ng, 0.1, 4, angle_limit=angle)
                for k, v in inputs.items():
                    A.set_input(mod, k, v)
            return setup

        if angle == 0.0:
            rows["native Mesh Bevel node alone"] = time_eval(ob, lambda o: setattr(
                o.modifiers.new("n", "NODES"), "node_group", native_group(0.1)))
        rows["CR Bevel default"] = time_eval(ob, cr())
        rows["Analytic Normals off"] = time_eval(ob, cr(**{"Analytic Normals": False}))
        rows["Clamp None"] = time_eval(ob, cr(Clamp="None"))
        rows["Clamp Per Edge"] = time_eval(ob, cr(Clamp="Per Edge"))
        rows["Exact Profile off"] = time_eval(ob, cr(**{"Exact Profile": False}))
        for name, (t, f) in rows.items():
            print(f"  {name:32s} {t:7.3f} s  ({f} faces)")
        stages = {}
        _, names = build_cut("0 wires split")
        for cut in names:
            g, _ = build_cut(cut)
            stages[cut] = time_eval(ob, lambda o, g=g: A.add_cr_bevel(o, g, 0.1, 4, angle_limit=angle))[0]
            print(f"  cut after {cut:28s} {stages[cut]:7.3f} s")
        report["models"][label] = dict(faces=nf, configs={k: v[0] for k, v in rows.items()}, stages=stages)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "profile.json"), "w") as fh:
        json.dump(report, fh, indent=2)


main()
