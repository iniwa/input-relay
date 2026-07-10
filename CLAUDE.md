# CLAUDE.md

Execution rules for Claude Code working on `input-relay`.
`AGENTS.md` is the Codex-side source of design intent, handoff rules, and
review criteria — read it before non-trivial work.

## Communication
- The user writes in Japanese; respond in Japanese.
- Keep reports concise and factual.

## Codex / Claude Code Workflow
- Codex normally delegates implementation by running this command from
  `D:/Git/input-relay`:

  ```powershell
  claude -p --model sonnet --permission-mode auto "<handoff/task prompt>"
  ```

- Codex handoffs live under `docs/handoffs/`; when a handoff path is
  provided, read it before editing and follow it first, then this file,
  then surrounding project patterns.
- Active handoffs are stored at the `docs/handoffs/` root. After Codex review,
  completed handoffs move to `docs/handoffs/archive/`.
- If the task is ambiguous, requires changing documented design intent, or
  needs files outside the handoff, stop and report before editing.
- Small, clearly-scoped fixes may be requested directly without a handoff.

## Model / Subagent Policy
- Standard execution is Sonnet 5 via `--model sonnet` with
  `--permission-mode auto`; treat `sonnet` as the current Sonnet alias.
- Codex handoffs are written for Sonnet-level execution: goal, file scope,
  constraints, non-goals, and verification are explicit enough that
  implementation requires no design judgment.
- If a handoff still requires a design decision, has an ambiguous scope, or
  conflicts with this file, stop and return the question to Codex.
- Subagents are optional, not the default; they inherit all handoff
  constraints.

## Architecture (keep in mind when editing)
- Two **resident** Windows processes, auto-started at login:
  - `sender/input_sender.py` on Main PC (192.168.1.210): captures
    keyboard (pynput) / raw mouse (60Hz flush) / gamepad (pygame 60Hz),
    sends events to the receiver over WebSocket, serves a config HTTP API
    (8082) and a monitor WS (8083).
  - `receiver/input_server.py` on Sub PC (192.168.1.211): WebSocket server
    (8888) relaying events to OBS browser-source overlays, HTTP server
    (8081) for overlay pages / config GUI / JSON API, and remote-control
    input injection (`input_injector.py`).
- Standalone 1-PC mode: `input_server.py --standalone` with
  `standalone_capture.py`.
- In normal 2PC operation the Main and Sub PCs have separate local config
  files. The live sender reads the Main PC's `sender_config.json`; receiver
  `/api/sender-config` only reads/writes the receiver PC's local copy.
- `docs/api.md` is the authoritative API reference; update it when changing
  routes (dispatch tables in `receiver/input_server.py`, `SenderHTTPHandler` in
  `sender/http_api.py`, monitor WS in `sender/monitor_ws.py`).

## Coding Rules
- Resident stability first: no unbounded queues/dicts tied to uptime, no
  per-event blocking work on the capture/relay path, keep 60Hz throttling.
- Preserve remote-control safety behavior: stuck-key prevention, auto
  disable on disconnect, mouse suppression (`ll_mouse_hook.py`).
- Stack stays minimal: stdlib + `websockets` + `pynput` (+ `pygame`).
  No new dependencies, build tooling, packaging, or CI/CD unless the
  handoff explicitly allows it.
- Single-file HTML GUIs are intentional; no frontend build system.
- Keep `.bat` launchers working when changing dependencies or entry points.
- Python 3.11-compatible code.

## Protected Files
Do not edit or delete unless explicitly requested:
- real `config/*.json` (user-local, gitignored; edit `*.example.json`
  instead when config shape changes)
- `startup/` registration state on the PCs

## Verification
- `python -m py_compile sender/*.py receiver/*.py input_common/*.py`
- `python -m unittest discover -s tests`
- `python -m ruff check .` when ruff is available in the local dev environment
- `git diff --check`
- If a check needs a live sender/receiver, name which PC it runs on and report
  it as blocked if it cannot run.

## Git / Deployment
- Workspace: `D:/Git/input-relay`. The only push remote is
  `origin = gitea:iniwa/input-relay`. Do not push to the GitHub mirror
  unless explicitly requested.
- After completed work, commit and push proactively (no need to wait for
  an instruction).
- Deployment to the Sub PC agent goes through secretary-bot: after pushing
  here, bump the `windows-agent/tools/input-relay` submodule pointer in
  `D:/Git/secretary-bot`, push, then `POST /api/update-code` on the Pi.

## Knowledge Persistence
- `docs/api.md`: JSON API reference (keep in sync with routes).
- `docs/improvements.md`: improvement checklist (check-to-implement flow).
- `docs/handoffs/archive/`: completed handoffs retained for implementation
  history; do not treat them as active work.
- Durable design decisions go to `AGENTS.md` (Codex reviews them).

## Tooling
- Use **Serena MCP** tools for code navigation and editing (symbol search,
  overview, replace, insert, etc.)
- Use **Tavily MCP** tools for web search and research when needed.
