# Goal

Complete the next unchecked improvement: establish Main PC input suppression before
any remote-mode overlay display work can wait.

# Files To Inspect

- AGENTS.md, CLAUDE.md, docs/improvements.md
- sender/input_sender.py
- sender/overlay_window.py
- sender/ll_mouse_hook.py
- tests/test_remote_control.py

# Files To Edit

- sender/input_sender.py
- tests/test_remote_control.py (or a focused new unittest file)
- docs/improvements.md (move this completed item to the archive)
- docs/handoffs archive is handled by Codex after review

# Constraints

- In _set_remote_mode(True), establish low-level mouse suppression and restart
  the suppress keyboard/mouse listeners before calling overlay manager show or
  any operation that can wait on Tk readiness. Preserve cursor freeze and
  existing double-defense design.
- Keep disable safety behavior: remove low-level suppression, hide overlay,
  unfreeze cursor, and restore unsuppressed listeners. Preserve Pause-driven
  temporary overlay hiding and user-hidden state behavior.
- Do not add dependencies or change remote-control protocol, ports, queue,
  launcher, config, or receiver behavior.
- Do not make overlay show synchronous on the async receiver event loop.
  Prefer ordering only; optional prewarm must be nonblocking and must not
  alter visible overlay behavior.
- Add fake-only unit tests that assert enable ordering: low-level suppress and
  listener restart finish before overlay show. Also cover disable ordering and
  no-op repeat mode changes. No real hooks, Tk, sockets, or OS input.

# Verification

Run:
```powershell
python -m py_compile sender\input_sender.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

# Expected Report

Report the exact enable/disable sequence, fake test cases, verification
results, blocked Main PC live check, and real-config access. Commit locally;
do not push.
