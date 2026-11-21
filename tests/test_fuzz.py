"""Fuzz tests with random shapes (fixed seeds, so failures are reproducible).

Run:  blender -b --factory-startup --python tests/test_fuzz.py

1. Random convex polyhedra (a cube cut by random planes; corners have 3 faces):
   - at a safe radius, every vertex must be exactly r from the eroded solid
     (same oracle as test_oracle.py);
   - with an oversized radius and Clamp Uniform, the node must lower the radius
     to 0.95 x the overlap limit, computed here independently in Python from
     the face inset rule, and the result must again match the oracle.
2. Random star-shaped prisms (convex and concave edges): no NaN, no flips
   against the native bevel, exact wherever an exact answer exists.
"""
import json
import math
import os
import random
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
INLINE_SIN = math.sin(math.radians(2.0))


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def random_convex(seed, cuts=6):
    rng = random.Random(seed)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    for _ in range(cuts):
        n = np.array([rng.gauss(0, 1) for _ in range(3)])
        n /= np.linalg.norm(n)
        co = n * rng.uniform(0.55, 0.85)
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        ret = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=n, clear_outer=True)
        cut_edges = [g for g in ret["geom_cut"] if isinstance(g, bmesh.types.BMEdge)]
        if cut_edges:
            bmesh.ops.holes_fill(bm, edges=cut_edges, sides=0)
    bmesh.ops.dissolve_limit(bm, angle_limit=1e-4, verts=bm.verts[:], edges=bm.edges[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(f"convex{seed}")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(f"convex{seed}", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def uniform_limit(bm):
    """Largest radius before the native construction overlaps (face inset rule)."""
    D = {e.index: math.tan(e.link_faces[0].normal.angle(e.link_faces[1].normal) / 2)
         if len(e.link_faces) == 2 else 0.0 for e in bm.edges}
    best = float("inf")
    for f in bm.faces:
        loops = list(f.loops)
        n = len(loops)

        def angle(lp):
            a = (lp.link_loop_prev.vert.co - lp.vert.co).normalized()
            b = (lp.link_loop_next.vert.co - lp.vert.co).normalized()
            return a.dot(b), b.cross(a).dot(f.normal)

        for i, lp in enumerate(loops):
            l1 = loops[(i + 1) % n]
            g, prev, nxt = lp.edge.index, lp.link_loop_prev.edge.index, l1.edge.index
            ca, sa = angle(lp)
            cb, sb = angle(l1)
            t0 = (D[prev] + D[g] * ca) / sa if abs(sa) >= INLINE_SIN else 0.0
            t1 = (D[nxt] + D[g] * cb) / sb if abs(sb) >= INLINE_SIN else 0.0
            k = t0 + t1
            if k > 1e-9:
                best = min(best, lp.edge.calc_length() / k)
    return best


def oracle_error(me, bm, r):
    normals = np.array([f.normal[:] for f in bm.faces])
    offsets = np.array([f.normal.dot(f.verts[0].co) - r for f in bm.faces])
    worst = 0.0
    for v in me.vertices:
        p = np.array(v.co[:], dtype=float)
        worst = max(worst, abs(np.linalg.norm(p - project_dykstra(p, normals, offsets)) - r) / r)
    return worst


# ---------------------------------------------------------------- 1. random convex polyhedra

for seed in range(25):
    rng = random.Random(1000 + seed)
    seg = rng.randint(2, 6)
    probe = random_convex(seed)
    bm = A.original_bmesh(probe)
    r_lim = uniform_limit(bm)
    valence_ok = all(len(v.link_faces) == 3 for v in bm.verts)
    nfaces = len(bm.faces)
    for label, r_in, clamp, r_expect in (("safe radius", 0.4 * r_lim, "None", 0.4 * r_lim),
                                         ("oversized, Uniform", 5.0 * r_lim, "Uniform", 0.95 * r_lim)):
        ob = random_convex(seed)
        mod = A.add_cr_bevel(ob, ng, r_in, seg, angle_limit=math.radians(0.5))
        A.set_input(mod, "Clamp", clamp)
        me = A.evaluate(ob)
        err = oracle_error(me, bm, r_expect)
        report["cases"][f"convex {seed} {label}"] = dict(faces=nfaces, segments=seg, r=r_expect, err=err,
                                                       three_valent=valence_ok)
        name = f"random convex #{seed} ({nfaces} faces, {seg} seg), {label}: oracle error {err:.1e} r"
        check(name, err < 1e-4 if valence_ok else True,
              "" if valence_ok else "skipped: a vertex has more than 3 faces")
    bm.free()

# ---------------------------------------------------------------- 2. random star-shaped prisms


def random_star_prism(seed):
    rng = random.Random(seed)
    n = rng.randint(5, 9)
    pts = []
    for i in range(n):
        a = 2 * math.pi * (i + rng.uniform(-0.25, 0.25)) / n
        rad = rng.uniform(0.6, 1.4)
        pts.append((rad * math.cos(a), rad * math.sin(a)))
    return A.extrude_polygon(f"star{seed}", pts, h=rng.uniform(0.6, 1.5))


for seed in range(15):
    rng = random.Random(2000 + seed)
    seg, r = rng.randint(1, 6), rng.uniform(0.03, 0.12)
    results = []
    for exact in (True, False):
        ob = random_star_prism(seed)
        mod = A.add_cr_bevel(ob, ng, r, seg, angle_limit=math.radians(10), exact=exact)
        A.set_input(mod, "Clamp", "Per Edge")
        if not exact:
            A.set_input(mod, "Analytic Normals", False)
        results.append((ob, A.evaluate(ob), mod))
    (ob, me, mod), (_, me_n, _) = results
    P = np.array([v.co[:] for v in me.vertices])
    flips = sum(A.count_flips(me, me_n))
    clamped = any("clamped" in w.message for w in mod.node_warnings)
    detail = dict(segments=seg, r=round(r, 4), flips=flips, clamped=clamped)
    if not clamped:      # the exact reference assumes one radius everywhere
        bm = A.original_bmesh(ob)
        sel = [e.index for e in bm.edges if len(e.link_faces) == 2 and
               e.link_faces[0].normal.angle(e.link_faces[1].normal) > math.radians(10)]
        frames = A.edge_frames(bm, r, sel)
        modes = A.classify_vertices(bm, frames, r)
        stats, _ = A.exact_error(me, bm, frames, modes, r)
        detail["exact_error"] = max((v[0] for k, v in stats.items() if v[1] and k != "joint_seam_end"), default=0)
        bm.free()
    report["cases"][f"star {seed}"] = detail
    check(f"random star prism #{seed}: finite, no flips, exact where defined",
          np.isfinite(P).all() and flips == 0 and detail.get("exact_error", 0) < 1e-4, detail)

# ---------------------------------------------------------------- 3. tilted prisms, partial selections


def random_tilted_prism(seed):
    rng = random.Random(seed)
    n = rng.randint(4, 8)
    pts = []
    for i in range(n):
        a = 2 * math.pi * (i + rng.uniform(-0.3, 0.3)) / n
        rad = rng.uniform(0.6, 1.4)
        pts.append((rad * math.cos(a), rad * math.sin(a)))
    tx, ty, h = rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), rng.uniform(0.8, 1.6)
    v = [(x, y, 0.0) for x, y in pts] + [(x, y, h + tx * x + ty * y) for x, y in pts]
    f = [list(range(n))[::-1], list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return A.new_object(f"tilt{seed}", v, f)


for seed in range(20):
    rng = random.Random(3000 + seed)
    seg, r = rng.randint(1, 6), rng.uniform(0.03, 0.1)
    clamp = rng.choice(["Uniform", "Per Edge", "None"])
    keep = {i for i in range(64) if rng.random() < 0.6}          # random subset of edges
    choose = lambda e: e.index in keep                            # noqa: E731
    results = []
    for exact in (True, False):
        ob = random_tilted_prism(seed)
        A.set_edge_selection(ob, choose)
        mod = A.add_cr_bevel(ob, ng, r, seg, angle_limit=math.radians(10), exact=exact, selection_attr="sel")
        A.set_input(mod, "Clamp", clamp)
        if not exact:
            A.set_input(mod, "Analytic Normals", False)
        results.append((ob, A.evaluate(ob), mod))
    (ob, me, mod), (_, me_n, _) = results
    pts = np.array([v.co[:] for v in ob.data.vertices])
    P = np.array([v.co[:] for v in me.vertices])
    outside = float(np.maximum(P - pts.max(0), pts.min(0) - P).max())
    Pn = np.array([v.co[:] for v in me_n.vertices])
    native_outside = max(0.0, float(np.maximum(Pn - pts.max(0), pts.min(0) - Pn).max()))   # no worse than native
    flips = sum(A.count_flips(me, me_n))
    clamped = any("clamped" in w.message for w in mod.node_warnings)
    detail = dict(segments=seg, r=round(r, 4), clamp=clamp, flips=flips, outside=round(outside, 6), clamped=clamped)
    if not clamped:
        bm = A.original_bmesh(ob)
        sel = [e.index for e in bm.edges if e.index in keep and len(e.link_faces) == 2 and
               e.link_faces[0].normal.angle(e.link_faces[1].normal) > math.radians(10)]
        frames = A.edge_frames(bm, r, sel)
        modes = A.classify_vertices(bm, frames, r)
        stats, _ = A.exact_error(me, bm, frames, modes, r)
        detail["exact_error"] = max((v[0] for k, v in stats.items() if v[1] and k != "joint_seam_end"), default=0)
        detail["modes"] = {m: sum(1 for v in modes.values() if A.MODE_NAMES[v["mode"]] == m) for m in A.MODE_NAMES}
        bm.free()
    report["cases"][f"tilted {seed}"] = detail
    check(f"random tilted prism #{seed} ({clamp}, {seg} seg): finite, no flips, inside bbox, exact where defined",
          np.isfinite(P).all() and flips == 0 and outside < native_outside + 1e-6 and
          detail.get("exact_error", 0) < 1e-4, detail)

# ---------------------------------------------------------------- 4. clamp fuzz: oversized radii


def random_boolean_block(seed):
    rng = random.Random(seed)
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    ob = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(radius=rng.uniform(0.2, 0.6), depth=3.0, vertices=rng.choice([12, 24, 32]),
                                        location=(rng.uniform(-0.4, 0.4), rng.uniform(-0.4, 0.4), 0.0),
                                        rotation=(rng.uniform(0, 3.1), rng.uniform(0, 3.1), 0))
    cutter = bpy.context.object
    mod = ob.modifiers.new("cut", "BOOLEAN")
    mod.object, mod.solver = cutter, "EXACT"
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter)
    return ob


def min_radius(mod):
    for w in mod.node_warnings:
        if "smallest radius used:" in w.message:
            return float(w.message.rsplit(":", 1)[1])
    return None


makers = [("convex", lambda s: random_convex(s)), ("star", lambda s: random_star_prism(s)),
          ("boolean", lambda s: random_boolean_block(s))]
for kind, make in makers:
    for seed in range(6):
        rng = random.Random(4000 + seed)
        r_in, seg = rng.uniform(0.5, 2.0), rng.randint(2, 5)
        radii = {}
        for clamp in ("Uniform", "Per Edge"):
            native, exact = make(seed), make(seed)
            pts = np.array([v.co[:] for v in exact.data.vertices])
            mod_n = A.add_cr_bevel(native, ng, r_in, seg, angle_limit=math.radians(25), exact=False)
            A.set_input(mod_n, "Analytic Normals", False)
            A.set_input(mod_n, "Clamp", clamp)
            mod = A.add_cr_bevel(exact, ng, r_in, seg, angle_limit=math.radians(25))
            A.set_input(mod, "Clamp", clamp)
            me_n, me = A.evaluate(native), A.evaluate(exact)
            P, Pn = np.array([v.co[:] for v in me.vertices]), np.array([v.co[:] for v in me_n.vertices])
            out = float(np.maximum(P - pts.max(0), pts.min(0) - P).max())
            out_n = max(0.0, float(np.maximum(Pn - pts.max(0), pts.min(0) - Pn).max()))
            inverted = A.inverted_faces(me_n, A.original_bmesh(native))
            flips = sum(A.count_flips(me, me_n))
            radii[clamp] = min_radius(mod)
            detail = dict(r_in=round(r_in, 3), seg=seg, inverted_native=inverted, flips=flips,
                          outside=round(out, 6), outside_native=round(out_n, 6), min_radius=radii[clamp])
            report["cases"][f"clamp {kind} {seed} {clamp}"] = detail
            # CR Bevel must never be worse than the native bevel at the same radius.
            check(f"clamp fuzz {kind} #{seed} {clamp}: no flips, no worse than native",
                  np.isfinite(P).all() and flips == 0 and out <= out_n + 1e-6, detail)
            if kind != "boolean":
                # The per-face clamp rule is complete here: no overlap at all.
                check(f"clamp fuzz {kind} #{seed} {clamp}: no overlap after clamping", inverted == 0, detail)
            else:
                # Known limit (shared with Blender's Bevel modifier): bevels can still
                # collide across a thin wall between a hole and an outer edge.
                report.setdefault("thin_wall_overlaps", []).append(dict(seed=seed, clamp=clamp, inverted=inverted))
        if radii["Uniform"] is not None and radii["Per Edge"] is not None:
            check(f"clamp fuzz {kind} #{seed}: Per Edge smallest radius >= Uniform radius",
                  radii["Per Edge"] >= radii["Uniform"] - 1e-4, radii)

with open(os.path.join(OUT, "fuzz_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=str)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
