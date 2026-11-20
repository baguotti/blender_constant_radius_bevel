# Phase 1 findings: exact edge strips

- **Build:** Blender 5.2.4 LTS, headless
- **Test radius:** r = 0.2, 8 segments unless noted
- **Raw numbers:** `../tests/results/phase1_results.json`
- **Test scene:** `../tests/results/phase1_tests.blend`
- **Re-run:** `blender -b --factory-startup --python tests/test_phase1.py` (exit code 1 on any failure)

## Verdict

**Done.** Bevel strips are exact circular arcs of radius r, checked against Blender's own Fillet Curve node. Corners are still the native Mesh Bevel patches; they are Phase 2.

## What was built

`src/build_cr_bevel.py` generates the "CR Bevel" node group (modifier flag on), in six labelled frames:

1. **Per-edge offset** d = r·tan(φ/2), plus the axis offset.
2. **Store** per-element data in `_cr_*` attributes.
3. **Skeleton:** one axis segment per selected edge, duplicated twice and grouped by each end vertex.
4. **Native Mesh Bevel** with all four offsets = d.
5. **Snap** every strip vertex onto its cylinder: Geometry Proximity on the skeleton, with Group ID = original vertex ID.
6. **Remove** the internal attributes.

**Inputs:**
- Selection (field)
- Radius
- Segments
- Angle Limit (default 30°)
- Exact Profile (on)

**Output attribute:** `cr_bevel_face` (face, bool).

`src/make_library.py` writes `library/cr_bevel_library.blend` with the group marked as an asset.

## Checks (all pass)

| Check | Result | Limit (relative to r) |
| --- | --- | --- |
| Fillet Curve reference really has radius r | 8.5e-7 | 1e-5 |
| 7-corner prism (incl. a concave corner): vertices vs Fillet Curve circles | 9.6e-7 | 1e-5 |
| Prism: no vertex outside the fillet arc's angular span | 5.8e-7 rad | 1e-5 |
| Prism: tangent points coincide with the fillet arc ends | 8.5e-7 | 1e-5 |
| Original faces stay planar | 6.0e-7 | 1e-5 |
| Segments 1, 2, 3, 16 vs Fillet Curve | 1.0e-6 | 1e-5 |
| Angle sweep: 20 single-edge cases, convex and concave, 20°–175° face angle | 4.4e-6 (worst: 20°, float32) | 1e-5 |
| Unselected vertices unchanged | 0 | 1e-7 |
| No `_cr_*` attributes left in the output | 0 | 0 |
| Exact Profile off reproduces the native node (12.1% at 60°) | matches | |
| Robustness: radius 0, empty selection, loose edges, single plane, flat grid | no crash, topology unchanged | |

## Performance (81,000-face input, all edges, 4 cores)

| Segments | Output faces | CR Bevel | Native node alone (Phase 0) |
| --- | --- | --- | --- |
| 1 | 351,000 | 1.0 s | 0.9 s |
| 4 | 2,025,000 | 4.2 s | 4.0 s |
| 8 | 6,561,000 | 12.1 s | 11.2 s |

The exact snap costs about 5–10%.

## Corrections found along the way

- **Edge Angle's Signed Angle is positive for convex edges** in 5.2.4. On a cube it reads +π/2; the manual says the opposite. The builder and `docs/PLAN.md` now use the measured convention.
- **Blender 5.x API change:** Geometry Nodes modifier inputs are no longer ID properties. Set them through `mod.properties.inputs.<Socket_N>.value`, and use `.type = "ATTRIBUTE"` plus `.attribute_name` for attribute inputs. See `tests/analytic.py:set_input`.

## Known limits (by design, for later phases)

- **Corner patches** (3+ beveled edges at a vertex) are the native ones. The strip vertices next to them are snapped, the patch interiors are not. Phase 2.
- **Chain joints and end caps** are exact only when the end face is perpendicular to the edge, as in all Phase 1 tests. General end caps are Phase 2.
- **No clamp yet:** radii too large for the geometry overlap. Phase 3.
- **Internal names:** the `_cr_` attribute prefix is reserved. Input attributes with that prefix are overwritten and removed.
