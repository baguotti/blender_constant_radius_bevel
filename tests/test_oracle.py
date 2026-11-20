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

OUT = os.path.join(HERE, "results")
ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "cases": {}, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def project_dykstra(p, normals, offsets, iters=4000):
    """Euclidean projection of p onto {x : normals @ x <= offsets} (Dykstra)."""
    x = p.copy()
    incr = np.zeros((len(normals), 3))
    for _ in range(iters):
        x_prev = x.copy()
        for i, (n, d) in enumerate(zip(normals, offsets)):
            y = x + incr[i]
            excess = n @ y - d
            x_new = y - max(excess, 0.0) * n
            incr[i] = y - x_new
            x = x_new
        if np.abs(x - x_prev).max() < 1e-13:
            break
    return x


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
        check(f"{name}, {seg} segments: every vertex at distance r from the eroded solid ({dist_err:.1e} r)",
              dist_err < 1e-5)
        check(f"{name}, {seg} segments: shading normals point away from it ({normal_err:.1e} rad)",
              normal_err < 1e-4)
        bm.free()

with open(os.path.join(OUT, "oracle_results.json"), "w") as fh:
    json.dump(report, fh, indent=2)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
