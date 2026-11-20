# Deep bug passes (after Phase 4)

## Pass 1 — version 1.0.1

New suite `tests/test_robustness.py` throws awkward inputs at the modifier. Every case must:
- not crash, and produce no NaN or inf;
- have the same topology as the native bevel and flip no face against it;
- where it should be exact, stay exact.

All phase suites, the robustness suite and the extension install test pass.

## Bugs found and fixed

| # | Input | Symptom | Cause | Fix |
| --- | --- | --- | --- | --- |
| 1 | Mesh with custom normals (weighted normals, CAD imports) | Error of 2.3 r; bevel faces wound the wrong way | The Normal node returns custom normals, even on faces. The native bevel also orients its new faces by them | Use the Normal node's "True Normal" output everywhere, and drop the input's custom normals before beveling (smooth/flat flags and sharp edges are passed through) |
| 2 | One face flipped (inconsistent winding) | Wrong shape (deviation 0.35) | Edge Angle then measures 180° − φ, so the offset is wrong | Such edges are detected (both faces run along the edge the same way), skipped, and a warning says "Recalculate normals". A fully inside-out mesh is consistent and stays exact |
| 3 | Knife edge, 2° between the faces | Flipped and degenerate faces | The exact arc becomes a sliver below float precision (it breaks at φ = 178°) | Edges sharper than 4° between faces (φ > 176°) are not beveled; 3° and up are verified clean |
| 4 | Selection containing a linked object | Operator error | Linked objects can't take modifiers | Skipped, and reported in the message |
| 5 | Test harness | False "flips" on large meshes | The Mesh Bevel node keeps vertex order between runs, but not face order | `count_flips` matches faces by their vertices; all suites use it |

## Checked and fine

| Input | Result |
| --- | --- |
| Object far from the origin (1000 units) | Same vertex modes; error at float32 precision (5e-4 r, i.e. 8e-5 absolute) |
| Tiny (×0.001) and huge (×1000) scale | Exact (about 1e-6 r) |
| Inside-out mesh | Same shape as with correct normals (5e-7) |
| Triangulated cube, open box (boundary edges), zero-length edge, loose vertex and edge | No crash, no NaN, no flips |
| Dense UV sphere at Angle Limit 0 | No flips |
| 1 and 64 segments; partial selection with Per Edge | Exact |
| Input already has a `cr_bevel_face` attribute | Recomputed, not inherited |
| Input smooth/flat flags | Survive |

## Behaviour to know

- **Input custom normals are replaced.** With Analytic Normals off, the result shades from the input's smooth/flat flags and sharp edges. Custom normals can't carry over a bevel in any meaningful way.
- **Native node quirk.** With Exact Profile off, the native Mesh Bevel node bends its profile according to the input's custom normals. This doesn't affect CR Bevel, because custom normals are now dropped first; it may be worth reporting upstream.
- **Object scale.** Radius is in the object's local units, as with Blender's Bevel modifier. With non-uniform object scale, the fillet isn't circular in world space; apply the scale first.

## Pass 2 — version 1.0.2

### New angles tried

Added to `tests/test_robustness.py`:
- **Segment parity:** 2, 3 and 5 segments at corners, joints and terminals. Odd counts put an n-gon at the corner's centre.
- **Clamp modes:** Uniform, Per Edge and None agree exactly when nothing needs clamping.
- **Far from the origin with a small radius** (1000 units, r = 0.01).
- **Thin plate** (0.08 thick, r = 0.2).
- **A twisted (non-planar) face.**
- **Stacking:** after Mirror, Array (merged) and Subdivision.
- **Mixed input:** instances, a curve and a mesh together.
- **Two materials on the input.**
- **A float attribute used as Selection.**
- **A cup cavity:** concave joints at non-90° angles.
- **Extremes:** empty mesh, Angle Limit 180°, Clamp None with r far too large, 256 segments.
- **Determinism:** two evaluations give identical positions.

New independent oracle, `tests/test_oracle.py`. For a convex solid with every edge rounded, the exact result is the solid shrunk by r and grown back by r. So every output vertex must lie exactly r from the shrunk solid, and its normal must point straight away from it. The distance is computed with Dykstra's projection, which shares nothing with the node's code. Cube, skewed box, tapered prism, 12-sided cylinder and pentagonal prism, at 3 and 6 segments: distance exact to 1.6e-6 r.

### Bugs found and fixed

| # | Input | Symptom | Cause | Fix |
| --- | --- | --- | --- | --- |
| 1 | Object far from the origin with a small radius (1000 units, r = 0.01) | Every corner silently fell back to native | Axis positions carry float32 error proportional to the distance from the origin (about 1e-7 × \|position\|, from face normals); the "axes meet" tolerance was 1e-3 r only | Geometric tolerance is now max(1e-3·r, 1e-6·\|position\|). Radius comparisons stay relative to r. Same rule in the Python reference |
| 2 | Flipped faces outside the Selection | Warning shown for edges the user never selected | Count ignored Selection | Counts only selected edges |
| 3 | Tests | "0 rad" normal errors were rounding, not precision | `acos` rounds angles below about 1e-4 rad to 0 | `angle_between` uses atan2. True figure: about 5e-5 rad, the quantization of Blender's tangent-space normal storage (0.003°, invisible) |

### Usability

The modifier panel now shows Selection, Radius, Segments, Angle Limit and Clamp. Shading (Analytic Normals, bevel material) and Advanced (Exact Profile) are collapsed panels. Socket names are unchanged.

### Checked and fine

Everything listed above passes: no NaN, no flips, exact where expected. Instances pass through unbevelled; only the mesh part is beveled.
