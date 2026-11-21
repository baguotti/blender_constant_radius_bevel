"""Robustness tests: awkward inputs the phase tests don't cover.

Run:  blender -b --factory-startup --python tests/test_robustness.py
Writes tests/results/robustness_results.json. Exit code is non-zero on failure.

For every case: no crash, no NaN/inf, same topology as the native bevel (Exact
Profile off) and no face flipped against it. Where a case should stay exact,
the exact error is checked too.
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]
import analytic as A  # noqa: E402
import build_cr_bevel  # noqa: E402

OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
ng = build_cr_bevel.build()
report = {"blender": bpy.app.version_string, "cases": {}, "checks": []}
failures = []


def check(name, ok, detail=""):
    report["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def from_bm(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def skew_box(name, scale=1.0, offset=(0.0, 0.0, 0.0)):
    b = [(0, 0, 0), (2, 0, 0), (2.4, 1.6, 0), (0.3, 1.8, 0)]
    v = b + [(x + 0.5, y + 0.2, 1.5) for x, y, _ in b]
    v = [tuple(c * scale + o for c, o in zip(p, offset)) for p in v]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return A.new_object(name, v, f)


def cube_bm(size=2.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    return bm


def run_case(name, make, radius, segments=4, clamp="Uniform", angle=math.radians(30), exact_r=None,
             selection=None, prepare=None, prepare_native=True):
    """prepare_native=False: the native reference skips `prepare`; its vertex order
    then differs, so orientation is checked against the exact normals instead."""
    """Evaluate exact and native on two copies; return the exact mesh and stats."""
    obs = []
    for exact in (True, False):
        ob = make()
        if prepare and (exact or prepare_native):
            prepare(ob)
        if selection:
            A.set_edge_selection(ob, selection)
        mod = A.add_cr_bevel(ob, ng, radius, segments, angle_limit=angle, exact=exact,
                             selection_attr="sel" if selection else None)
        A.set_input(mod, "Clamp", clamp)
        if not exact:
            A.set_input(mod, "Analytic Normals", False)
        obs.append((ob, mod))
    try:
        me, me_n = A.evaluate(obs[0][0]), A.evaluate(obs[1][0])
    except Exception as exc:  # pragma: no cover
        check(f"{name}: evaluates", False, exc)
        return None
    P = np.array([v.co[:] for v in me.vertices]) if len(me.vertices) else np.zeros((0, 3))
    same = len(me.vertices) == len(me_n.vertices) and len(me.polygons) == len(me_n.polygons)
    if prepare_native or not prepare:
        flips = sum(A.count_flips(me, me_n)) if same else -1
    else:
        flips = A.inverted_faces(me, A.original_bmesh(obs[0][0]))
    stats = dict(verts=len(me.vertices), faces=len(me.polygons), finite=bool(np.isfinite(P).all()),
                 same_topology=same, flips=flips, warnings=[w.message for w in obs[0][1].node_warnings])
    check(f"{name}: finite, same topology as native, no flips", stats["finite"] and same and flips == 0, stats)
    if exact_r is not None:
        ob = obs[0][0]
        bm = A.original_bmesh(ob)
        sel = [e.index for e in bm.edges if len(e.link_faces) == 2 and
               e.link_faces[0].normal.angle(e.link_faces[1].normal) > angle] if not selection else A.selected_edges(ob)
        frames = A.edge_frames(bm, exact_r, sel)
        modes = A.classify_vertices(bm, frames, exact_r)
        errs, fb = A.exact_error(me, bm, frames, modes, exact_r)
        worst = max((v[0] for k, v in errs.items() if v[1] and k != "joint_seam_end"), default=0.0)
        stats["exact_error"] = worst
        stats["mode_counts"] = {m: sum(1 for i in modes.values() if A.MODE_NAMES[i["mode"]] == m) for m in A.MODE_NAMES}
        coord = float(np.abs(np.array([v.co[:] for v in ob.data.vertices])).max())
        tol = max(1e-4, 16 * np.spacing(np.float32(coord)) / exact_r)   # float32 limit far from origin
        check(f"{name}: exact (error {worst:.1e} r, tolerance {tol:.0e})", worst < tol, stats["mode_counts"])
        bm.free()
    report["cases"][name] = stats
    return me, stats


# ---------------------------------------------------------------- scale and position

_, base = run_case("skew box at origin", lambda: skew_box("sb0"), 0.15, exact_r=0.15)
_, far = run_case("skew box 1000 units from origin", lambda: skew_box("sb1", offset=(1000, 1000, 1000)), 0.15,
                  exact_r=0.15)
check("far from origin: same vertex modes as at origin", far.get("mode_counts") == base.get("mode_counts"),
      (far.get("mode_counts"), base.get("mode_counts")))
run_case("skew box scaled 0.001", lambda: skew_box("sbs", scale=0.001), 0.00015, exact_r=0.00015)
run_case("skew box scaled 1000", lambda: skew_box("sbl", scale=1000.0), 150.0, exact_r=150.0)

# ---------------------------------------------------------------- broken input normals


def flip_one_face(ob):
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
    bm.to_mesh(me)
    bm.free()


def flip_all_faces(ob):
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()


def same_points(me_a, me_b):
    """Largest distance from a vertex of one mesh to the nearest vertex of the other."""
    a = np.array([v.co[:] for v in me_a.vertices])
    b = np.array([v.co[:] for v in me_b.vertices])
    if not len(a) or not len(b):
        return float("inf")
    d_ab = max(np.linalg.norm(b - p, axis=1).min() for p in a)
    d_ba = max(np.linalg.norm(a - p, axis=1).min() for p in b)
    return float(max(d_ab, d_ba))


good, _ = run_case("skew box, consistent normals (reference)", lambda: skew_box("sbgood"), 0.15)
for label, prep in (("one face flipped", flip_one_face), ("inside out", flip_all_faces)):
    me, _ = run_case(f"skew box, {label}", lambda: skew_box("sbflip"), 0.15, prepare=prep)
    if me is not None:
        dev = same_points(me, good)
        report["cases"][f"skew box, {label}"]["deviation_from_consistent"] = dev
        if label == "inside out":   # consistently flipped: still a valid solid, same result
            check(f"skew box, {label}: same shape as with consistent normals (max dev {dev:.1e})", dev < 1e-5)
        else:                       # inconsistent: its edges are skipped and a warning explains why
            warns = report["cases"][f"skew box, {label}"]["warnings"]
            check(f"skew box, {label}: warns about flipped faces", any("wrong way" in w for w in warns), warns)

# ---------------------------------------------------------------- topology


def triangulated_cube():
    bm = cube_bm()
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    return from_bm("tri_cube", bm)


def open_box():
    bm = cube_bm()
    bm.faces.ensure_lookup_table()
    top = [f for f in bm.faces if f.normal.z > 0.9]
    bmesh.ops.delete(bm, geom=top, context="FACES_ONLY")
    return from_bm("open_box", bm)


def cube_with_duplicate_vertex():
    bm = cube_bm()
    bm.verts.ensure_lookup_table()
    corner = bm.verts[0].co.copy()
    edge = bm.verts[0].link_edges[0]
    ret = bmesh.ops.subdivide_edges(bm, edges=[edge], cuts=1)
    new = [g for g in ret["geom_inner"] if isinstance(g, bmesh.types.BMVert)][0]
    new.co = corner             # zero-length edge between the corner and the new vertex
    return from_bm("dup_vert", bm)


def cube_with_loose_parts():
    bm = cube_bm()
    a = bm.verts.new((5, 0, 0))
    b = bm.verts.new((6, 0, 0))
    bm.edges.new((a, b))
    bm.verts.new((7, 7, 7))
    return from_bm("loose", bm)


run_case("triangulated cube", triangulated_cube, 0.15)
run_case("open box (boundary edges)", open_box, 0.15, exact_r=0.15)
run_case("cube with a zero-length edge", cube_with_duplicate_vertex, 0.15)
run_case("cube with loose vertex and edge", cube_with_loose_parts, 0.15, exact_r=0.15)

# ---------------------------------------------------------------- angles


def sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    return bpy.context.object


def knife_wedge(deg):
    a = math.radians(deg)          # interior angle -> phi = 180 - deg
    pts = [(0.0, 0.0), (4.0, 0.0), (4 * math.cos(a), 4 * math.sin(a))]
    return A.extrude_polygon("knife", pts)


run_case("UV sphere, Angle Limit 0", sphere, 0.02, angle=0.0)
for deg in (2, 3, 5):   # 2 deg is past MAX_EDGE_ANGLE (skipped); 3 and 5 are beveled
    run_case(f"knife edge ({deg} deg between faces)", lambda: knife_wedge(deg), 0.05, segments=16)

# ---------------------------------------------------------------- segments and selection

for seg in (1, 64):
    run_case(f"skew box, {seg} segments", lambda: skew_box(f"sbseg{seg}"), 0.15, segments=seg, exact_r=0.15)
run_case("skew box, partial selection, Per Edge", lambda: skew_box("sbsel"), 0.15, clamp="Per Edge",
         selection=lambda e: any(v.co.z > 1.0 for v in e.verts), exact_r=0.15)

# ---------------------------------------------------------------- inherited data


def with_custom_normals(ob):
    ob.data.normals_split_custom_set_from_vertices([(0.0, 0.0, 1.0)] * len(ob.data.vertices))


def with_cr_attribute(ob):
    a = ob.data.attributes.new("cr_bevel_face", "BOOLEAN", "FACE")
    for d in a.data:
        d.value = True


# The native node bends its profile according to custom normals, so the native
# reference is made without them (CR Bevel itself must be exact either way).
run_case("input already has custom normals", lambda: skew_box("sbcn"), 0.15, prepare=with_custom_normals,
         prepare_native=False, exact_r=0.15)
me, _ = run_case("input already has a cr_bevel_face attribute", lambda: skew_box("sbattr"), 0.15,
                 prepare=with_cr_attribute, exact_r=0.15)
if me is not None:
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    check("cr_bevel_face is recomputed (not inherited)", 0 < flags.sum() < len(flags), int(flags.sum()))

# Smooth/flat flags and sharp edges of the input survive (with Analytic Normals
# off they are all that decides shading).
bpy.ops.mesh.primitive_cylinder_add(vertices=16)
ob = bpy.context.object
for poly in ob.data.polygons:
    poly.use_smooth = abs(poly.normal.z) < 0.5      # smooth sides, flat caps
mod = A.add_cr_bevel(ob, ng, 0.1, 4)
A.set_input(mod, "Analytic Normals", False)
me = A.evaluate(ob)
flags = np.zeros(len(me.polygons), bool)
me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
orig = [p for p in me.polygons if not flags[p.index]]
check("input smooth/flat face flags survive",
      all(p.use_smooth == (abs(p.normal.z) < 0.5) for p in orig), len(orig))

# ---------------------------------------------------------------- open meshes (reported by the user)


def _flipped(ob):
    flip_all_faces(ob)
    return ob


def profile_bar(name, open_front=False, open_back=False, tilt=0.0):
    """The user's test shape: a profile with a 45 and a 135 degree edge, extruded
    along y. Optionally without its front/back face; `tilt` slants the front."""
    prof = [(1.0, 1.0), (3.0, -1.0), (-1.0, -1.0), (-1.0, 1.0)]       # x, z
    front = [(x, -1.0 - tilt * (x + 1.0), z) for x, z in prof]
    back = [(x, 1.0, z) for x, z in prof]
    v = front + back
    faces = [[i, (i + 1) % 4, 4 + (i + 1) % 4, 4 + i] for i in range(4)]
    if not open_front:
        faces.append([3, 2, 1, 0])
    if not open_back:
        faces.append([4, 5, 6, 7])
    ob = A.new_object(name, v, faces)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    return ob


for label, kw in (("open on both sides", dict(open_front=True, open_back=True)),
                  ("open on one side", dict(open_front=True)),
                  ("open on both sides, slanted open end", dict(open_front=True, open_back=True, tilt=0.3))):
    _, st = run_case(f"profile bar {label}", lambda: profile_bar("bar", **kw), 0.1, segments=6,
                     angle=math.radians(20), exact_r=0.1)
    check(f"profile bar {label}: open ends are exact (no fallback)",
          st.get("mode_counts", {}).get("fallback", 1) == 0, st.get("mode_counts"))


def fold_sheet(name, slant=0.0, triangles=False):
    """An open sheet folded along y (2 faces). Trapezoid flaps; `slant` tilts the
    open ends; `triangles`: two triangles sharing the fold (a "book")."""
    if triangles:
        return A.new_object(name, [(0, -1, 0), (0, 1, 0), (1, 0, 0.3), (-0.8, 0.2, 0.9)], [[0, 1, 2], [1, 0, 3]])
    v = [(0, -1, 0), (0, 1, 0), (2, 1.5, 0), (2, -1 - slant, 0), (0, 1.3, 2), (0, -1.2 + slant, 2)]
    return A.new_object(name, v, [[0, 1, 2, 3], [1, 0, 5, 4]])


for label, make in (("folded sheet, trapezoid flaps", lambda: fold_sheet("fold")),
                    ("folded sheet, slanted open ends", lambda: fold_sheet("fold", 0.6)),
                    ("two triangles sharing an edge", lambda: fold_sheet("book", triangles=True)),
                    ("open bar, normals flipped (concave)",
                     lambda: _flipped(profile_bar("bar", open_front=True, open_back=True))),
                    ("open bar, open ends slanted 76 deg",
                     lambda: profile_bar("bar", open_front=True, open_back=True, tilt=4.0))):
    _, st = run_case(label, make, 0.1, segments=6, angle=math.radians(10), exact_r=0.1)
    check(f"{label}: open ends are exact (no fallback)",
          st.get("mode_counts", {}).get("fallback", 1) == 0, st.get("mode_counts"))

# ---------------------------------------------------------------- polylines (wire edges)


def polyline(name, pts, closed=False):
    n = len(pts)
    edges = [(i, i + 1) for i in range(n - 1)] + ([(n - 1, 0)] if closed else [])
    return A.new_object(name, pts, [], edges)


for label, pts, closed in (("open 3D zigzag", [(0, 0, 0), (2, 0, 0), (2.5, 1.5, 0.5), (1, 2, 1.5), (1, 3, 0)], False),
                           ("closed square loop", [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], True)):
    r = 0.15
    ob = polyline(f"wire_{label}", pts, closed)
    A.add_cr_bevel(ob, ng, r, 6)
    me = A.evaluate(ob)
    P = np.array([v.co[:] for v in me.vertices])
    corners = pts if closed else pts[1:-1]
    worst = 0.0
    for c in corners:
        near = P[np.linalg.norm(P - np.array(c), axis=1) < 3 * r]
        cen = near.mean(0)
        _, _, vt = np.linalg.svd(near - cen)          # arc plane
        uv = (near - cen) @ vt[:2].T
        sol = np.linalg.lstsq(np.c_[2 * uv, np.ones(len(uv))], (uv ** 2).sum(1), rcond=None)[0]
        rad = math.sqrt(sol[2] + sol[:2] @ sol[:2])
        worst = max(worst, abs(rad - r) / r, float(np.abs(np.linalg.norm(uv - sol[:2], axis=1) - rad).max()) / r)
    ends_ok = closed or all(np.min(np.linalg.norm(P - np.array(e), axis=1)) < 1e-6 for e in (pts[0], pts[-1]))
    check(f"polyline {label}: every corner an arc of radius r ({worst:.1e} r), ends kept",
          worst < 1e-5 and ends_ok and len(me.polygons) == 0, len(P))

ob = polyline("wire_off", [(0, 0, 0), (2, 0, 0), (2, 2, 0)])
mod = A.add_cr_bevel(ob, ng, 0.15, 6)
A.set_input(mod, "Wire Corners", False)
check("Wire Corners off: polyline left unchanged", len(A.evaluate(ob).vertices) == 3)


def fit_arc_radius(P, c, window):
    """Radius of the circle fitted to the output points within `window` of corner c
    (the arc reaches r / tan(a/2) from the corner: r at a right angle)."""
    near = P[np.linalg.norm(P - np.array(c), axis=1) < window]
    cen = near.mean(0)
    _, _, vt = np.linalg.svd(near - cen)
    uv = (near - cen) @ vt[:2].T
    sol = np.linalg.lstsq(np.c_[2 * uv, np.ones(len(uv))], (uv ** 2).sum(1), rcond=None)[0]
    return math.sqrt(sol[2] + sol[:2] @ sol[:2])


# Segments: a corner becomes Segments + 1 points, like Bevel's Vertices mode.
seg_ok = []
for seg in (1, 2, 5):
    ob = polyline(f"wire_seg{seg}", [(0, 0, 0), (2, 0, 0), (2, 2, 0)])
    A.add_cr_bevel(ob, ng, 0.3, seg)
    seg_ok.append(len(A.evaluate(ob).vertices) == 2 + seg + 1)
check("polyline: a corner gets Segments + 1 points", all(seg_ok), seg_ok)

# Radius <= 0 leaves wires alone (as it leaves edges alone).
r_ok = []
for r in (0.0, -0.2):
    ob = polyline("wire_r", [(0, 0, 0), (2, 0, 0), (2, 2, 0)])
    A.add_cr_bevel(ob, ng, r, 4)
    r_ok.append(len(A.evaluate(ob).vertices) == 3)
check("polyline: radius 0 or negative leaves it unchanged", all(r_ok), r_ok)

# Clamp on a polyline with one short piece (0.4 between two right angles):
# Uniform lowers every corner to 0.2, Per Edge only the two next to it.
zig = [(0, 0, 0), (3, 0, 0), (3, 0.4, 0), (6, 0.4, 0), (6, 3, 0)]
got = {}
for cl in ("Uniform", "Per Edge"):
    ob = polyline(f"wire_clamp_{cl}", zig)
    A.set_input(A.add_cr_bevel(ob, ng, 0.5, 8), "Clamp", cl)
    P = np.array([v.co[:] for v in A.evaluate(ob).vertices])
    got[cl] = [fit_arc_radius(P, c, 1.01 * w) for c, w in zip(zig[1:-1], (0.2, 0.2, 0.2 if cl == "Uniform" else 0.5))]
check("polyline Clamp Uniform: one radius at every corner (0.2)",
      all(abs(x - 0.2) < 1e-4 for x in got["Uniform"]), [round(x, 4) for x in got["Uniform"]])
check("polyline Clamp Per Edge: only the short piece's corners are lowered",
      all(abs(x - 0.2) < 1e-4 for x in got["Per Edge"][:2]) and abs(got["Per Edge"][2] - 0.5) < 1e-4,
      [round(x, 4) for x in got["Per Edge"]])

# A duplicate point (zero-length piece) must not lower the Uniform radius to 0.
ob = polyline("wire_dup", [(0, 0, 0), (2, 0, 0), (2, 0, 0), (2, 2, 0), (4, 2, 0)])
A.add_cr_bevel(ob, ng, 0.3, 4)
P = np.array([v.co[:] for v in A.evaluate(ob).vertices])
check("polyline with a duplicate point: other corners still rounded at r",
      abs(fit_arc_radius(P, (2, 2, 0), 0.31) - 0.3) < 1e-4, len(P))

# Uniform is one radius for the whole object: a solid next to a polyline.
bm = cube_bm()
vs = [bm.verts.new(c) for c in ((3, 0, 0), (5, 0, 0), (5, 0.2, 0), (7, 0.2, 0))]
for a, b in zip(vs, vs[1:]):
    bm.edges.new((a, b))
ob = from_bm("cube_and_wire", bm)
mod = A.add_cr_bevel(ob, ng, 0.4, 4)
me = A.evaluate(ob)
check("Clamp Uniform: the cube takes the polyline's limit too (0.1)",
      any("0.1000" in w.message for w in mod.node_warnings), [w.message for w in mod.node_warnings])

# Wires stay connected: at a branch, and where a wire touches the rest of the mesh.
ob = A.new_object("wire_T", [(0, 0, 0), (2, 0, 0), (4, 0, 0), (2, 2, 0), (2, 3, 1)], [], [(0, 1), (1, 2), (1, 3), (3, 4)])
A.add_cr_bevel(ob, ng, 0.3, 4)
me = A.evaluate(ob)
check("branching polyline: corner rounded, branch point shared (9 points, 8 edges)",
      (len(me.vertices), len(me.edges)) == (9, 8), (len(me.vertices), len(me.edges)))
bm = cube_bm()
bm.verts.ensure_lookup_table()
v0 = bm.verts[0]
a = bm.verts.new(v0.co * 2)
b = bm.verts.new(v0.co * 2 + Vector((1, 0, 0)))
bm.edges.new((v0, a))
bm.edges.new((a, b))
ob = from_bm("wire_on_cube", bm)
A.add_cr_bevel(ob, ng, 0.3, 4, angle_limit=math.radians(100))       # cube itself not beveled
me = A.evaluate(ob)
deg = np.bincount(np.array([e.vertices[:] for e in me.edges]).ravel(), minlength=len(me.vertices))
check("polyline touching a face's vertex stays attached to it",
      len(me.vertices) == 8 + 1 + 5 and deg.max() == 4, (len(me.vertices), int(deg.max())))

# Loose points are kept, with Wire Corners on and off (v1.0.4 dropped them).
for on in (True, False):
    bm = cube_bm()
    bm.verts.new((5, 5, 5))
    a, b, c = (bm.verts.new(p) for p in ((6, 0, 0), (8, 0, 0), (8, 2, 0)))
    bm.edges.new((a, b))
    bm.edges.new((b, c))
    ob = from_bm("loose_point", bm)
    A.set_input(A.add_cr_bevel(ob, ng, 0.2, 3), "Wire Corners", on)
    P = np.array([v.co[:] for v in A.evaluate(ob).vertices])
    loose = np.min(np.linalg.norm(P - np.array((5, 5, 5)), axis=1)) < 1e-6
    check(f"loose point kept (Wire Corners {'on' if on else 'off'})",
          loose and len(P) == 96 + 1 + (2 + 4 if on else 3), len(P))

# ---------------------------------------------------------------- Per Edge on an organic model (v1.0.6)


def suzanne():
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    mod = ob.modifiers.new("s", "SUBSURF")
    mod.levels = 2
    bpy.ops.object.modifier_apply(modifier="s")
    return ob


me_pe, st = run_case("subdivided Suzanne, Per Edge", suzanne, 0.05, clamp="Per Edge", angle=math.radians(30))
# Every corner of every face must keep its bevels apart with the final radii:
# the inset rule t0 + t1 <= |g| (radii read back from the internal group output).
ng_dbg = bpy.data.node_groups.new("CR prepared", "GeometryNodeTree")
ng_dbg.is_modifier = True
build_cr_bevel._interface(ng_dbg)
Bd = build_cr_bevel.Builder(ng_dbg)
gid = Bd.node("NodeGroupInput")
_solid, _wc, _wl = build_cr_bevel._wires(Bd, gid)
_prep, _, _ = build_cr_bevel._edge_data(Bd, gid, _solid, _wl)
Bd.link(_prep, Bd.node("NodeGroupOutput").inputs["Geometry"])
for item in ng_dbg.interface.items_tree:
    if getattr(item, "name", "") == "Clamp" and item.in_out == "INPUT":
        item.default_value = build_cr_bevel.CLAMP_MODES[0]
ob = suzanne()
A.set_input(A.add_cr_bevel(ob, ng_dbg, 0.05, 4, angle_limit=math.radians(30)), "Clamp", "Per Edge")
mp = A.evaluate(ob)
dd = np.zeros(len(mp.edges))
mp.attributes["_cr_d"].data.foreach_get("value", dd)
beveled = np.zeros(len(mp.edges), bool)
mp.attributes["_cr_sel"].data.foreach_get("value", beveled)
dd *= beveled                      # _cr_d is stored on every edge; only selected ones are beveled
worst = 0.0
key_to_edge = {e.key: e.index for e in mp.edges}
for f in mp.polygons:
    vs, es = list(f.vertices), list(f.edge_keys)
    n = len(vs)
    nrm = Vector(f.normal)
    for i in range(n):
        a, b = Vector(mp.vertices[vs[i]].co), Vector(mp.vertices[vs[(i + 1) % n]].co)
        prv, nxt = Vector(mp.vertices[vs[i - 1]].co), Vector(mp.vertices[vs[(i + 2) % n]].co)
        g = key_to_edge[tuple(sorted((vs[i], vs[(i + 1) % n])))]
        ep = key_to_edge[tuple(sorted((vs[i - 1], vs[i])))]
        en = key_to_edge[tuple(sorted((vs[(i + 1) % n], vs[(i + 2) % n])))]
        if dd[g] == 0 and dd[ep] == 0 and dd[en] == 0:
            continue
        def t(here, to_prev, to_next, d_side):
            """Inset of the bevel's boundary point along g at this corner (as in the builder):
            (D_side + D_g cos) / sin, with sin = (to_next x to_prev) . n (< 0 if reflex)."""
            p_, n_ = (to_prev - here).normalized(), (to_next - here).normalized()
            sn = n_.cross(p_).dot(nrm)
            return 0.0 if abs(sn) < build_cr_bevel.INLINE_SIN else (d_side + dd[g] * p_.dot(n_)) / sn
        shrink = t(a, prv, b, dd[ep]) + t(b, a, nxt, dd[en])
        worst = max(worst, shrink / (b - a).length)
check("subdivided Suzanne, Per Edge: every corner within its overlap limit (t0 + t1 <= 0.95 |g|)",
      0 < worst <= 0.95 * (1 + 1e-5), f"{worst:.6f} of |g|")

# ---------------------------------------------------------------- pass 2: segment parity at corners


def tapered():
    base = [(0, 0), (3, 0), (3.5, 2), (1, 3), (-0.5, 1.5)]
    top = [(1.4 + 0.6 * (x - 1.4), 1.3 + 0.6 * (y - 1.3)) for x, y in base]
    v = [(x, y, 0) for x, y in base] + [(x, y, 1.2) for x, y in top]
    f = [[4, 3, 2, 1, 0], [5, 6, 7, 8, 9]] + [[i, (i + 1) % 5, 5 + (i + 1) % 5, 5 + i] for i in range(5)]
    return A.new_object("tapered", v, f)


for seg in (2, 3, 5):
    run_case(f"skew box corners, {seg} segments", lambda: skew_box(f"par{seg}"), 0.15, segments=seg, exact_r=0.15)
    run_case(f"tapered prism joints+terminals, {seg} segments", tapered, 0.12, segments=seg, exact_r=0.12,
             selection=lambda e: not all(v.co.z < 0.1 for v in e.verts))

# ---------------------------------------------------------------- pass 2: clamp modes agree when nothing clamps

positions = {}
for clamp in ("Uniform", "Per Edge", "None"):
    ob = skew_box(f"agree_{clamp}")
    mod = A.add_cr_bevel(ob, ng, 0.1, 4)
    A.set_input(mod, "Clamp", clamp)
    positions[clamp] = np.array([v.co[:] for v in A.evaluate(ob).vertices])
dev = max(float(np.abs(positions[c] - positions["None"]).max()) for c in ("Uniform", "Per Edge"))
check("Uniform, Per Edge and None agree when nothing needs clamping", dev < 1e-6, f"{dev:.1e}")

# ---------------------------------------------------------------- pass 2: precision far from origin


_, near = run_case("skew box at origin, r=0.01", lambda: skew_box("p_near"), 0.01, exact_r=0.01)
_, far = run_case("skew box 1000 units away, r=0.01", lambda: skew_box("p_far", offset=(1000, 1000, 1000)),
                  0.01, exact_r=0.01)
check("far from origin with a small radius: corners still exact (no silent fallback)",
      far.get("mode_counts") == near.get("mode_counts"), (far.get("mode_counts"), near.get("mode_counts")))

# ---------------------------------------------------------------- pass 2: thin wall and twisted faces


def thin_plate():
    bm = cube_bm()
    bmesh.ops.scale(bm, vec=(1.0, 1.0, 0.04), verts=bm.verts[:])     # 2 x 2 x 0.08 plate
    return from_bm("thin_plate", bm)


me, st = run_case("thin plate (0.08 thick), r=0.2, Uniform", thin_plate, 0.2)
if me is not None:
    z = np.array([v.co.z for v in me.vertices])
    check("thin plate: stays within its thickness", z.min() >= -0.04 - 1e-6 and z.max() <= 0.04 + 1e-6,
          f"{z.min():.4f}..{z.max():.4f}")
run_case("thin plate, r=0.2, Per Edge", thin_plate, 0.2, clamp="Per Edge")


def twisted_box():
    bm = cube_bm()
    for v in bm.verts:
        if v.co.z > 0 and v.co.x > 0 and v.co.y > 0:
            v.co.z += 0.3                       # top face no longer planar
    return from_bm("twisted", bm)


run_case("box with a non-planar (twisted) face", twisted_box, 0.15)

# ---------------------------------------------------------------- pass 2: modifier stacking


def stacked(kind):
    def make():
        ob = from_bm(f"stack_{kind}", cube_bm())
        if kind == "mirror":
            m = ob.modifiers.new("m", "MIRROR")
            m.use_clip = True
            ob.location.x = 0.0
            bm = bmesh.new()
            bm.from_mesh(ob.data)
            bmesh.ops.translate(bm, vec=(1.0, 0, 0), verts=bm.verts[:])   # half on +x, mirrored onto -x
            bm.to_mesh(ob.data)
            bm.free()
        elif kind == "array":
            m = ob.modifiers.new("a", "ARRAY")
            m.count, m.relative_offset_displace = 3, (1.0, 0, 0)          # touching copies, merged
            m.use_merge_vertices = True
        elif kind == "subsurf":
            m = ob.modifiers.new("s", "SUBSURF")
            m.levels = 1
        return ob
    return make


for kind in ("mirror", "array", "subsurf"):
    run_case(f"after a {kind} modifier", stacked(kind), 0.1)

# ---------------------------------------------------------------- pass 2: non-mesh input and data


def gn_instances_object():
    ob = from_bm("inst_src", cube_bm(0.5))
    g = bpy.data.node_groups.new("MakeInstances", "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
    line = g.nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = 3
    inst = g.nodes.new("GeometryNodeInstanceOnPoints")
    curve = g.nodes.new("GeometryNodeCurvePrimitiveCircle")
    join = g.nodes.new("GeometryNodeJoinGeometry")
    g.links.new(line.outputs[0], inst.inputs["Points"])
    g.links.new(gi.outputs[0], inst.inputs["Instance"])
    g.links.new(inst.outputs[0], join.inputs[0])
    g.links.new(curve.outputs[0], join.inputs[0])
    g.links.new(gi.outputs[0], join.inputs[0])
    g.links.new(join.outputs[0], go.inputs[0])
    m = ob.modifiers.new("inst", "NODES")
    m.node_group = g
    return ob


ob = gn_instances_object()
A.add_cr_bevel(ob, ng, 0.05, 2)
try:
    me = A.evaluate(ob)
    check("instances + curve + mesh input: evaluates, mesh part beveled",
          me is not None and "cr_bevel_face" in me.attributes, len(me.vertices))
except Exception as exc:  # pragma: no cover
    check("instances + curve + mesh input: evaluates", False, exc)


def two_materials(ob):
    ob.data.materials.append(bpy.data.materials.new("A"))
    ob.data.materials.append(bpy.data.materials.new("B"))
    for p in ob.data.polygons:
        p.material_index = 1 if p.normal.z > 0.5 else 0


me, _ = run_case("input with two materials", lambda: skew_box("mats"), 0.15, prepare=two_materials, exact_r=0.15)
if me is not None:
    flags = np.zeros(len(me.polygons), bool)
    me.attributes["cr_bevel_face"].data.foreach_get("value", flags)
    tops = [p for p in me.polygons if not flags[p.index] and p.normal.z > 0.5]
    others = [p for p in me.polygons if not flags[p.index] and p.normal.z <= 0.5]
    check("original faces keep their material", all(p.material_index == 1 for p in tops) and
          all(p.material_index == 0 for p in others))


def float_selection(ob):
    a = ob.data.attributes.new("weight", "FLOAT", "EDGE")
    for i, d in enumerate(a.data):
        d.value = 1.0 if i % 2 == 0 else 0.0


ob = skew_box("floatsel")
float_selection(ob)
mod = A.add_cr_bevel(ob, ng, 0.15, 4, angle_limit=0.0, selection_attr="weight")
me = A.evaluate(ob)
check("float attribute works as Selection", "cr_bevel_face" in me.attributes and len(me.vertices) > 8, len(me.vertices))

# ---------------------------------------------------------------- pass 2: concave chains (cup cavity)


def cup():
    """Box with a tapered cavity: concave floor edges, convex rim edges."""
    bm = cube_bm(2.0)
    top = [f for f in bm.faces if f.normal.z > 0.9][0]
    bmesh.ops.inset_region(bm, faces=[top], thickness=0.4)
    ext = bmesh.ops.extrude_face_region(bm, geom=[top])
    verts = [g for g in ext["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, -1.2), verts=verts)
    bmesh.ops.scale(bm, vec=(0.7, 0.8, 1.0), verts=verts)          # tapered walls, non-90 degree edges
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return from_bm("cup", bm)


floor = lambda e: all(abs(v.co.z + 0.2) < 1e-4 for v in e.verts)      # noqa: E731  cavity floor loop
run_case("cup: concave cavity floor loop (concave joints)", cup, 0.1, selection=floor, exact_r=0.1)
run_case("cup: all sharp edges (convex rims, concave corners)", cup, 0.1, exact_r=0.1)

# ---------------------------------------------------------------- pass 2: extremes

ob = A.new_object("empty", [], [])
A.add_cr_bevel(ob, ng, 0.1, 4)
try:
    me = A.evaluate(ob)
    check("empty mesh: evaluates to empty", len(me.vertices) == 0)
except Exception as exc:  # pragma: no cover
    check("empty mesh: evaluates", False, exc)

ob = skew_box("limit180")
A.add_cr_bevel(ob, ng, 0.1, 4, angle_limit=math.pi)
me = A.evaluate(ob)
check("Angle Limit 180 deg: nothing beveled, topology unchanged", len(me.vertices) == 8 and len(me.polygons) == 6)

ob = from_bm("huge_r", cube_bm())
mod = A.add_cr_bevel(ob, ng, 5.0, 4)
A.set_input(mod, "Clamp", "None")
me = A.evaluate(ob)
check("Clamp None with radius 5 on a size-2 cube: overlaps but stays finite",
      bool(np.isfinite(np.array([v.co[:] for v in me.vertices])).all()))

import time  # noqa: E402
ob = skew_box("seg256")
A.add_cr_bevel(ob, ng, 0.15, 256)
t0 = time.perf_counter()
me = A.evaluate(ob)
dt = time.perf_counter() - t0
report["cases"]["256 segments"] = dict(verts=len(me.vertices), seconds=dt)
check("256 segments: evaluates in reasonable time", dt < 20 and len(me.vertices) > 100000, f"{len(me.vertices)} verts, {dt:.1f} s")

# ---------------------------------------------------------------- pass 2: determinism

runs = []
for _ in range(2):
    ob = skew_box("det")
    A.add_cr_bevel(ob, ng, 0.15, 6)
    runs.append(np.array([v.co[:] for v in A.evaluate(ob).vertices]))
check("two evaluations give identical positions", np.array_equal(runs[0], runs[1]))

report["failures"] = failures
with open(os.path.join(OUT, "robustness_results.json"), "w") as fh:
    json.dump(report, fh, indent=2, default=str)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
