# Goal

Fix three cleanup gaps found by Codex review of phase 1, and add focused
regression coverage. Do not expand scope.

# Background

The phase-1 implementation currently passes 80 unittests, but review found
edge paths that do not fully satisfy the handoff's bounded-wait and
single-finally requirements.

# Files To Inspect

- `AGENTS.md`, `CLAUDE.md`
- `docs/handoffs/2026-07-11-all-improvements-phase-1-resident-stability.md`
- current uncommitted changes in `sender/monitor_ws.py`, `sender/raw_mouse.py`,
  `input_common/gamepad.py`
- `tests/test_monitor_ws.py`, `tests/test_raw_mouse.py`, `tests/test_gamepad.py`

# Files To Edit

- `sender/monitor_ws.py`
- `sender/raw_mouse.py`
- `input_common/gamepad.py`
- the three corresponding test files

# Constraints

1. Monitor cleanup: after a client send timeout/error, discard the client
   before closing as now, but also bound `ws.close()` with the same 1.0 second
   timeout. A close that stalls or raises must not stop the broadcaster or
   healthy-client delivery. Add a stalled-close test.
2. Raw Input: after `timeBeginPeriod(1)`, even `GetModuleHandleW(None)` must be
   inside the single `try/finally`. Initialize resource flags/references without
   acquisition first, enter `try`, then acquire the module handle and all later
   resources. Add a fake test where `GetModuleHandleW` raises and assert
   `timeEndPeriod(1)` still runs and no invalid cleanup runs.
3. Gamepad session cleanup:
   - Assign `self._pygame` immediately after importing pygame, before calling
     `pg.init()` / `pg.joystick.init()`, so partial init failure is teardown-able.
   - In the outer session `finally`, use cleanup that neutralizes, clears all
     three state buffers, sets `state["joy"]` to `None`, and then always tears
     down pygame even if neutral-event emission raises. Neutral emission
     failure may be logged, but must not terminate retry or skip teardown.
   - Make pygame joystick quit and global quit independent best-effort calls so
     failure of the first does not skip the second; always set `_pygame=None`.
   - Add tests for buffer/reference clearing on session failure, partial init
     teardown, and teardown continuing after neutral emission or joystick-quit
     failure.
4. Preserve all prior phase-1 behavior and tests. No dependency, config,
   protocol, port, GUI, API, launcher, or documentation edits.

# Non Goals

- Do not implement phase 2 improvements.
- Do not refactor unrelated code or change retry/backoff timing.

# Verification

```powershell
python -m py_compile sender\monitor_ws.py sender\raw_mouse.py input_common\gamepad.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

# Expected Report

Report the three fixes, focused tests, full test count, verification results,
and config access. Do not commit or push.
