# 2026-07-09 Receiver Per-Client Browser Send

## Goal
Implement the next remaining item from `docs/improvements.md`:

- `receiver` browser broadcasts should send per client and drop/discard failed or stale clients instead of waiting for all browser clients with `asyncio.gather`.

Use Claude Code with Sonnet 5 for the implementation. Keep all HTTP/WebSocket API routes, ports, message shapes, and startup entry points compatible with the current repository, because this repo is consumed as the `secretary-bot` submodule at `windows-agent/tools/input-relay`.

## Background
`receiver/input_server.py` receives sender input events, broadcasts them to OBS/browser clients, and optionally injects remote-control input on the Sub PC.

The current implementation has two relevant broadcast paths:

- `broadcast_to_browsers(message)` snapshots `browser_clients` and waits for all `send()` calls through `asyncio.gather`.
- `sender_handler(ws)` also snapshots `browser_clients` and waits for all `send()` calls before continuing to the remote-control injection branch.

One slow/stalled browser client should not delay other browser clients or remote-control injection.

## Files To Inspect
- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md`
- `docs/api.md`
- `receiver/input_server.py`
- `sender/input_sender.py` monitor broadcaster pattern around `monitor_broadcaster()`

## Files To Edit
Expected:

- `receiver/input_server.py`
- `docs/improvements.md`

Do not edit sender files for this task unless you only inspect them.
Do not edit real `config/*.json` files.

## Required Changes
In `receiver/input_server.py`:

1. Replace browser broadcasting that waits on `asyncio.gather` with a per-client send helper.
2. The helper should:
   - Snapshot current `browser_clients` under `_browser_lock`.
   - Return quickly if there are no clients.
   - Send to each client independently.
   - On send failure, discard that client under `_browser_lock`.
   - Avoid holding `_browser_lock` while awaiting `client.send(...)`.
3. Apply the helper to both:
   - `broadcast_to_browsers(message)`
   - the direct browser fan-out inside `sender_handler(ws)`
4. Preserve message order for a given caller as much as the existing single coroutine flow allows.
5. Ensure remote-control injection in `sender_handler` is not delayed by one failed/stale browser client more than necessary.

If you choose to make `sender_handler` call `broadcast_to_browsers(msg)`, verify that it does not create recursion or alter message payloads.

## Constraints
- No new runtime dependencies.
- No frontend build tooling, packaging, or CI/CD changes.
- No route, port, or JSON message shape changes.
- No change to browser connection registration/unregistration semantics except discarding failed clients.
- No unbounded queues/tasks tied to uptime.
- Do not change remote-control safety behavior.
- Do not implement other `docs/improvements.md` items in this handoff.

## Non Goals
- Do not refactor the whole receiver server.
- Do not change OBS overlay frontend files.
- Do not add tests/lint infrastructure.
- Do not commit, push, or update the `secretary-bot` submodule pointer.

## Verification
Run:

```powershell
python -m py_compile receiver\input_server.py
git diff --check
```

Also perform a focused static review:

- Confirm no `asyncio.gather(... c.send(message) ...)` browser fan-out remains in `receiver/input_server.py`.
- Confirm `_browser_lock` is not held while awaiting WebSocket `send`.
- Confirm failed browser clients are removed from `browser_clients`.
- Confirm sender-to-browser event payloads are unchanged.
- Confirm remote-control injection still calls `input_injector.replay_event(event)` under the same conditions as before.

Live checks are optional. If not run, state that the Sub PC receiver/OBS browser-source live check was not run.

## Expected Report
Report concisely in Japanese:

- Files changed.
- Exact broadcast behavior change.
- Whether `docs/api.md` was unchanged and why.
- Verification commands and results.
- Any blocked live checks.
- Whether `docs/improvements.md` was updated by moving this completed item to the completion archive.

