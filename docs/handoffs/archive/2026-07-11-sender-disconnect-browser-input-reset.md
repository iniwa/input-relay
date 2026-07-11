# Goal

Complete the next unchecked improvement: when the sender disconnects, explicitly
reset all browser overlay input state so a missing key-up or neutral axis does
not remain visible.

# Files To Inspect

- AGENTS.md, CLAUDE.md, docs/improvements.md
- receiver/input_server.py
- receiver/overlay.html
- docs/api.md
- existing tests

# Files To Edit

- receiver/input_server.py
- receiver/overlay.html
- docs/api.md
- tests (fake/static frontend test only if feasible without new dependencies)
- docs/improvements.md (move completed item to archive)

# Constraints

- On sender-handler cleanup, broadcast one backward-compatible
  {"type":"input_reset"} message to current browser clients. It must happen
  for that sender connection even when Remote Control was already off.
- Do not alter normal input message payloads, browser connection lifecycle,
  layout/config/history preference handling, Remote Control disconnect
  auto-disable, ports, or dependencies.
- In overlay.html, input_reset must immediately cancel pending display-delay
  timers and afterglow timers; clear pressedKeys, directional state, axisState,
  active/afterglow DOM classes, and refresh controller stick/trigger visuals
  to neutral. It must not add history entries or rebuild the layout.
- Reset mouse-trail accumulated state if needed for a complete displayed-input
  reset, without stopping the normal animation loop.
- Keep mode_switch behavior compatible; factor a shared reset helper only if
  it makes all reset paths correct and simple.
- Tests must not start real sockets or browsers. Add a source-level/static
  regression assertion if no existing JS test harness exists, and add
  receiver fake-browser async coverage for the disconnect broadcast.

# Verification

python -m py_compile receiver\input_server.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check

# Expected Report

Describe reset message ordering, every cleared frontend state category, tests,
verification, blocked live Sub PC browser check, and config access. Commit
locally only; do not push.
