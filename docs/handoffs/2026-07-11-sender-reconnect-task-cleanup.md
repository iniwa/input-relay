# Goal

Fix sender reconnect task cleanup: no Queue.get/Event.wait child task or send/recv
task may survive a reconnect/cancellation cycle or consume a later event.

# Files To Inspect

- AGENTS.md, CLAUDE.md, docs/improvements.md
- sender/input_sender.py
- tests/test_remote_control.py

# Files To Edit

- sender/input_sender.py
- tests/test_remote_control.py
- docs/improvements.md (archive completed item)

# Constraints

- Keep the 500-item queue, oldest-drop, FIFO successful sends, reconnect
  backoff, and remote-state messages unchanged.
- In _send_loop, always cancel and await both per-iteration child tasks in a
  finally block, including cancellation/error/reconnect paths. Use
  gather(return_exceptions=True) for cancellation cleanup.
- In sender(), after either send/receive task completes or an error occurs,
  cancel any still-pending task and await all created send/receive tasks before
  leaving the websocket context. Do not suppress unexpected completed-task
  exceptions.
- Do not block capture/event paths, add dependencies, touch config, or alter
  network protocol.
- Add fake-only async tests proving repeated reconnect/cancellation leaves no
  orphan child task and that the next queued event is sent exactly once.

# Verification

python -m py_compile sender\input_sender.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check

# Expected Report

Report cancellation/await ordering, repeated-cycle tests and results, blocked
Main PC live check, and config access. Commit locally only; do not push.
