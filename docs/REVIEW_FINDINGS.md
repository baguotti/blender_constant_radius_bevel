# Deep bug pass (after Phase 4) — version 1.0.1

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
