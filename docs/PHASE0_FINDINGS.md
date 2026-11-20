# Phase 0 findings: Mesh Bevel node as the base for a constant-radius bevel

- **Build:** Blender 5.2.4 LTS (hash cd5dc428cce1, built 2026-11-10), headless, 4 CPU cores
- **Test radius:** r = 0.2, 8 segments, shape 0.5
- **Raw numbers:** `../phase0/results/phase0_results.json`
- **Test scene:** `../phase0/results/phase0_tests.blend`. Every test object has the probe modifier.
- **Re-run:** `blender -b --factory-startup --python phase0/run_phase0.py`

## Verdict

**Go.** The node keeps exactly the offsets we give it, so constant-radius offsets work as designed. What's left is fixing the profile shape and the corners afterwards, which was the plan.

## Answers to the checklist

| Question | Answer | Evidence |
| --- | --- | --- |
| Are offsets kept exactly (no hidden width-evening or clamp)? | **Yes.** Error below 1e-6 relative in every test, including chains with different angles and unbeveled edges in between | `outer_offset_rel_err_max` = 0.0 in all tests |
| Is shape 0.5 a true circular arc? | **Only at 90°.** Otherwise it is the stretched quarter circle. The errors match the plan's estimates | See table below |
| Where do corner boundary vertices land, including with unbeveled edges in between? | Exactly where the offset lines cross. No loop-slide drift | Tapered prism top loop (5 edges, φ 58–67°) and L-shape: offsets exact |
| Do attributes survive the bevel? | **Point attributes: yes.** New vertices copy the value of the original vertex they came from (not interpolated). **Edge attributes: no.** New edges get 0. **Face attributes:** new strip and corner faces copy one neighbouring original face's value | `attribute_survival_cube` |
| Offset domain and Left/Right | Edge-domain fields work. Left/Right orientation doesn't matter for us, because all four offsets are equal | All tests |
| Performance | Fine for a modifier at moderate segment counts | Table below |
| Modifier asset | `node_group.is_modifier = True` plus `asset_mark()` both work. The Add Modifier menu still needs checking in the UI | `phase0/asset_check.py` |

### Native profile error vs a true arc (worst vertex, relative to r)

| Angle between faces | φ | Max error | Mean error |
| --- | --- | --- | --- |
| 30° | 150° | 75.2% | 37.4% |
| 45° | 135° | 30.7% | 14.8% |
| 60° | 120° | 12.1% | 5.7% |
| 90° | 90° | 0 | 0 |
| 120° | 60° | 1.44% | 0.67% |
| 135° | 45° | 1.05% | 0.49% |
| 150° | 30° | 0.53% | 0.25% |
| 165° | 15° | 0.14% | 0.07% |

### Shapes

| Test | Strip max error | Corner max error | Notes |
| --- | --- | --- | --- |
| Cube, all edges | 0 | 0 | The 90° corner special case lands exactly on the sphere |
| Cube, single edge (terminal ends) | 0 | n/a | End caps fine at 90° |
| L-shape, concave inner edge only | 0 | n/a | Concave works with the same formula, s = −1 |
| Skewed box, all edges | 4.5% | 5.7% | Fixable: three edges meet at every corner, so exact centres exist |
| Tapered prism, top loop (chain) | 2.8% | n/a | Fixable with the cylinder snap plus the chain-joint rule |
| L-shape, all edges (mixed corners) | 41% (one vertex), mean 0.9% | 0 where an exact centre exists | 74 corner verts at mixed convex/concave corners have no single sphere, so they get the fallback |
| Pentagonal pyramid, all edges (5-edge apex) | 15.7% | 19.7% | The 5-edge apex has no single sphere, so it gets the fallback |

### Performance (81,000-face input: 13,500 cubes, all edges)

| Segments | Output faces | Time |
| --- | --- | --- |
| 1 | 351,000 | 0.88 s |
| 4 | 2,025,000 | 4.0 s |
| 8 | 6,561,000 | 11.2 s |

## Consequences for Phase 1

1. **Skip the node's clamp.** It doesn't touch our offsets, so our own clamp is the only one, and the planned regression check stays in.
2. **Corner snap by direct lookup.** Corner-patch vertices carry the original vertex ID, so each corner's sphere centre can be fetched by index. No proximity search is needed.
3. **Strip snap.** Edge IDs don't survive. A strip vertex still carries its end vertex's ID, though. So use Geometry Proximity on the skeleton axes with Group ID = end vertex ID: there are at most three candidate axes per vertex, and it can't snap to the wrong edge.
4. **The snap matters on sharp edges.** It is most needed below about a 90° face angle, where the native error passes 12% of r.
5. **Fallback corners.** Mixed convex/concave corners and corners with 4+ edges (the pyramid apex) need the fallback, as planned.

## Limits of this test

- The measured error uses the nearest axis or sphere, so it slightly understates errors at chain joints.
- Shading and the Add Modifier menu were not checked visually.
- One build only: 5.2.4 LTS. 5.3 is not tested.
