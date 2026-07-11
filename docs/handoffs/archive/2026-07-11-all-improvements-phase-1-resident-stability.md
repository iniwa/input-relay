# Goal

Implement all resident-stability items in `docs/improvements.md` section 1,
including deterministic regression tests, without changing ports, protocols,
or input semantics.

# Background

`sender` and `receiver` are long-running Windows processes. Slow WebSocket
clients, disconnected devices, and transient native-library failures must not
create unbounded memory, delay Remote Control injection, or leave displayed
inputs stuck. This is phase 1 of the request to implement every current item in
`docs/improvements.md`; do not archive checklist items yet because Codex will
do that after all phases pass review.

# Files To Inspect

- `AGENTS.md`, `CLAUDE.md`, `docs/improvements.md`, `docs/api.md`
- `sender/monitor_ws.py`, `sender/raw_mouse.py`, `sender/input_sender.py`
- `receiver/input_server.py`, `receiver/standalone_capture.py`
- `input_common/gamepad.py`
- all files under `tests/`

# Files To Edit

- `sender/monitor_ws.py`
- `sender/raw_mouse.py`
- `receiver/input_server.py`
- `input_common/gamepad.py`
- tests under `tests/` (new unittest files are allowed)

Do not edit `docs/improvements.md` in this phase.

# Constraints

## Sender monitor WebSocket

- Give the monitor queue an explicit max size of 500.
- `enqueue()` remains thread-safe. Schedule one loop-thread helper which:
  drops immediately when there are no monitor clients; otherwise, when full,
  removes exactly the oldest queued item before enqueuing the newest.
- Bound each client send with a 1.0 second timeout. Send to a snapshot of
  clients concurrently so a stalled client does not postpone a healthy
  client's send. After timeout/error, best-effort close and discard only that
  failed client. Preserve healthy FIFO ordering and the current JSON payload.
- Keep local monitor operation independent of receiver connectivity and keep
  port 8083 as the default.

## Receiver browser fan-out and Remote Control ordering

- In `sender_handler`, parse/validate as now, then perform
  `_rc_inject_event(event)` before awaiting browser delivery. Remote Control
  state messages remain control-only and are not broadcast/injected.
- `broadcast_to_browsers` must start per-client bounded sends concurrently,
  preserve the 1.0 second timeout and failed-client discard, and wait for that
  event's send attempts before returning. Since sender/standalone callers
  invoke broadcasts sequentially, this preserves per-client message order.
- Add an async test with one stalled and one healthy browser proving the
  healthy send completes before the stalled timeout. Add a sender-handler test
  proving RC injection happens before a stalled browser send completes.

## Standalone queue

- Use an `asyncio.Queue(maxsize=500)`.
- All queue mutation occurs on the asyncio loop thread; keep capture callbacks
  thread-safe.
- Normal operation is FIFO. On overflow, clear the queued backlog, enqueue one
  JSON `{"type": "input_reset"}` message, then enqueue the newest event. This
  is the fixed key-down/key-up safety policy: sacrificing the overflowed
  backlog is preferable to a missing key-up leaving the overlay stuck.
- Rate-limit overflow warning logs to at most once per 5 seconds. Do not add
  blocking work to capture callbacks.
- Preserve standalone keyboard/mouse/gamepad support, JSON event shapes, and
  60 Hz source throttling.

## Raw Input

- Record whether `SetTimer` succeeded. When it failed, call `flush()` after
  every `MsgWaitForMultipleObjectsEx` wake/timeout (the existing 16 ms wait),
  after dispatching pending messages. When the timer succeeded, WM_TIMER stays
  the flush trigger.
- Starting immediately after `timeBeginPeriod(1)`, put every later acquisition
  and early-return path inside one `try/finally`. Track acquired resources so
  cleanup only calls valid operations. End the timer period on every path;
  kill an installed timer; destroy a created window; unregister the registered
  window class when applicable. Cleanup failures remain best-effort/logged.
- Preserve accumulated integer deltas, background Raw Input capture, 16 ms
  throttling, and outgoing message shape.
- Tests must use fake ctypes/Win32 objects only; do not install a real hook.

## Shared Gamepad

- Before clearing/replacing controller state for disconnect, selected
  controller switch, refresh switch, session exception, or shutdown, emit
  neutral events for all active buffered state:
  active buttons, hats, and threshold axes get their exact existing `key_up`;
  every non-neutral tracked raw axis gets `axis_update` value `0`.
  Then clear all three buffers and release the joystick reference.
- Fix the controller-switch order so old controller state is neutralized before
  assigning the new joystick.
- `run()` must survive exceptions from pygame init, scan, event pump,
  joystick creation/init, and getters. Tear down the failed session
  best-effort, neutralize any active state, and retry while `is_running()` is
  true with exponential backoff starting at 0.1 seconds and capped at 2.0
  seconds. Reset backoff after a session has successfully reached polling.
- Keep the existing 0.1 second no-controller sleep, 60 Hz polling, selected-id
  behavior, event key names, and JSON shapes. Do not add a second retry loop in
  sender or standalone; the shared class owns recovery.
- Tests use fake pygame/joystick/time only and cover disconnect neutralization,
  switch neutralization, and recovery after a transient exception.

## Browser registration cleanup

- Once a browser WebSocket is inserted in `browser_clients`, encompass all
  later work (including `load_config` and initial config send) in one
  `try/finally` that always discards it.
- Apply the existing 1.0 second send timeout to the initial config send.
- Preserve exactly one initial config message and ignore later browser
  messages as before. Test non-`ConnectionClosed` load/send failures.

## General

- Python 3.11 and stdlib + existing dependencies only. Use `unittest`, not
  pytest. Tests must not access real `config/*.json`, sockets, hooks, pygame,
  or OS input.
- No unbounded state, no per-input blocking work, no route/port/payload change,
  and no build tooling or startup-flow edits.
- Preserve all Remote Control fail-closed, exact-VK tracking, disconnect
  release, suppression, and sender reconnect behavior already covered by tests.

# Non Goals

- Do not implement section 2 GUI/API/launcher/preset changes in this phase.
- Do not edit README/API/checklist prose in this phase.
- Do not touch real config files, startup registration state, dependencies,
  packaging, CI/CD, or external repositories.

# Verification

Run all of:

```powershell
python -m py_compile sender\*.py receiver\*.py input_common\*.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

If wildcard expansion prevents the exact py_compile command in PowerShell,
enumerate the same Python files explicitly. Live Main/Sub PC checks are
expected to be blocked and must not be simulated with real input injection.

# Expected Report

Report each of the six implemented checklist items separately, tests added,
all verification results/counts, any blocked live checks, and confirmation
that real config files were not read/written. Stop and report any design
question instead of deciding beyond this handoff. Do not commit or push; Codex
will review first.
