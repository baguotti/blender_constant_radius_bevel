# Blender Constant-Radius Bevel

A Geometry Nodes modifier for Blender 5.2+ that bevels edges with a true constant-radius (rolling-ball) fillet. It is built on the native Mesh Bevel node plus an exact cleanup step that snaps the new vertices onto the true cylinders and spheres.

**Status:** Phase 3 done: production-ready. Exact bevel geometry and shading, an overlap clamp, and the input's own shading kept on original faces.

## Try it

1. Add this repo's `library/` folder under Preferences > File Paths > Asset Libraries.
2. Select a mesh, then Add Modifier > CR Bevel.
3. Set Radius and Segments. Edges sharper than Angle Limit are beveled; to bevel only certain edges, use an attribute for Selection.
4. Clamp: Uniform (default) keeps one radius everywhere and lowers it if something would overlap; Per Edge lowers it only where needed (best for boolean results). A warning on the modifier tells you when it clamps.

## Layout

| Path | Contents |
| --- | --- |
| `docs/PLAN.md` | Plan: approach, math, node group spec, roadmap |
| `docs/PHASE0_FINDINGS.md` | What the native Mesh Bevel node actually does, with measurements |
| `src/` | Node group builder and asset-library script |
| `library/` | Built asset library (`cr_bevel_library.blend`) |
| `tests/` | Analytic reference and per-phase tests with results |
| `phase0/` | Phase 0 harness and results (frozen record) |

## Running the tests

```bash
blender -b --factory-startup --python tests/test_phase1.py
blender -b --factory-startup --python tests/test_phase2.py
blender -b --factory-startup --python tests/test_phase3.py
```
