# 2026-07-09 Minimal Lint And Tests

## Goal
Implement the final remaining item from `docs/improvements.md`:

- Add minimal lint configuration.
- Add a minimal test suite for pure logic.

Use Claude Code with Sonnet 5 for the implementation. Do not change runtime dependencies, `.bat` launcher install behavior, API routes, ports, or JSON message shapes.

## Background
The project intentionally has no packaging/build system and is run by Windows `.bat` launchers. Those launchers own runtime dependency installation (`websockets`, `pynput`, and optionally `pygame`). This handoff should add development-time checks without changing resident runtime behavior.

Current useful pure-logic targets include:

- `input_common/input_events.py` (`key_to_str`, `get_vk`, `make_event`)
- receiver preset/layout preset load/save/delete helpers in `receiver/input_server.py`
- receiver static path resolution logic in `OverlayHandler._resolve_static_path`

There are currently uncommitted changes from the previous handoff (`input_common/` and standalone shared input logic). Work with them; do not revert them.

## Files To Inspect
- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md`
- `input_common/input_events.py`
- `receiver/input_server.py`
- `sender/http_api.py`
- `sender/input_sender.py`
- existing `.bat` launchers, only to confirm they do not need changes

## Files To Edit
Expected:

- new `pyproject.toml`
- new `tests/` files
- `docs/improvements.md`
- optionally `README.md` if you add a very small development-check command section

Do not edit:

- real `config/*.json`
- `.bat` launchers
- runtime dependency installation behavior
- API docs unless behavior actually changes

## Required Changes

### 1. Minimal ruff configuration
Add `pyproject.toml` with minimal ruff configuration:

- Python target: 3.11
- Keep the rule set conservative/default-like.
- Do not add formatting requirements that force broad churn across the repo.
- Avoid mass lint rewrites unrelated to this handoff.

The goal is to make `ruff check` usable as a development check, not to introduce packaging or CI.

Recommended command:

```powershell
python -m ruff check .
```

If `ruff` is not installed in the local environment, do not install it unless it is already available without changing project dependency/install files. Report it as not run/blocked. Do not modify `.bat` files to install ruff.

### 2. Minimal tests
Add tests under `tests/` using the standard library `unittest`; do not require `pytest`.

Use test code that avoids live keyboard/mouse/gamepad hooks and avoids starting resident servers.

Minimum useful coverage:

- `input_common.input_events.make_event` returns valid JSON with `type`, `key`, `source`, `timestamp`, and optional `vk`.
- `key_to_str` / `get_vk` cover fake key objects for:
  - VK A-Z normalization
  - VK 0-9 normalization
  - `char` fallback
  - `name` fallback
  - `vk_<code>` fallback
- receiver static path resolver returns a path under `receiver/` for a normal file and rejects:
  - `../config/config.json`
  - `C:/Windows/win.ini`
- receiver preset CRUD pure helpers can be tested with `tempfile.TemporaryDirectory()` by temporarily rebinding `PRESETS_PATH` / `LAYOUT_PRESETS_PATH` to temp files. Do not touch real config files.

Import considerations:

- Some modules are direct-script oriented and import local siblings by bare name.
- Tests may insert `sender/`, `receiver/`, or repo root into `sys.path` locally in test setup.
- Keep this localized to tests; do not add packaging.

Recommended command:

```powershell
python -m unittest discover -s tests
```

### 3. Documentation/update checklist
Move the completed lint/tests item from `docs/improvements.md` to the completion archive once the minimal checks are in place.

If you add README documentation, keep it short: just the two development commands and note that ruff is a dev tool, not a runtime dependency.

## Constraints
- No new runtime dependencies.
- No build tooling, packaging, or CI/CD.
- No `.bat` launcher changes.
- No route, port, API, or JSON message behavior changes.
- No live process startup in tests.
- No broad style churn.
- Do not implement new product features.
- Do not commit, push, or update the `secretary-bot` submodule pointer.

## Non Goals
- Do not make the whole codebase fully typed.
- Do not add pytest/tox/nox/pre-commit.
- Do not add GitHub/Gitea CI.
- Do not refactor production code just to satisfy lint unless the fix is tiny and obviously correct.

## Verification
Run:

```powershell
python -m unittest discover -s tests
python -m py_compile sender\input_sender.py sender\gamepad.py receiver\standalone_capture.py input_common\input_events.py input_common\gamepad.py receiver\input_server.py
git diff --check
```

Run if available:

```powershell
python -m ruff check .
```

If ruff is unavailable, report that clearly and do not install it or change runtime install scripts.

Also perform a focused static review:

- Confirm tests do not read/write real `config/*.json`.
- Confirm tests do not start keyboard/mouse/gamepad hooks or servers.
- Confirm `.bat` launchers are unchanged.
- Confirm runtime imports still work for direct sender/receiver scripts.

## Expected Report
Report concisely in Japanese:

- Files changed.
- Test cases added.
- Lint configuration added.
- Verification commands and results, including whether ruff ran.
- Any blocked checks.
- Whether `docs/improvements.md` was updated by moving this completed item to the completion archive.

