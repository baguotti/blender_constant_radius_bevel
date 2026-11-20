# CLAUDE.md

Constant-radius bevel for Blender, built as a Geometry Nodes modifier on top of the native Mesh Bevel node.

## Start here
- `docs/PLAN.md`: approach, math, node group spec, roadmap. It is the source of truth.
- `docs/PHASE0_FINDINGS.md`: measured behaviour of the Mesh Bevel node (Blender 5.2.4 LTS).

## Environment
- Blender 5.2.4 LTS is on PATH as `blender`; the cloud environment's setup script installs it. If it is missing, install it by hand:
  download `https://download.blender.org/release/Blender5.2/blender-5.2.4-linux-x64.tar.xz` into `/opt/blender` and link it to `/usr/local/bin/blender`.
- Run everything headless: `blender -b --factory-startup --python <script>`.
- Minimum target is 5.2 LTS. Build node groups in 5.2 so they also open in later versions.

## Workflow
- Work one roadmap phase at a time. Each phase must pass the test that defines "done" in `docs/PLAN.md` before the next starts.
- Build node groups from Python scripts that live in the repo, so every `.blend` can be regenerated. Never hand-edit a `.blend` without the script that made it.
- Every phase gets a headless test script that prints measurable numbers (error relative to r, timing) and writes JSON to `<phase>/results/`.
- After a phase:
    - update `docs/PLAN.md` (status, roadmap row)
    - write `docs/PHASEn_FINDINGS.md`
    - commit to `main`
- The user checks visual results (shading, the Add Modifier menu) in their own Blender. Send them the `.blend` for this.
- Don't commit `*.blend1` or `__pycache__/`.
