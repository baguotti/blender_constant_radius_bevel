"""Independent oracle: convex solids with every edge rounded.

Run:  blender -b --factory-startup --python tests/test_oracle.py

For a convex polyhedron, rounding every edge with a rolling ball of radius r
gives exactly  (solid shrunk by r) grown by r : the set of points at distance r
from the eroded solid E = {x : n_i . x <= d_i - r for every face i}.
So every output vertex must be at distance exactly r from E, and its shading
normal must point from its projection on E towards it.

This uses none of the node group's own ideas (axes, vertex modes, skeleton):
the projection onto E is computed with Dykstra's algorithm on the half-spaces.
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
from analytic import project_dykstra  # noqa: E402

OUT = os.path.join(HERE, "results")
ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "cases": {}, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def prism(name, pts, h=1.2, top_scale=1.0):
    n = len(pts)
    cx, cy = np.mean(pts, axis=0)
    top = [(cx + top_scale * (x - cx), cy + top_scale * (y - cy)) for x, y in pts]
    v = [(x, y, 0) for x, y in pts] + [(x, y, h) for x, y in top]
    f = [list(range(n))[::-1], list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return A.new_object(name, v, f)


def cube():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    me = bpy.data.meshes.new("cube")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("cube", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def skew_box():
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object("skew_box", v, f)


CASES = [
    ("cube", cube, 0.3),
    ("skew box", skew_box, 0.2),
    ("tapered pentagonal prism", lambda: prism("taper", [(0, 0), (3, 0), (3.5, 2), (1, 3), (-0.5, 1.5)],
                                                top_scale=0.6), 0.15),
    ("12-sided cylinder", lambda: prism("cyl12", [(math.cos(a), math.sin(a)) for a in
                                                  np.linspace(0, 2 * math.pi, 12, endpoint=False)]), 0.1),
    ("pentagonal prism", lambda: prism("pent", [(math.cos(a), math.sin(a)) for a in
                                                np.linspace(0, 2 * math.pi, 5, endpoint=False)]), 0.2),
]

for name, make, r in CASES:
    for seg in (3, 6):
        ob = make()
        bm = A.original_bmesh(ob)
        normals = np.array([f.normal[:] for f in bm.faces])
        offsets = np.array([f.normal.dot(f.verts[0].co) - r for f in bm.faces])
        mod = A.add_cr_bevel(ob, ng, r, seg, angle_limit=math.radians(1))
        A.set_input(mod, "Clamp", "None")
        me = A.evaluate(ob)
        cn = me.corner_normals
        vert_normal = {}
        for loop in me.loops:
            vert_normal.setdefault(loop.vertex_index, []).append(np.array(cn[loop.index].vector))
        dist_err = normal_err = 0.0
        for v in me.vertices:
            p = np.array(v.co[:], dtype=float)
            q = project_dykstra(p, normals, offsets)
            d = np.linalg.norm(p - q)
            dist_err = max(dist_err, abs(d - r) / r)
            radial = (p - q) / d
            # A vertex on a bevel or corner has one shading normal; on a crease
            # with flat faces it has several, one of which must match exactly
            # where the surface is smooth: take the best match per vertex.
            # atan2(|cross|, dot) stays accurate for tiny angles (acos does not).
            best = min(math.atan2(np.linalg.norm(np.cross(n, radial)), float(n @ radial))
                       for n in vert_normal.get(v.index, [radial]))
            normal_err = max(normal_err, best)
        report["cases"][f"{name} seg{seg}"] = dict(dist_err=dist_err, normal_err=normal_err, verts=len(me.vertices))
        check(f"{name}, {seg} segments: every vertex at distance r from the eroded solid "
              f"({dist_err:.1e} r, n={len(me.vertices)})", dist_err < 1e-5 and len(me.vertices) > len(bm.verts))
        check(f"{name}, {seg} segments: shading normals point away from it ({normal_err:.1e} rad)",
              normal_err < 1e-4)
        bm.free()

# ---------------------------------------------------------------- concave edges: an enclosed cavity
# Rounding the concave edges of a cavity is rounding the convex edges of the empty
# space K it encloses (from inside K). So every bevel vertex must be at distance r
# from K shrunk by r. The cavity is a skewed box inside a bigger box (two shells),
# so all its corners have 3 faces. Half-space orientation comes from the geometry.


def hollow_box():
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    inner = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    outer = [(-1.0, -1.0, -1.0), (4.0, -1.0, -1.0), (4.0, 3.0, -1.0), (-1.0, 3.0, -1.0),
             (-1.0, -1.0, 2.5), (4.0, -1.0, 2.5), (4.0, 3.0, 2.5), (-1.0, 3.0, 2.5)]
    quads = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    faces = quads + [[i + 8 for i in q[::-1]] for q in quads]       # inner shell faces inward
    return A.new_object("hollow", outer + inner, faces)


for seg in (3, 6):
    r = 0.12
    ob = hollow_box()
    bm = A.original_bmesh(ob)
    cavity = [f for f in bm.faces if all(v.index >= 8 for v in f.verts)]
    centre = sum((f.calc_center_median() for f in cavity), cavity[0].normal * 0) / len(cavity)
    normals, offsets = [], []
    for f in cavity:
        m = f.normal.copy()
        if m.dot(centre - f.calc_center_median()) > 0:      # make m point out of K
            m.negate()
        normals.append(m[:])
        offsets.append(m.dot(f.verts[0].co) - r)
    normals, offsets = np.array(normals), np.array(offsets)
    A.set_edge_selection(ob, lambda e: all(v.index >= 8 for v in e.verts))
    mod = A.add_cr_bevel(ob, ng, r, seg, angle_limit=0.0, selection_attr="sel")
    A.set_input(mod, "Clamp", "None")
    me = A.evaluate(ob)
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    on = set(i for p in me.polygons if flags[p.index] for i in p.vertices)
    err = 0.0
    for i in on:
        p = np.array(me.vertices[i].co[:], dtype=float)
        q = project_dykstra(p, normals, offsets)
        err = max(err, abs(np.linalg.norm(p - q) - r) / r)
    report["cases"][f"enclosed cavity seg{seg}"] = dict(dist_err=err, bevel_verts=len(on))
    check(f"enclosed skewed cavity, {seg} segments: concave bevel vertices at distance r "
          f"({err:.1e} r, n={len(on)})", err < 1e-5 and len(on) > 0)
    bm.free()

with open(os.path.join(OUT, "oracle_results.json"), "w") as fh:
    json.dump(report, fh, indent=2)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
