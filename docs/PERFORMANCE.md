# Performance

Measure with `blender -b --factory-startup --python tests/profile_cr_bevel.py` (add `-- quick` for small meshes). It writes `tests/results/profile.json`: total time per configuration, and cumulative time with the group cut after each stage. Timings are the best of 3 runs; this shared machine varies by about ±25% between runs, so compare runs made back to back.

## Optimization pass (v1.0.6)

Same machine, back to back, 4 segments, radius 0.1.

| Model | Configuration | v1.0.5 | v1.0.6 | Speed-up |
| --- | --- | --- | --- | --- |
| Cube grid, 81,000 faces, all edges (2.0 M faces out) | Native Mesh Bevel node alone | 3.7 s | 3.6 s | — |
| | CR Bevel default | 7.1 s | 6.7 s | 1.07× |
| | Clamp Per Edge | 25.8 s | 7.7 s | **3.4×** |
| | Analytic Normals off | 5.1 s | 4.3 s | 1.2× |
| | Everything before the bevel (stages 0–5) | 0.56 s | 0.21 s | 2.7× |
| Subdivided Suzanne, 31,000 faces, angle limit 30° | CR Bevel default | 0.30 s | 0.10 s | **3×** |
| | Clamp Per Edge | 8.1 s | 0.35 s | **23×** |

**Same output.** Old and new builders compared vertex by vertex (positions, corner normals, warnings, attributes) on a subdivided Suzanne, a boolean block, a skewed prism and a cube with a polyline, for every Clamp mode with Analytic Normals on and off: bit-identical, except Per Edge on Suzanne (below). All test suites pass with the same tolerances.

**One fix found on the way (Per Edge).** A corner the loop has just equalized sits exactly on its limit (k = 0.95·|g|). Whether "k > limit" then holds was decided by float rounding, and a false "violated" lowered its neighbours for nothing; the old and new formulas round differently, so 18 of Suzanne's 236 edges came out different (some larger, some smaller). The test now counts a corner as violated only when it is more than 1e-6 over its limit. Result: 50 edges keep a larger radius than before, none gets a smaller one, and the overlap margin is unchanged (5% below the point where bevels touch).

### What changed

1. **Per Edge clamp loop.** Its 32 passes recomputed every invariant (corner angles, edge lengths, neighbour lookups, the Uniform limit) in every pass. They are now stored on the corners once, so the shrink along an edge is `k = wp·r_prev + wg·r_g + wn·r_next` and a pass is only lookups. The loop's intermediate value is stored rather than re-read through Field at Index three times. The setup is built only when Per Edge is chosen (a lazy Geometry Switch), and its attributes are removed before the bevel so they don't ride through it.
2. **One evaluation per group of attributes.** Separate Store Named Attribute nodes each evaluate their field on their own, so the 8 per-vertex values repeated the shared work (edge sorting at each vertex, axis crossings) 8 times. A single Capture Attribute with several items evaluates them together (`Builder.store_many`).
3. **Only where needed.** Vertex classification runs only on vertices of beveled edges (Capture Attribute's Selection); the snap, with its skeleton proximity queries, only on points of bevel faces. Set Position reads the stored result instead of re-evaluating its selection on every point.
4. **No wires, no wire work.** A mesh without loose edges skips the wire split, the join and the merge.

### Where the time goes now (cube grid)

| Part | Time |
| --- | --- |
| Native Mesh Bevel | about 3.7 s |
| Tangent-space custom normals (Blender's encoding) | about 2.4 s |
| Everything else of ours: clamp, classification, skeleton, snap, normal fields | about 0.5 s |

The custom-normal encoding itself (Set Mesh Normal in Tangent Space) is the largest part we add: with a constant normal it costs the same, so our normal fields are only about 0.1 s. Free mode would take about 0.1 s instead, but its normals don't follow a deforming modifier placed after CR Bevel (Phase 3 decision).

On models where only some edges are sharp enough to bevel, our share is small: the Suzanne case runs close to the cost of the bevel itself.
