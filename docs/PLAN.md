# Constant-Radius Bevel for Blender: Plan

As of 2026-11-20. Shared copy: https://claude.ai/artifact/UhqSfuLbSPwaLLuNvdxvZy. This file is the version in the repo.

## Goal

Build a reusable Geometry Nodes group, "CR Bevel", that bevels selected mesh edges with a true constant-radius (rolling-ball) fillet. It should be addable as a modifier in any project.

- **Target:** Blender 5.2 LTS minimum, because the Mesh Bevel node first shipped there. Build in 5.2 so the file also opens in 5.3.
- **Deliverable:** a .blend asset library with the group flagged as a modifier. A thin extension (add-on) is optional.
- **Status:** v1.0.5: polyline and open-mesh fixes (bug pass 8). v1.0.4: open meshes and polylines. v1.0.3 after seven bug passes ([REVIEW_FINDINGS.md](REVIEW_FINDINGS.md)). Phase 4 done: installable extension. Phase 3: production-ready. Exact geometry and normals, overlap clamp, input shading kept. See [PHASE0](PHASE0_FINDINGS.md), [PHASE1](PHASE1_FINDINGS.md), [PHASE2](PHASE2_FINDINGS.md) [PHASE3](PHASE3_FINDINGS.md) and [PHASE4](PHASE4_FINDINGS.md) findings.

## Background: why the 2019 attempt failed

The [2019 project](https://forum.blender.org/t/fall-2019-bevel-constant-radius-project/9872) by Hans Goudey added a Radius mode inside the old C bevel code ([D6386](https://archive.blender.org/developer/differential/0006/0006386/index.html)). It worked in 2D and was abandoned in June 2022.

The review listed these failures:
- the width-evening code (`adjust_offsets`) fought the radius
- end points of bevels that stop at an unbeveled edge were wrong
- corner fills came out the wrong shape
- mixed radii and miters were broken

The [Bevel V2 design](https://archive.blender.org/developer/maniphest/0098/0098674/index.html) lists Constant radius as wanted, but the shipped node doesn't have it.

**Lesson:** separate topology (native node) from exact shape (our cleanup step).

## Building blocks (Blender 5.2+)

| Node | Use |
| --- | --- |
| Mesh Bevel | Topology. Start/End × Left/Right perpendicular offsets as edge fields. Outputs Vertex Face, Edge Face, Outer Edge, Mid Edge. No width type, clamp, loop slide or hardening |
| Edge Angle | Signed Angle: convex positive, concave negative (measured in 5.2.4; the manual says the opposite) |
| Geometry Proximity | Closest point on the skeleton axes, with a Group ID |
| Sample Index | Direct lookup of corner centres by original vertex ID |
| Set Mesh Normal | Exact custom normals |
| SDF Grid Offset | Optional volume "Round All" mode |
| Fillet Curve | Ground truth for tests (true arcs) |

## Math

Notation: n1, n2 = the edge's face normals; φ = angle between them = |Signed Angle|; s = +1 convex, −1 concave; r = radius; E = a point on the edge.

1. **Offset into each face** (all four node inputs): `d = r·tan(φ/2)`.
2. **Cylinder axis:** `A = E − s · r/cos(φ/2) · normalize(n1+n2)`.
3. **Strip snap:** Q = closest point on the axis to P; `P' = Q + r·normalize(P − Q)`.
4. **Corner ball** (3 beveled edges, same sign, same r, 3 faces): C solves `n_i·C = n_i·V − s·r` (it's where the axes meet). Corner-patch vertices use the same rule as strips: Q = nearest point on the axes cut at C, `P' = Q + r·normalize(P − Q)`. Q is C in the ball's region and a point on an axis next to a cylinder. That's exactly "shrink by r, grow by r", and it is robust when the native patch is skewed (very unequal offsets).
5. **Chain joint** (2 beveled edges at a 3-face vertex): the axes cross in one offset plane. Seam vertices go onto the cylinder–cylinder intersection. Only when both cylinders touch the third (unbeveled) edge within r of each other (t_k = d_k / |g × u_k|), otherwise the seam can't end well: fallback.
6. **Terminal end:** snap in the cross-section, then slide along the axis back into the end face's plane.
7. **Normals:** (P' − Q)/r on strips, (P' − C)/r on corners.

## Node group spec

**Inputs:**
- Geometry
- Selection (bool edge field)
- Radius (float field)
- Segments
- Clamp (None / Per-edge / Uniform)
- Exact Profile, Exact Corners, Analytic Normals
- Bevel Material

**Stages:**
1. **Pre-pass.** Keep manifold edges with a usable angle, compute d, clamp.
2. **Skeleton.** Axes cut at ball centres and chain crossings, carrying r and the end-vertex group IDs.
3. **Mesh Bevel** with all four offsets = d, shape 0.5, miters off.
4. **Snap.**
    - Strips: proximity to the axes, grouped by the end vertex's original ID (point attributes survive the bevel; edge attributes don't).
    - Corners: Sample Index of the centre by original vertex ID.
    - End caps: kept in the end face's plane.
    - Outer edges: stay fixed.
5. **Custom normals** on the bevel faces.
6. **Outputs:** mesh, `cr_bevel_face`, `cr_clamped`, `cr_radius`, Warning node.

## Corner cases

| Case | Handling | Exact? |
| --- | --- | --- |
| Strip interior | Cylinder snap | Yes |
| 3 edges, same sign, same r | Sphere | Yes |
| Chain joint (3 faces) | Cylinder–cylinder intersection; the seam end next to an unbeveled edge stays native | Yes, except that one vertex when the angles differ |
| Terminal end | Cylinder ∩ end face | Yes |
| Terminal end at an open border (2 faces) | Cylinder ∩ the plane of the two border edges (the missing face) | Yes |
| Polyline corners (wire edges) | Fillet Curve: circular arcs of radius r | Yes |
| Concave edge | s = −1 | Yes |
| 4+ edges, mixed convex/concave, >3 faces at a corner/joint | Native geometry kept unchanged (FALLBACK) | No |
| Knife edge (φ near 180°), overlap | Clamp, flag `cr_clamped` | Clamped |
| Non-planar faces | Separate start/end offsets | Approx. |

## Roadmap

| Phase | Work | Done when |
| --- | --- | --- |
| 0. Verify | Node behaviour, profile error, attributes, speed | **Done:** see the findings |
| 1. Edges | Offsets, skeleton, exact strip snap | **Done:** matches Fillet Curve to 1e-6 r |
| 2. Corners | Spheres, chain joints, end caps, fallback | **Done:** 11 cases exact to ~1e-6 r; fallback untouched |
| 3. Production | Clamp, normals, material, UVs, warnings | **Done:** clean zebra renders; no overlaps on Suzanne or a boolean block |
| 4. Packaging | Asset library, optional extension | **Done:** extension installs, enables and works in a clean 5.2 LTS |
| 5. Optional | Corners, joints and bevel ends at vertices with >3 faces (count planes); joint seam end; thin-wall overlaps (global offset); non-planar faces; volume Round All | Each case exact or measurably better |

## Open decisions

- Minimum version 5.2 LTS?
- Per-edge radius, or one global radius?
- Clamp default: Uniform or Per-edge?
- Asset library only, or also the extension?
