# Goal

Fix the next unchecked item in `docs/improvements.md`: make Remote Control
connection transitions fail closed. The receiver must never enable OS input
injection merely because an HTTP request was accepted while no synchronized
sender is present.

# Background

The receiver currently enables its own Remote Control state before sending a
command to the sender, even if `sender_ws` is absent or already stale.
Furthermore, a newly connected sender only sends its state when it is already
ON. Thus a receiver can remain ON while a Main PC sender is in normal mode,
and ordinary input can be injected on the Sub PC.

The sender can send and receive `{"type":"remote_control","enabled":...}`
already. Treat an incoming message of that type at the receiver as the
sender's explicit observed state/acknowledgement. No protocol field or route
needs to change.

# Files To Inspect

- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md` (second unchecked item)
- `receiver/input_server.py`
- `sender/input_sender.py`
- `receiver/config_gui.html`
- `docs/api.md`
- `tests/test_remote_control.py`

# Files To Edit

- `receiver/input_server.py`
- `sender/input_sender.py`
- `tests/test_remote_control.py`
- `docs/improvements.md` (move this completed item to the completion archive)
- `docs/api.md` if the API success/failure semantics need factual updates

# Constraints

- Keep `POST /api/remote-control`, the WebSocket path/port, and the existing
  `remote_control` JSON message shape unchanged.
- A newly accepted sender connection starts *unsynchronized*. Before any
  sender state message is received, Remote Control injection must be
  fail-closed even if stale state existed; connection cleanup must also reset
  this readiness.
- On every successful sender connection, before normal queued input can be
  sent, `sender/input_sender.py` must explicitly send its current state as
  `remote_control` with `enabled` set to either true or false. Do not retain
  the old "send only when ON" behavior.
- At the receiver, only the sender's received state message may establish the
  synchronized/ready state for that connection. It must set the receiver RC
  state to the sender's reported value. A newly connected sender reporting
  false therefore explicitly keeps injection OFF.
- Enabling from the receiver HTTP API requires a currently connected,
  synchronized sender. If absent or not synchronized, reject the request
  without changing `remote_control_enabled`, without sending a command, and
  with an explicit non-2xx HTTP error (prefer a small status-aware API error
  mechanism rather than returning `{ok: false}` with HTTP 200).
- A successful enable request sends the existing command to the synchronized
  sender but must not locally enable injection until the sender subsequently
  reports/acknowledges enabled state. This prevents normal-mode input already
  in flight from being injected before the sender has engaged suppression.
  Preserve normal GUI operation: success can still return the requested
  `enabled` value, while browser state changes continue to arrive through the
  existing `remote_control_state` broadcast when acknowledgement is received.
- Disable is safety-first: disable receiver injection and exact held-input
  cleanup immediately, even if the sender is absent or the notify send fails;
  best-effort notify a current sender afterward. Keep disconnect auto-disable.
- Avoid races between HTTP threads and the WebSocket loop. Connection/readiness
  state must be read/changed under the existing RC synchronization boundary or
  an equally clear dedicated lock. If an HTTP handler needs a websocket send
  acknowledgement, it may wait only on this low-frequency control-plane path
  with a short bounded timeout; do not block the input event path.
- Do not implement the following checklist item (sender suppression ordering),
  browser reset, queue changes, or API/config ownership work.
- No new dependencies, no real config changes, no launcher/port changes, no
  live sender/receiver processes.

# Verification

Run:

```powershell
python -m py_compile sender\input_sender.py receiver\input_server.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

Add fake-only tests covering at least:

1. Sender connection starts unsynchronized and an input event cannot inject
   before an explicit state message.
2. A sender's initial explicit false state keeps RC injection off.
3. API enable with no sender, and with connected-but-unsynchronized sender,
   returns non-2xx and leaves RC state off/no command sent.
4. API enable with a synchronized sender sends the command but does not enable
   local injection until the sender acknowledgement arrives; acknowledgement
   then enables it.
5. Disconnect resets readiness and disables an active Remote Control state;
   a reconnect must synchronize again before injection can resume.
6. Sender connection setup sends explicit false as well as true state before
   starting normal queued input handling. Use a fake WebSocket / task seams;
   do not start real sockets.

Live confirmation is blocked: it requires the Main PC sender and Sub PC
receiver. Name that explicitly in the report.

# Expected Report

Report the connection/readiness state representation, precise API failure and
pending-enable behavior, ordering of sender initialization messages, each test
case/result, full verification status, and whether any real config was read or
modified. Commit and push the completed scoped change to the Gitea `origin`.
