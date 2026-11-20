# Phase 2 findings: corners, joints, straight continuations, end caps

- **Build:** Blender 5.2.4 LTS, headless
- **Test radius:** r = 0.15, 6 segments
- **Raw numbers:** `../tests/results/phase2_results.json`
- **Test scene:** `../tests/results/phase2_tests.blend`
- **Re-run:** `blender -b --factory-startup --python tests/test_phase2.py` (exit code 1 on any failure). The Phase 1 suite still passes on the Phase 2 group.

## Verdict

**Done.** Every vertex with a standard layout is now exact: box corners at any angle, chain joints, straight continuations through extra vertices, and bevels that stop at a slanted face. Vertices without an exact answer keep the native geometry, unchanged.

## How the group handles each original vertex

Each vertex touched by a beveled edge gets a mode (attribute `_cr_mode`, internal):

| Mode | When | What happens | Exact? |
| --- | --- | --- | --- |
| TERMINAL | 1 beveled edge, 3 faces | Snap to the cylinder, then slide along the axis back into the end face. The end face is the one most aligned with the edge | Yes |
| PIPE | 2 beveled edges, collinear, same axis | Snap to the shared cylinder | Yes |
| JOINT | 2 beveled edges, axes cross, same sign, 3 faces | Seam vertices go onto the intersection of the two cylinders (closed form: project into the bisecting plane, then scale from the crossing point). Seam ends on an original face keep their native place | Yes, except one seam-end vertex (see limits) |
| CORNER | 3 beveled edges, axes meet in one point, same sign, 3 faces | Corner-patch vertices go onto the ball; strip vertices onto their cylinder | Yes |
| FALLBACK | anything else: 4+ beveled edges, mixed convex/concave, other layouts | Native geometry kept, unchanged | No (native) |

The skeleton's axis segments are cut at the crossing point for JOINT and CORNER, and extended elsewhere. That's what makes the nearest-axis search pick the right cylinder.

## Checks (all pass, limit 1e-5 r unless noted)

| Case | Modes found | Worst error / r |
| --- | --- | --- |
| Cube, all edges | 8 corner | 5.3e-7 |
| Skewed box, all edges | 8 corner | 1.2e-6 |
| Tapered prism, top loop | 5 joint | 1.2e-6 (seam end: 4.3e-2, see limits) |
| Tapered prism, top loop + sides | 5 corner, 5 terminal (slanted ends) | 2.2e-6; end caps stay in the face to 3.9e-10 |
| Slanted end cap | 2 terminal | 7.2e-8; in the face to 3.1e-7 |
| Subdivided cube | 8 corner, 24 pipe | 3.9e-7 |
| Inside corner (block with a corner removed) | 10 corner incl. concave, 9 pipe, 3 fallback | 5.5e-7 |
| Cylinder rim (32 sides) | 32 joint | 4.5e-7 |
| Cylinder (12 sides), all sharp edges | 16 joint, 8 corner | 7.0e-7 |
| L-prism, all edges | 10 corner, 2 fallback (mixed) | 5.5e-7 |
| Pentagonal pyramid | 5 corner, 1 fallback (5-edge apex) | 7.7e-7 |

**Also checked for every case:**
- Fallback vertices don't move at all.
- Topology is identical to the native bevel.
- No face flips, and no NaN or inf.
- Every move stays under 0.4 r compared with native. The largest is 0.19 r, at the pyramid's base corners.
- The node group's vertex modes match an independent Python re-implementation (`tests/analytic.py:classify_vertices`) on every vertex of every case.

**Smoke tests** (no crash, no NaN, same topology, no flips):
- A boolean-cut block with a cylindrical hole, r = 0.05.
- Suzanne, r = 0.005.

## Bugs found and fixed during the review

1. **Wrong bisector.** Read directly on an edge, Normal averages the edge's *vertex* normals, not its two face normals. It was exact on Phase 1's prisms only by symmetry. Fixed by evaluating Normal on the face domain first. This also made the axes meet exactly, which the corner/joint classification depends on.
2. **Joint seams were pushed onto a ball.** That's wrong, because a seam lies on both cylinders. Replaced with the exact cylinder–cylinder intersection.
3. **Degenerate layouts.** Suzanne has folded faces that share two edges, and that produced a "joint" with 2 faces, moved by 3.4 r. JOINT and CORNER now require exactly 3 faces; everything else falls back safely.

## Known limits

- **Joint seam end.** When two beveled edges with different angles meet next to an unbeveled edge, the seam vertex next to that edge can't be on both cylinders with the native topology; the exact shape needs extra vertices. It keeps its native place, which keeps the side faces planar. Error: 4.3% of r in the tapered prism, whose edges differ by about 9°. Zero when the angles are equal.
- **Triangulated or split corners.** A corner or joint with more than 3 faces falls back, even when some faces are coplanar (for example, triangulated caps). Phase 3 can count distinct planes instead of faces.
- **No clamp yet.** Suzanne at r = 0.05 has 237 edges whose bevel is wider than half the neighbouring edge, and that produces 4 flipped faces. That's the Phase 3 clamp's job.
- **Fallback corners are left native**, rather than relaxed as the plan suggested: relaxing a patch whose border is unchanged gains nothing and risks bulges.

## Performance

81,000-face input, all edges, 8 segments, 6.56 M output faces. On this shared machine, timings varied by about ±25% between runs (the native node alone took 11–14 s). CR Bevel ran at 1.1–1.3× the native node's time in the same run.
