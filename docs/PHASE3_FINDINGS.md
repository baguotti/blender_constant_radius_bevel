# Phase 3 findings: clamp, exact normals, material, production checks

- **Build:** Blender 5.2.4 LTS, headless
- **Raw numbers:** `../tests/results/phase3_results.json`
- **Renders:** `../tests/results/phase3_zebra.png`, `../tests/results/phase3_closeup.png` (made by `tests/render_phase3.py`)
- **Re-run:**
    - `blender -b --factory-startup --python tests/test_phase3.py`
    - `blender -b --factory-startup --python tests/render_phase3.py`
- The Phase 1 and Phase 2 suites still pass on the Phase 3 group.

## Verdict

**Done.** The modifier is safe on messy meshes: no overlaps, no spikes, no flipped faces. It shades exactly (normals match the true cylinders and spheres) and keeps the input's own smooth/flat shading on original faces. The renders show clean, evenly spaced zebra stripes and true spherical corners, where the native bevel wobbles.

## New inputs

| Input | Default | What it does |
| --- | --- | --- |
| Clamp | Uniform | Uniform: lowers the radius everywhere just enough that nothing overlaps, so it stays constant. Per Edge: lowers it only where needed. None: no limit |
| Analytic Normals | On | Exact normals on the bevel, written as tangent-space custom normals, so they survive deforming modifiers after this one |
| Set Bevel Material, Bevel Material | Off, none | Material for the new bevel faces |

A **Warning** appears on the modifier when the clamp lowers the radius, for example "12 edge(s) clamped to avoid overlaps; smallest radius used: 0.9500".

## The clamp

**Rule (exact for the native construction).** In every face, the bevel's boundary points sit where offset lines cross. Along each edge g of the face they move inwards by:
- t0 = (d_prev + d_g·cos a) / sin a at one corner
- t1 = (d_next + d_g·cos b) / sin b at the other

Here a and b are the face's interior angles, and unbeveled edges have d = 0. The bevel overlaps when t0 + t1 > |g|. Reflex corners are included (sin < 0). Corners within 2° of straight are skipped, because the native bevel's in-line case places those boundary points differently.

**Uniform.** Every offset is r·tan(φ/2), so each corner gives r ≤ |g| / (t0 + t1) per unit radius. The radius used is 0.95 × the smallest limit.

**Per Edge.** Each edge starts from its own limit. A Repeat zone (32 passes, skipped unless Per Edge is chosen) then checks every corner against the actual, unequal offsets. When a corner is violated, its three edges get their smallest radius, capped at the corner's uniform limit. Pieces of one straight edge also share the smaller radius. The radii never drop below the Uniform result. Different radii meeting at a corner can't share a ball, so such corners fall back to native geometry.

## Checks (all pass)

| Check | Result |
| --- | --- |
| Cube (size 2), r = 1.5, Uniform and Per Edge: radius used, exactness at it | 0.95 (exact limit 1.0 × 0.95); exact to 4.8e-8 r; warning text correct |
| Suzanne, r = 0.05 (had 4 flips in Phase 2) | Uniform and Per Edge: 0 inverted faces, 0 flips vs native. Clamp None: inverted faces appear, so the clamp is needed |
| Boolean-cut block (n-gons with reflex corners around the hole), r = 0.3 and 0.1, Uniform and Per Edge | Nothing leaves the original bounding box (Per Edge used to make 5-unit spikes); 0 inverted faces, 0 flips |
| Cylinder rim, r = 0.15 | Not clamped, no warning |
| Normals on cube, skewed box, concave notch | Bevel corners match the exact normal to 0 rad (float precision); original faces exactly flat |
| Smooth-shaded input (cylinder sides) | Stays smooth |
| Analytic Normals off | No custom normals written |
| Bevel material | Exactly the bevel faces get it |
| UV map | Kept, all values finite |
| Twist modifier after CR Bevel | Evaluates; custom normals kept |

## Bugs found by looking at the renders (the numeric tests had passed)

1. **Spikes up to 5 units on the boolean block with Per Edge.** My first clamp skipped reflex and near-straight face corners, which is exactly where unequal offsets push a boundary point far away. Fixed by the exact rule above (reflex corners included), plus the Per Edge passes that keep each corner valid with the real offsets.
2. **Smooth surfaces were flattened.** Original faces are now shaded as in the input.
3. **Zig-zag shading on long bevel strips at fallback corners.** The fallback normals averaged in the corner patch, which tilted them along the edge. Strip vertices now average only their strip faces. A first attempt used the nearest axis instead and picked the wrong edge, because axes aren't cut at fallback corners; it was reverted.

Two earlier drafts of the clamp were also replaced after the tests caught them:
- "Half the shortest neighbouring edge" was far too strict: it cut a 32-sided cylinder rim to two thirds of the requested radius.
- Exactly-straight corners counted as unbounded, which drove some Per Edge radii to 0.

## Known limits

- **Uniform can shrink a lot on messy meshes.** One tight corner limits the whole object, for example 0.0155 on the boolean block. The warning says so; Per Edge is the practical choice there.
- **Per Edge loses the exact ball** wherever edges with different radii meet at a corner, so those corners fall back to native. Their shading is still clean.
- **Joint seam normals** use the average of the two cylinder normals: the exact surface has a slight crease there.
- **Still open from the plan:** corners and joints at vertices with more than 3 faces (counting planes), the joint seam end, and non-planar faces.

## Performance

81,000-face input, all edges, Uniform clamp. Timings on this shared machine vary by about ±25% between runs.

| Configuration | 4 segments (2.0 M faces out) | 8 segments (6.6 M faces out) |
| --- | --- | --- |
| Native Mesh Bevel node alone | — | 11.5 s |
| CR Bevel, all on (default) | 7.3 s | 21 s |
| CR Bevel, Analytic Normals off | 5.0 s | — |
| CR Bevel, Clamp None | 7.0 s | — |

**Where the time goes:**
- **Tangent-space custom normals: about 2.1 s** at 4 segments. Free-mode normals would take 0.4 s, but wouldn't follow a deforming modifier placed after this one, for example an armature on a rigged prop. So tangent space stays.
- **The clamp: about 0.3 s.**
- **The exact snap: under 0.5 s.**

**Tip:** turn Analytic Normals off while modelling heavy scenes (about 30% faster), and back on for final shading.
