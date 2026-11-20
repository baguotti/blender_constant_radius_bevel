# Blender Constant-Radius Bevel

A Geometry Nodes modifier for Blender 5.2+ that bevels edges with a true constant-radius (rolling-ball) fillet. It is built on the native Mesh Bevel node plus an exact cleanup step that snaps the new vertices onto the true cylinders and spheres.

**Status:** version 1.0.2, an installable extension (after two deep bug passes; see `docs/REVIEW_FINDINGS.md`). Exact bevel geometry and shading, an overlap clamp, and the input's own shading kept on original faces.

## Install (Blender 5.2 or later)

1. Get `cr_bevel-1.0.2.zip`: build it with `blender -b --factory-startup --python src/make_extension.py`, which writes it to `dist/`.
2. In Blender: Edit > Preferences > Get Extensions > the drop-down at the top right > Install from Disk, then pick the zip.
3. Select a mesh, then Add Modifier > Generate > CR Bevel.

**Settings:**
- **Radius** and **Segments.**
- **Angle Limit:** edges sharper than this are beveled. To bevel only certain edges, use an attribute for Selection.
- **Clamp:** Uniform (default) keeps one radius everywhere and lowers it if something would overlap. Per Edge lowers it only where needed, which is best for boolean results. A warning on the modifier tells you when it clamps.
- **Shading panel:** Analytic Normals (exact shading on the bevel; turn it off for about 30% more speed while modelling heavy scenes) and an optional bevel material.
- **Advanced panel:** Exact Profile (off = the native node's profile, for comparison).

**Without installing:** add this repo's `library/` folder under Preferences > File Paths > Asset Libraries; CR Bevel then appears under Add Modifier.

## Layout

| Path | Contents |
| --- | --- |
| `docs/PLAN.md` | Plan: approach, math, node group spec, roadmap |
| `docs/PHASE0_FINDINGS.md` | What the native Mesh Bevel node actually does, with measurements |
| `src/` | Node group builder, asset-library and extension build scripts |
| `extension/` | Extension source (manifest and add-on) |
| `library/` | Built asset library (`cr_bevel_library.blend`) |
| `tests/` | Analytic reference and per-phase tests with results |
| `phase0/` | Phase 0 harness and results (frozen record) |

## Running the tests

```bash
blender -b --factory-startup --python tests/test_phase1.py
blender -b --factory-startup --python tests/test_phase2.py
blender -b --factory-startup --python tests/test_phase3.py
blender -b --factory-startup --python tests/test_phase4.py   # after src/make_extension.py
blender -b --factory-startup --python tests/test_robustness.py
blender -b --factory-startup --python tests/test_oracle.py
```
