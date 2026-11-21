# CLAUDE.md

Constant-radius bevel for Blender, built as a Geometry Nodes modifier on top of the native Mesh Bevel node.

## Start here
- `docs/PLAN.md`: approach, math, node group spec, roadmap. It is the source of truth.
- `docs/PHASE0_FINDINGS.md`, `docs/PHASE1_FINDINGS.md`, ...: measured results of each phase.
- `docs/REVIEW_FINDINGS.md`: deep bug pass (robustness).
- `docs/PERFORMANCE.md`: timings and what makes the node group fast.

## Environment
- Blender 5.2.4 LTS is on PATH as `blender`; the cloud environment's setup script installs it. If it is missing, install it by hand:
  download `https://download.blender.org/release/Blender5.2/blender-5.2.4-linux-x64.tar.xz` into `/opt/blender` and link it to `/usr/local/bin/blender`.
- Run everything headless: `blender -b --factory-startup --python <script>`.
- Minimum target is 5.2 LTS. Build node groups in 5.2 so they also open in later versions.

## Commands
- Build the asset library: `blender -b --factory-startup --python src/make_library.py`
- Build the extension zip (dist/, not committed): `blender -b --factory-startup --python src/make_extension.py`
- Tests (exit code 1 on failure); run all of them after any change to the builder:
    - `blender -b --factory-startup --python tests/test_phase1.py`
    - `blender -b --factory-startup --python tests/test_phase2.py`
    - `blender -b --factory-startup --python tests/test_phase3.py`
    - `blender -b --factory-startup --python tests/test_phase4.py` (needs the extension zip)
    - `blender -b --factory-startup --python tests/test_robustness.py` (awkward inputs; see docs/REVIEW_FINDINGS.md)
    - `blender -b --factory-startup --python tests/test_oracle.py` (independent check: convex solids and a concave cavity vs the eroded-solid distance)
    - `blender -b --factory-startup --python tests/test_fuzz.py` (random shapes, fixed seeds)
    - `blender -b --factory-startup --python tests/test_lifecycle.py` (save/reopen, apply, stacking, keyframes, linking, Edit Mode)
- Performance profile (per configuration and per stage; see docs/PERFORMANCE.md): `blender -b --factory-startup --python tests/profile_cr_bevel.py` (`-- quick` for small meshes)
- Shading renders (look at them after visual changes): `blender -b --factory-startup --python tests/render_phase3.py`
- Phase 0 record (frozen): `blender -b --factory-startup --python phase0/run_phase0.py`

## Code layout
- `src/build_cr_bevel.py`: the only place the node group is defined
- `extension/`: add-on source; bump `version` in `blender_manifest.toml` for each release
- `tests/analytic.py`: shared reference geometry and measurements
- `tests/test_phaseN.py`: one test script per phase

## Blender API notes (5.2)
- Modifier inputs: `mod.properties.inputs.<Socket_N>.value`; for an attribute input, set `.type = "ATTRIBUTE"` and `.attribute_name`.
- Edge Angle's Signed Angle is positive for convex edges (the manual says the opposite).
- Use the Normal node's "True Normal" output for geometry: "Normal" returns custom normals when the input has them. Read on an edge it averages vertex normals; use Field on Domain (Face) for the two face normals.
- Compare two results face by face with `analytic.count_flips` (face order is not stable between runs).
- Every check that measures a set must also require that set to be non-empty (a check once passed while measuring nothing).
- Measure small angles with `analytic.angle_between` (atan2), never `acos`/`Vector.angle`: they round tiny angles to 0.
- Tangent-space custom normals are stored quantized (~5e-5 rad): normal limits below 1e-4 can't pass.
- A menu group input can feed only ONE Menu Switch node; derive other values from its output (see `_menu`).
- Separate Store Named Attribute nodes each evaluate their field on their own: store several values with `Builder.store_many` (one Capture Attribute), and pass a Selection when only some elements need them.
- Geometry Switch with a single-value condition is lazy: use it to skip whole branches (e.g. the Per Edge loop).
- Named Attribute's "Exists" output is a field at runtime: a Switch or Warning fed from it fails ("Type cannot be switched by a field"). Count with Attribute Statistic instead.
- Reading the object's own transform (Self Object > Object Info, or a driver) makes Blender re-evaluate the modifier on every move, even when the value isn't used: keep such nodes out of the default group. `build(scene_units=True)` makes the separate "CR Bevel (Scene Units)" group; it scales back BEFORE writing custom normals (tangent-space normals don't survive a non-uniform scale) and maps exact normals with `normal_to_object`.
- Warning node only shows if its output is used: route geometry through a Switch on it.
- A multi-input socket (String Join) stacks new links on top: link parts in reverse order.
- The vertex modes (FALLBACK/TERMINAL/PIPE/JOINT/CORNER) are described in the builder's docstring; `tests/analytic.py` re-implements them independently and the tests compare both.

## Workflow
- Work one roadmap phase at a time. Each phase must pass the test that defines "done" in `docs/PLAN.md` before the next starts.
- Build node groups from Python scripts that live in the repo, so every `.blend` can be regenerated. Never hand-edit a `.blend` without the script that made it.
- Every phase gets a headless test script that prints measurable numbers (error relative to r, timing) and writes JSON to `tests/results/`.
- After a phase:
    - update `docs/PLAN.md` (status, roadmap row)
    - write `docs/PHASEn_FINDINGS.md`
    - commit to `main`
- Visual checks: render with `tests/render_phase3.py` and LOOK at the images (numeric tests missed three shading bugs in Phase 3). The user checks the Add Modifier menu in their own Blender.
- Don't commit `*.blend1` or `__pycache__/`.
- Releasing: bump `version` in `extension/blender_manifest.toml`, add `docs/release_notes/vX.Y.Z.md`, then start `.github/workflows/release.yml`. This session can't push tags or create releases directly, so dispatch it: `gh api -X POST repos/baguotti/blender_constant_radius_bevel/actions/workflows/release.yml/dispatches -f ref=main`. CI builds, tests and publishes.
