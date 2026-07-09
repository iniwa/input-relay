# 2026-07-09 Sender Module Split

## Goal
Implement the next remaining item from `docs/improvements.md`:

- Split `sender/input_sender.py` responsibilities by moving the sender HTTP config API and monitor WebSocket server into separate `sender/` modules.

Use Claude Code with Sonnet 5 for the implementation. Behavior, ports, API routes, JSON message shapes, startup entry points, and `.bat` launcher compatibility must remain unchanged.

## Background
`sender/input_sender.py` currently owns:

- receiver WebSocket client
- sender HTTP GUI/config API
- sender monitor WebSocket
- keyboard/mouse listener management
- raw mouse/gamepad startup
- remote-control mode control

This handoff is a conservative module split only. The runtime architecture remains one sender process on Main PC. `secretary-bot` consumes this repo as a submodule and depends on the sender/receiver APIs documented in `docs/api.md`.

## Files To Inspect
- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md`
- `docs/api.md`
- `sender/input_sender.py`
- `sender/sender_gui.html`
- existing sender modules for style:
  - `sender/gamepad.py`
  - `sender/raw_mouse.py`
  - `sender/overlay_window.py`
  - `sender/ll_mouse_hook.py`

## Files To Edit
Expected:

- `sender/input_sender.py`
- new `sender/http_api.py`
- new `sender/monitor_ws.py`
- `docs/api.md` only for implementation-location references, if needed
- `docs/improvements.md`

Do not edit real `config/*.json` files.

## Required Changes

### 1. Extract sender HTTP API
Move `SenderHTTPHandler` and `start_http_server` out of `sender/input_sender.py` into a new module, for example `sender/http_api.py`.

The extracted module must keep these routes and behavior unchanged:

- `GET /` and `GET /index.html` serve `sender_gui.html`
- `GET /api/config`
- `GET /api/controllers`
- `GET /api/status`
- `POST /api/config`
- `POST /api/select-controller`
- `POST /api/refresh-controllers`
- `POST /api/restart`
- `OPTIONS` CORS response

Use an explicit context/callback object or function parameters rather than importing mutable globals back from `input_sender.py`. Avoid circular imports.

The context should provide the HTTP module only what it needs, such as:

- current config object and a way to save it
- GUI path
- gamepad accessor
- overlay valid positions
- reconnect trigger
- status snapshot values (`ws_status`, selected controller, remote mode, input timestamps, server time)
- restart scheduling callback or enough data to schedule the existing restart behavior

Preserve the existing config mutation semantics:

- `host`, `port`, `gamepad_enabled`, `raw_mouse_enabled`, `local_name`, `target_name`, and `remote_overlay.enabled` / `remote_overlay.position` are handled the same way.
- changing host/port triggers reconnect.
- changing gamepad/raw_mouse flags remains save-only until restart, as documented.
- restart still responds first and then replaces the current process after the same delay.

### 2. Extract sender monitor WebSocket
Move monitor WS state and functions out of `sender/input_sender.py` into a new module, for example `sender/monitor_ws.py`.

Preserve behavior:

- `enqueue_monitor(data)` remains thread-safe from keyboard/mouse/gamepad/listener threads.
- monitor clients receive the same JSON strings as before.
- failed monitor clients are discarded.
- monitor WS default port remains 8083.
- no new unbounded buffers. If the current monitor queue remains unbounded, do not broaden the task into changing that unless it is a tiny local improvement with no behavior risk. The main goal is module split.

Recommended API shape:

- `monitor_ws.MonitorServer(is_running, logger)` or simple module functions with explicit `queue`, `clients`, and `loop` initialization.
- `input_sender.py` should keep a small wrapper `enqueue_monitor(data)` if that minimizes call-site churn, but the implementation should live in `monitor_ws.py`.
- `main()` should initialize monitor state with the sender asyncio loop and start the monitor server from the new module.

### 3. Keep `input_sender.py` focused
After extraction, `input_sender.py` should still own:

- config load/save defaults
- receiver WebSocket connection/send/reconnect loop
- input event creation and capture callbacks
- remote-control mode and overlay/blocker/listener lifecycle
- startup/shutdown orchestration

Do not refactor keyboard normalization, standalone capture, gamepad internals, or receiver code in this handoff.

## Constraints
- No new runtime dependencies.
- No frontend build tooling, packaging, or CI/CD changes.
- No route, port, or JSON message shape changes.
- Do not change `.bat` launchers or entry point command lines.
- Keep imports compatible with running `python sender/input_sender.py` directly from the repo.
- Keep Python 3.11 compatibility.
- Avoid per-event blocking work and unbounded long-lived tasks introduced by this split.
- Do not implement other `docs/improvements.md` items in this handoff.

## Non Goals
- Do not change sender/receiver protocol.
- Do not change raw mouse/gamepad polling behavior.
- Do not extract keyboard normalization here.
- Do not add tests/lint infrastructure here.
- Do not commit, push, or update the `secretary-bot` submodule pointer.

## Verification
Run:

```powershell
python -m py_compile sender\input_sender.py sender\http_api.py sender\monitor_ws.py
git diff --check
```

Also perform a focused static review:

- Confirm `sender/input_sender.py` imports the new modules without circular imports.
- Confirm all sender HTTP routes listed above still exist and return the same response shapes.
- Confirm `POST /api/config` still mutates/saves config and triggers reconnect only when host/port change.
- Confirm `POST /api/restart` still responds before restart is scheduled.
- Confirm monitor enqueue remains thread-safe and failed clients are discarded.
- Confirm `docs/api.md` route contract is unchanged. If only implementation-location references changed, update those references.

Live checks are optional. If not run, state that Main PC sender GUI/monitor WS live checks were not run.

## Expected Report
Report concisely in Japanese:

- Files changed.
- What moved to each new module.
- Whether API/port/message behavior changed.
- Whether `docs/api.md` was changed and why.
- Verification commands and results.
- Any blocked live checks.
- Whether `docs/improvements.md` was updated by moving this completed item to the completion archive.

