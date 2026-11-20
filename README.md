# Blender Constant-Radius Bevel

A Geometry Nodes modifier for Blender 5.2+ that bevels edges with a true constant-radius (rolling-ball) fillet. It is built on the native Mesh Bevel node plus an exact cleanup step that snaps the new vertices onto the true cylinders and spheres.

**Status:** Phase 0 (verification) done. Phase 1 (exact edge strips) is next.

## Layout

| Path | Contents |
| --- | --- |
| `docs/PLAN.md` | Plan: approach, math, node group spec, roadmap |
| `docs/PHASE0_FINDINGS.md` | What the native Mesh Bevel node actually does, with measurements |
| `phase0/` | Headless test harness, test scene and raw results |

## Re-running the Phase 0 tests

```bash
blender -b --factory-startup --python phase0/run_phase0.py
```
