# Phase 4 findings: packaging

- **Build:** Blender 5.2.4 LTS, headless
- **Raw results:** `../tests/results/phase4_results.json`
- **Build the zip:** `blender -b --factory-startup --python src/make_extension.py` → `dist/cr_bevel-1.0.2.zip`
- **Test:** `blender -b --factory-startup --python tests/test_phase4.py`

## Verdict

**Done.** CR Bevel installs as a standard Blender Extension and adds itself to Add Modifier > Generate.

## What ships

| Path | Contents |
| --- | --- |
| `extension/blender_manifest.toml` | Extension manifest: id `cr_bevel`, version 1.0.0, Blender 5.2+, GPL-3.0-or-later |
| `extension/__init__.py` | One operator, "CR Bevel", in Add Modifier > Generate. It appends the node group from the bundled `.blend` the first time it's used in a file, then reuses that copy, so your edits to it are kept |
| `library/cr_bevel_library.blend` | The same node group as an asset, for anyone who prefers the Asset Browser |

**Kept simple on purpose:**
- The extension doesn't change your preferences: it registers no asset library.
- One `.blend` is committed (in `library/`); the extension build copies it in.
- `dist/` is a build output and isn't committed.

## Checks (all pass)

The zip is installed into a temporary Blender user folder (your real configuration isn't touched), then checked in a fresh Blender:

| Check | Result |
| --- | --- |
| `extension validate` and `extension build` | OK |
| `extension install-file -e` | Installs and enables |
| Menu entry in Add Modifier > Generate | Present |
| Operator on a cube and a cylinder | Both get a working CR Bevel (144 and 256 bevel faces) |
| Second use in the same file | Reuses the node group (1 group, no duplicates) |
