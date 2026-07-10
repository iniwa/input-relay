# 2026-07-09 Standalone Shared Input Logic

## Goal
Implement the next remaining item from `docs/improvements.md`:

- Remove duplicated keyboard normalization/event creation logic between `sender/input_sender.py` and `receiver/standalone_capture.py`.
- Remove duplicated gamepad polling logic by sharing the existing `Gamepad` implementation with standalone mode.

Use Claude Code with Sonnet 5 for the implementation. Keep behavior, JSON event shapes, ports, APIs, startup entry points, and `.bat` launcher compatibility unchanged.

## Background
Current duplication:

- `receiver/standalone_capture.py` has `_MODIFIER_MAP`, `_key_to_str`, `_get_vk`, `_make_event` that duplicate `sender/input_sender.py`.
- `receiver/standalone_capture.py` has a local `_gamepad_loop` that duplicates a simplified version of `sender/gamepad.py`.

Design decision for this handoff:

- Do not make receiver import implementation from `sender/` directly.
- Introduce a small shared module namespace at repo root, e.g. `input_common/`.
- Move reusable implementation there.
- Keep `sender/gamepad.py` as a compatibility wrapper so existing `import gamepad as gamepad_mod` in `sender/input_sender.py` and direct `python sender/input_sender.py` execution remain safe.

This project is used as a `secretary-bot` submodule, so import compatibility and API compatibility matter more than making a large cleanup.

## Files To Inspect
- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md`
- `sender/input_sender.py`
- `sender/gamepad.py`
- `receiver/standalone_capture.py`
- launch scripts / entrypoint assumptions if needed

## Files To Edit
Expected:

- new `input_common/__init__.py`
- new `input_common/input_events.py`
- new `input_common/gamepad.py`
- `sender/input_sender.py`
- `sender/gamepad.py`
- `receiver/standalone_capture.py`
- `docs/improvements.md`

Do not edit real `config/*.json` files.
Do not edit receiver/sender API docs unless you actually change API behavior; the intended implementation should not require `docs/api.md` changes.

## Required Changes

### 1. Shared keyboard/event helpers
Create a shared helper module, for example `input_common/input_events.py`, with:

- `key_to_str(key)`
- `get_vk(key)`
- `make_event(event_type, key, source="keyboard", vk=None)`

Behavior must match the current sender implementation:

- left/right shift normalize to `shift`
- left/right ctrl normalize to `ctrl`
- left/right alt normalize to `alt`
- digits and A-Z use virtual-key normalization to avoid shifted/control characters
- fallback to `key.char.lower()`, then `key.name`, then `vk_<code>`, then `str(key)`
- JSON event shape and timestamp behavior unchanged

Update:

- `sender/input_sender.py` to import and use the shared helpers.
- `receiver/standalone_capture.py` to import and use the same helpers.

Keep names local if that minimizes diff, e.g. `from input_common.input_events import get_vk as _get_vk`.

Because sender and receiver are often run as direct scripts from subdirectories, ensure imports work for:

- `python sender/input_sender.py`
- `python receiver/input_server.py --standalone`

Use a minimal root-path bootstrap if necessary. Do not introduce packaging/build tooling.

### 2. Shared gamepad poller
Move the reusable `Gamepad` implementation from `sender/gamepad.py` to `input_common/gamepad.py`.

Preserve sender compatibility:

- Keep `sender/gamepad.py` present as a thin compatibility wrapper that re-exports `Gamepad` and any constants that existing local code might import.
- `sender/input_sender.py` can keep `import gamepad as gamepad_mod` if that is the smallest safe change.

Update `receiver/standalone_capture.py`:

- Replace the local `_gamepad_loop` polling implementation with the shared `Gamepad`.
- Use `Gamepad(emit_callback=_emit, is_running=lambda: _running)` or an equivalent minimal wrapper.
- Keep standalone event JSON shapes identical for:
  - `btn_<i>` key down/up
  - `hat_<i>_left/right/up/down` key down/up
  - `axis_<i>_neg/pos` key down/up
  - `axis_update`
- Keep 60Hz polling and disconnect sleep behavior.
- If `pygame` is missing in standalone mode, keep the existing user-visible behavior as closely as practical: print a concise disabled message and return from the gamepad thread rather than crashing noisily.

### 3. Scope control
Do not refactor:

- receiver WebSocket server
- sender HTTP/monitor modules
- raw mouse handling
- remote-control mode
- overlay frontend files

## Constraints
- No new runtime dependencies.
- No frontend build tooling, packaging, or CI/CD changes.
- No route, port, or JSON message shape changes.
- Do not change `.bat` launchers or entry point command lines.
- Keep direct-script execution compatible.
- Keep Python 3.11 compatibility.
- No unbounded queues/tasks or per-event blocking work introduced.
- Do not implement lint/test setup in this handoff.
- Do not commit, push, or update the `secretary-bot` submodule pointer.

## Non Goals
- Do not redesign gamepad controller selection UI/API.
- Do not change sender gamepad API (`info`, `selected_id`, `select`, `request_refresh`, `run`).
- Do not change standalone mode feature set beyond using the shared implementation.
- Do not add tests/lint infrastructure here.

## Verification
Run:

```powershell
python -m py_compile sender\input_sender.py sender\gamepad.py receiver\standalone_capture.py input_common\input_events.py input_common\gamepad.py
git diff --check
```

Also perform focused static review:

- Confirm `sender/input_sender.py` no longer contains its own `_MODIFIER_MAP`, `key_to_str`, or `_get_vk` implementation.
- Confirm `receiver/standalone_capture.py` no longer contains duplicated keyboard normalization or a full local gamepad polling loop.
- Confirm direct imports should work when running sender/receiver scripts from their subdirectories.
- Confirm standalone gamepad event key names and `axis_update` payloads match the previous implementation.
- Confirm sender still gets a `Gamepad` class with `info`, `selected_id`, `select`, `request_refresh`, and `run`.

Live checks are optional. If not run, state that Main PC sender capture and Sub PC standalone mode live checks were not run.

## Expected Report
Report concisely in Japanese:

- Files changed.
- What moved into `input_common/`.
- How direct-script import compatibility is preserved.
- Whether API/port/message behavior changed.
- Verification commands and results.
- Any blocked live checks.
- Whether `docs/improvements.md` was updated by moving this completed item to the completion archive.

