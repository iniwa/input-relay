# 2026-07-09 High Priority Stability Fixes

## Goal
Implement the two remaining high-priority items from `docs/improvements.md`:

1. Block path traversal in receiver static file serving.
2. Prevent sender `event_queue` from accumulating input events while the receiver is disconnected.

Use Claude Code with Sonnet 5 for the implementation. Keep all HTTP/WebSocket API routes, ports, message shapes, and startup entry points compatible with the current repository, because this repo is consumed as the `secretary-bot` submodule at `windows-agent/tools/input-relay`.

## Background
`input-relay` is a Windows resident two-process tool:

- `sender/input_sender.py` runs on Main PC and captures keyboard, raw mouse, and gamepad input.
- `receiver/input_server.py` runs on Sub PC and serves OBS overlay/config pages plus receiver APIs.
- `secretary-bot` depends on the existing receiver/sender APIs documented in `docs/api.md`; this task must not change those contracts.

The current improvement checklist describes two high-priority resident-operation issues:

- `receiver/input_server.py` directly joins request paths to `OVERLAY_DIR`, allowing raw HTTP clients to read files outside `receiver/`.
- `sender/input_sender.py` uses an unbounded `asyncio.Queue` and enqueues input events even while `ws_status` is not connected.

## Files To Inspect
- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md`
- `docs/api.md`
- `receiver/input_server.py`
- `sender/input_sender.py`
- `sender/raw_mouse.py`
- `sender/gamepad.py`

## Files To Edit
Expected:

- `receiver/input_server.py`
- `sender/input_sender.py`
- `docs/improvements.md`

Only edit `docs/api.md` if you discover an API contract actually changes. The intended implementation should not require that.

Do not edit real `config/*.json` files.

## Required Changes

### 1. Receiver static file path traversal
In `OverlayHandler.do_GET`, keep API dispatch and overlay mode routes unchanged:

- `/api/*` dispatch remains unchanged.
- `/history`, `/input`, and `/mouse-trail` still call `_serve_overlay_with_mode`.
- `/` still serves `config_gui.html`.
- Normal files under `receiver/`, such as `/overlay.html`, `/config_gui.html`, `/shared_render.js`, CSS, and JSON files, remain servable.

For static file serving, resolve the requested path safely:

- Resolve the candidate path under `OVERLAY_DIR`.
- Resolve `OVERLAY_DIR`.
- If the candidate is not inside `OVERLAY_DIR`, return 404.
- Return 404 on malformed/invalid paths instead of raising.
- Preserve existing content type behavior unless a small helper makes it clearer.

Use `Path.resolve()` and `Path.is_relative_to()`; Python 3.11 is the target.

### 2. Sender disconnected queue behavior
Prevent input events from accumulating while the receiver is disconnected.

Required behavior:

- If `ws_status != "connected"`, `_post_event` must not enqueue ordinary input events.
- Connected behavior must preserve event ordering.
- Remote-control state/toggle delivery must still work when connected and must not silently break.
- Monitor WebSocket behavior should remain unchanged; monitor clients may still see local events even when receiver is disconnected if the existing `_emit(..., monitor=True)` path provides that.
- Avoid adding blocking work on keyboard/mouse/gamepad capture paths.

Implementation preference:

- Add a bounded receiver queue, e.g. `asyncio.Queue(maxsize=N)`, to protect against accidental long-term growth even while connected.
- In `_post_event`, check `ws_status` before scheduling the enqueue.
- For a full queue, drop the oldest queued receiver event and enqueue the newest one. Do this on the asyncio loop thread so it is thread-safe.
- Use a small constant for the queue size with a comment explaining that stale overlay/input events are less valuable than bounded resident memory.

Do not change sender HTTP API routes, sender monitor WS port, receiver WS port, or remote-control JSON message shapes.

## Constraints
- No new runtime dependencies.
- No frontend build tooling, packaging, or CI/CD changes.
- No `.bat` launcher behavior changes.
- Do not change documented ports:
  - receiver HTTP 8081
  - receiver WS 8888
  - sender HTTP 8082
  - sender monitor WS 8083
- Do not change secretary-bot-facing API contracts in `docs/api.md`.
- Keep resident-process stability first: bounded state, no per-event blocking I/O, no unthrottled input source.
- Preserve remote-control safety behavior: stuck-key prevention, auto-disable on disconnect, and mouse suppression behavior.

## Non Goals
- Do not implement low-priority items from `docs/improvements.md`.
- Do not refactor `sender/input_sender.py` into multiple modules.
- Do not refactor standalone capture or gamepad code.
- Do not add tests/lint infrastructure in this handoff.
- Do not push, commit, or bump the `secretary-bot` submodule pointer unless explicitly requested after Codex review.

## Verification
Run:

```powershell
python -m py_compile sender/*.py receiver/*.py
git diff --check
```

Also perform a focused static review:

- Confirm `GET /../config/config.json` would return 404 from `OverlayHandler.do_GET`.
- Confirm `GET /C:/Windows/win.ini` would return 404 from `OverlayHandler.do_GET`.
- Confirm `/`, `/overlay.html`, `/shared_render.js`, `/history`, `/input`, and `/mouse-trail` still route as before.
- Confirm `_post_event` does not enqueue receiver events while disconnected.
- Confirm connected sender event order is still FIFO except for the bounded-queue overflow case.

Live process checks are optional. If not run, state that they were not run and name the PC where they would need to run:

- receiver static serving check: Sub PC.
- sender disconnected queue behavior: Main PC with receiver stopped.

## Expected Report
Report concisely in Japanese:

- Files changed.
- Exact behavior change for path traversal.
- Exact behavior change for disconnected sender queue.
- Whether `docs/api.md` was unchanged and why.
- Verification commands and results.
- Any blocked live checks.
- Whether `docs/improvements.md` was updated by moving the two completed high-priority items to the completion archive.

