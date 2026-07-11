# Goal

Fix the first unchecked item in `docs/improvements.md`: prevent Remote
Control stuck keys by tracking actual injected inputs atomically.  Keyboard
state must be keyed by the exact VK that was injected, not the display `key`
string; mouse buttons must be tracked by their exact button name.

# Background

`receiver/input_server.py` currently keeps `_rc_pressed_keys` as display
names.  It calls `input_injector.replay_event(event)` with no result and later
calls `release_all`, which reconstructs a VK from that display name.  This
releases right Shift/Ctrl/Alt as their left variants and cannot release Win
keys (`91`/`92`).  Its enabled-state test, injection, pressed-set update, and
disable cleanup are not one synchronized lifecycle, so an in-flight key-down
can run after OFF has released the old set.

# Files To Inspect

- `AGENTS.md`
- `CLAUDE.md`
- `docs/improvements.md` (first unchecked item)
- `receiver/input_server.py`
- `receiver/input_injector.py`
- `docs/api.md`
- existing `tests/*.py`

# Files To Edit

- `receiver/input_server.py`
- `receiver/input_injector.py`
- `tests/test_remote_control.py` (new)
- `docs/improvements.md` (move the completed item to the completion archive)
- `docs/api.md` only if its remote-control description needs a factual wording
  update after the implementation

# Constraints

- Keep all event JSON and API routes unchanged.
- Keep normal replay order: the OS input is injected before successful pressed
  state is recorded/removed.
- Make `input_injector.replay_event(event)` expose enough exact identity for
  the caller to track an injected keyboard key as its actual integer VK and a
  mouse button as its exact supported `mouse_*` name.  Non-injectable events
  must not produce a tracked identity.
- Exact tracking applies to successful injection only.  Make the SendInput
  helper/injection path report success so failed/ineligible key events do not
  enter the pressed state.  Preserve existing behavior for mouse move and
  scroll; they are never held-state entries.
- Use a stable explicit identity format (for example tagged tuples) so a VK
  cannot collide with a mouse button.  Do not reconstruct a VK from display
  text during cleanup.
- In the receiver, protect the complete Remote Control event lifecycle
  (enabled check, replay, and tracked-state update) with `_rc_lock`.  The
  lock must also prevent a key-down that began before/while OFF from being
  recorded after disable has cleared the state.  The synchronous SendInput
  call is small and is allowed inside this existing short critical section;
  do not add awaits or blocking network/file work there.
- When disabling, take a snapshot and clear tracked state while holding
  `_rc_lock`, then release that exact snapshot outside the lock.  Preserve
  auto-disable on sender disconnect and the browser state broadcast.
- Preserve ordinary key-up behavior: only remove a tracked identity after a
  successful corresponding key-up injection.  Repeated up/down events must
  remain harmless.
- Add a precise injector release API for the exact tracked identities.  It
  may retain `release_all` as a compatibility wrapper only if useful; there
  must be no production cleanup path that derives VKs from display names.
- No new dependencies, no live hooks/sockets, no change to ports/launchers,
  no config file changes, and no changes to remote-control state protocol.

# Non Goals

- Do not implement the next fail-closed connection-transition item.
- Do not reorder browser fan-out versus remote injection (that is a separate
  checklist item).
- Do not change sender suppression/overlay behavior, gamepad behavior, or
  browser overlay rendering.
- Do not redesign the Remote Control API or add new persistence.

# Verification

Run:

```powershell
python -m py_compile receiver\input_server.py receiver\input_injector.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

The new unit tests must use fakes/mocks only and cover at least:

1. Right Shift (`161`), right Ctrl (`163`), right Alt (`165`), and Win keys
   (`91`, `92`) are released by their exact original VKs after OFF.
2. Simultaneous left/right modifier presses are independent and both release.
3. A mouse button uses the exact button release path.
4. A key-down racing with disable cannot remain in tracked state or create a
   post-disable injected down.  Make this deterministic with a fake injector
   or synchronization hook; do not use timing sleeps as the assertion.
5. Failed or unsupported injection is not tracked.

Do not attempt live Remote Control verification; that requires the Main PC
sender and Sub PC receiver.

# Expected Report

Report changed files, exact tracked-identity representation and lock/disable
ordering, test coverage/results, all verification output status, and any
design question or blocked live check.  State explicitly that no real
`config/*.json` was read or modified.
