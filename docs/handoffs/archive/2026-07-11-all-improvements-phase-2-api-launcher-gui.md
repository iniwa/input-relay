# Goal

Implement every remaining code/configuration item in `docs/improvements.md`
sections 2 and 3 (except the final checklist archive prose), with deterministic
regression tests and no change to documented responsibility boundaries.

# Background

Phase 1 resident-stability changes are already present in the working tree and
pass tests. This phase fixes receiver restart, receiver-local sender config UI,
standalone pygame installation, preset transactions, sender custom ports, and
two confirmed dead-code items. Main and Sub PC configs are separate: the Main
PC sender GUI/API is the live sender configuration path; receiver
`/api/sender-config` remains only a compatibility API for a Sub-PC-local copy.

# Files To Inspect

- `AGENTS.md`, `CLAUDE.md`, `docs/improvements.md`, `docs/api.md`, `README.md`
- `receiver/input_server.py`, `receiver/config_gui.html`,
  `receiver/standalone_capture.py`
- `sender/input_sender.py`, `sender/http_api.py`, `sender/sender_gui.html`
- `start_sender.bat`, `start_standalone.bat`
- `config/sender_config.example.json`
- all tests, including phase-1 additions currently untracked

# Files To Edit

- `receiver/input_server.py`, `receiver/config_gui.html`
- `sender/input_sender.py`, `sender/sender_gui.html`
- `start_sender.bat`, `start_standalone.bat`
- `config/sender_config.example.json`
- `README.md`, `docs/api.md`
- tests under `tests/` (new unittest files allowed)

Do not edit `docs/improvements.md` yet; Codex will archive all items after the
final review and verification.

# Constraints

## Receiver restart contract and cleanup

- The formal receiver route remains exactly `DELETE /api/restart`; do not add a
  POST route. Change `restartServer()` in the GUI to DELETE, require `res.ok`,
  show the returned/non-2xx error and do not reload on failure; only schedule
  the existing 2-second reload after a successful response.
- Add a module-level `threading.Lock` plus pending bool. Under the lock, only
  the first DELETE schedules the restart thread; repeated DELETE requests while
  pending return the same `{ "ok": true }` response without starting another
  thread.
- `_restart_server` still sleeps 0.5 seconds so the HTTP response goes first.
  After the sleep and before `os.execv`, call `_set_rc_state(False)` to release
  all tracked injected input. When standalone is active (use
  `_standalone_queue is not None`), import/call `standalone_capture.stop()`
  best-effort. Then exec with the existing executable/argv.
- If cleanup or `os.execv` fails, log it and reset the pending guard under the
  lock so a later DELETE can retry. Do not let one cleanup failure skip the
  other cleanup or the exec attempt.
- Add handler/dispatch tests proving POST is absent, DELETE is present, repeated
  calls start one thread, RC/standalone cleanup precedes exec, response returns
  before the 0.5-second worker delay, and exec failure releases the guard. Use
  fakes only; never restart the test process.

## Receiver-local sender config ownership and GUI

- Keep GET/POST `/api/sender-config` and `config_change` behavior for
  compatibility. Clearly relabel the receiver GUI section/tab as a receiver
  (Sub PC) local copy, state that it does not configure the resident Main PC
  sender, and direct live changes to the Main PC sender GUI/API (default 8082).
- Server-side POST must perform one locked read-modify-write transaction: load
  the existing receiver-local JSON object (or `{}`), update only incoming
  `host` and `port` keys, preserve all other keys, save, and broadcast the
  merged full object. Ignore other incoming keys rather than allowing this
  two-field compatibility UI/API to overwrite them. Keep `{ "ok": true }`.
- Change all three obsolete 8765 fallbacks in the receiver GUI to 8888,
  including the input value and init fallback.
- The debug WebSocket must use the receiver process's actual `_ws_port`, not
  receiver-local sender config. Serve `config_gui.html` through a small helper
  that injects `window.__WS_PORT__=<actual integer>` into `<head>`; keep all
  other static serving unchanged. `connectDebug()` uses that injected value,
  falling back to 8888 only if absent/invalid.
- Add temp-config merge tests and a pure/fake HTML-serving or render-helper test
  for actual WS-port injection. Do not access the real config file.

## Standalone pygame dependency

- Add `pygame` to the existing `pip install` line in `start_standalone.bat`.
  Preserve fetch/pull/install/start order and the entry point.
- Update README standalone instructions to say the launcher installs pygame;
  remove the manual pygame workaround and any now-stale limitation text.
- Add a lightweight static launcher regression test (read committed batch text
  only); do not start a server or install packages during tests.

## Preset/layout-preset transactions and atomic saves

- Change `_config_io_lock` to `threading.RLock` so public load/save helpers may
  be called while a mutation holds the same lock.
- Add one stdlib-only JSON atomic-write helper using a temp file in the target
  file's own directory, closing it before `os.replace`. Best-effort remove the
  temp file on failure. Use it for `save_presets` and
  `save_layout_presets`; preserve indent=2, `ensure_ascii=False`, paths, shapes,
  and legacy preset migration.
- Wrap each complete POST/DELETE preset and layout-preset read-modify-write
  sequence in one outer `_config_io_lock` transaction. Broadcast/log only
  after the transaction succeeds and the lock is released.
- The receiver-local sender-config merge above may use the same atomic helper.
- Add deterministic two-thread tests for both preset families and both add and
  delete/update interference. Arrange the first atomic write to wait while it
  holds the transaction, start the second mutation, prove it cannot complete
  until release, then assert neither update is lost. Do not use sleep as the
  correctness assertion.

## Sender configurable HTTP/monitor ports

- Add `http_port: 8082` and `monitor_port: 8083` to sender defaults and the
  committed example config. Preserve the keys and defaults.
- Add a pure port-normalization helper in `sender/input_sender.py`: accept an
  integer or base-10 numeric string in range 1..65535, reject bool/out-of-range/
  malformed values, and return the supplied default. Use it before starting
  both HTTP and monitor servers. Add unit tests.
- `sender_gui.html` must retain the loaded config, normalize its
  `monitor_port` equivalently (default 8083), and connect the input monitor to
  that port. Initialization must await config loading before the first monitor
  connection. Existing 2-second reconnects reuse the selected port.
- After git pull and before firewall/browser start, `start_sender.bat` must set
  default variables 8082/8083, safely read the Main PC's real
  `config/sender_config.json` if valid, validate each port as 1..65535, and
  expose only validated numeric output to batch variables. Corrupt/missing/
  malformed config must silently leave the defaults. Do not `eval` config or
  execute config text.
- Use the resulting variables for both firewall `localport` values and the
  automatically opened `http://localhost:<http_port>/` URL. Preserve admin
  elevation, rule names, pull/install/start order, and Python entry point.
- Update README/docs API text which currently says the launcher/monitor GUI are
  fixed to 8082/8083. Sender POST `/api/config` may continue to ignore
  `http_port`/`monitor_port` as documented; manual file edit + restart remains
  the way to change the listening ports.
- Add static tests for GUI use/order and batch default/config-derived variable
  use. Do not read the real config or invoke netsh/start.

## Confirmed dead code only

- Delete the earlier `switchOverlayMode` function at the current ~1155 block;
  retain the later effective definition unchanged.
- Delete only the `_standalone` declaration, `global` entry, and assignment in
  `receiver/input_server.py`. Use `_standalone_queue is not None` for restart
  cleanup; do not alter standalone branching.
- Add or update static tests if useful, but make no other dead-code cleanup.

## Cross-cutting regression coverage

- Existing tests already cover reconnect cancellation, RC lifecycle, queue
  overflow, browser/monitor clients, Raw Input, and Gamepad reset. Preserve all
  of them. New tests must use `unittest`, fake HTTP/WebSocket/native objects,
  and temp dirs only.
- No new dependency, build tooling, packaging, CI/CD, route, port default,
  protocol/payload, startup entry point, or real config access.
- Preserve resident boundedness, per-event latency, Remote Control fail-closed
  and exact input release, 60 Hz throttling, and single-file HTML GUIs.

# Non Goals

- Do not remove or repurpose `/api/sender-config`.
- Do not synchronize Main/Sub PC config automatically.
- Do not add UI controls to edit sender `http_port`/`monitor_port` through the
  sender HTTP API.
- Do not deploy, edit secretary-bot, touch startup registration, or commit/push.
- Do not archive `docs/improvements.md` items in this phase.

# Verification

Run all of:

```powershell
python -m py_compile sender\*.py receiver\*.py input_common\*.py
python -m unittest discover -s tests
python -m ruff check .
git diff --check
```

Also report `start_sender.bat` and `start_standalone.bat` smoke checks as static
tests. Real launcher execution, firewall changes, browser open, live restart,
and Main/Sub PC input checks are blocked in this workspace and must not run.

# Expected Report

Report each of the six remaining checklist groups separately, exact tests and
count, verification results, blocked live checks, and confirmation that no
real config was read/written. Stop on design ambiguity. Do not commit or push;
Codex reviews first.
