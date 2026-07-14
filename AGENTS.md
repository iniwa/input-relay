# AGENTS.md

## Purpose
This file is the Codex-side document for `input-relay`: design intent,
handoff rules, and review criteria.

`CLAUDE.md` records execution rules for Claude Code.

For periodic project maintenance (improvement-candidate survey and
AGENTS.md / CLAUDE.md / README.md refresh), follow
`CLAUDEmdStrage/_base/PROJECT_MAINTENANCE_ja.md`.

## Project Summary
- Project name: input-relay (Input Display / OBS Overlay)
- Purpose: show keyboard / gamepad / mouse input as an OBS browser-source
  overlay (SF6-style leverless layout + input history), with an optional
  remote-control mode that injects Main PC input into the Sub PC.
- Primary users: the owner (single user, home LAN only).
- Runtime target: two resident Python processes on Windows 11 —
  `sender` on Main PC (192.168.1.210), `receiver` on Sub PC (192.168.1.211).
  Standalone 1-PC mode also exists.
- Repository path: `D:/Git/input-relay` (standalone workspace).
- Remote: `gitea:iniwa/input-relay` (origin). A GitHub mirror exists but is
  not pushed from this workspace.
- Deployment: consumed by `secretary-bot` as a git submodule
  (`windows-agent/tools/input-relay`) pinned to a commit; Sub PC receives
  updates when the submodule pointer is bumped in secretary-bot.

## Environment Selection
- `D:/Git/` -> Home Sub PC / `C:/Git/` -> Home Main PC.
- sender runs on Main PC, receiver runs on Sub PC. Runtime checks that need
  a live process must name which PC they run on.

## Role Split / Model Policy
- GPT-5.6 Terra (`gpt-5.6-terra`) or Sol (`gpt-5.6-sol`) owns requirements and design. Prefer Sol for substantial ambiguity, risk, or cross-boundary reasoning.
- After design is fixed, GPT-5.6 Luna Max (`gpt-5.6-luna-max`) coordinates implementation through small, sequential handoffs: one independently verifiable route, subsystem boundary, or lifecycle path plus its direct regression tests.
- Claude Code Sonnet 5 performs delegated edits and verification at effort medium from the repository root: `claude -p --model sonnet --permission-mode auto "<handoff/task prompt>"`.
- Handoffs state the goal, files, constraints, non-goals, verification, and concrete data sources so Sonnet needs no design judgment. Claude Code implements only the current slice and returns design questions to Codex.
- Luna Max reviews each result before preparing the next slice. Material design questions return to Terra/Sol instead of changing the approved design.
- Codex may keep small or design-sensitive changes in one context. Fable 5 is only a medium-effort second opinion for difficult design decisions.
- Claude Code subagents are optional and limited to clearly parallel mechanical work; they inherit the handoff and may not expand scope, change design, add dependencies, alter deployment or external exposure, or touch secrets.
- When GPT-5.6 Sol Ultra (`gpt-5.6-sol-ultra`) delegates work to a subagent, use GPT-5.6 Luna Max (`gpt-5.6-luna-max`) or GPT-5.6 Terra (`gpt-5.6-terra`). When using Terra, set its reasoning level from low through high; do not use a level outside that range.
- On Windows, keep delegated command lines ASCII-only, put non-ASCII instructions in a UTF-8 handoff file, and close background `codex exec` stdin with `$null |`. If an intended model is unavailable, use an available model only when the work remains safe and report the limitation.

## Project-Specific Design Principles
- Both processes are **resident** (auto-started at login). Stability over
  features: bounded queues/buffers, reconnect with backoff, cleanup on
  disconnect, no unbounded growth tied to uptime.
- Input path is latency-sensitive (overlay display and remote-control
  injection). Do not add per-event blocking work on the capture or relay
  path; high-frequency sources stay throttled (raw mouse / gamepad at 60Hz).
- Keep the stack minimal: stdlib + `websockets` + `pynput` (+ `pygame` for
  gamepad). No build tooling, no packaging, no CI/CD.
- Single-file HTML GUIs (`config_gui.html`, `overlay.html`, `sender_gui.html`)
  are intentional; do not introduce a frontend build system.
- The `.bat` launchers own dependency installation and `git pull`; keep them
  compatible when changing dependencies or entry points.
- Remote-control mode is safety-sensitive: stuck-key prevention, auto-disable
  on disconnect, and mouse suppression behavior must be preserved.
- User-local settings live in `config/*.json` (gitignored; `*.example.json`
  is the committed template). Never commit real config files.
- In 2PC mode, each PC owns its local config. The live sender reads the Main
  PC's `config/sender_config.json`; a receiver-side copy cannot configure the
  Main PC process. Use the sender HTTP GUI/API for live sender changes.

## Handoff Workflow
1. Codex reads project context, `AGENTS.md`, `CLAUDE.md`, and relevant files.
2. Codex writes a concrete handoff file under `docs/handoffs/`
   (create the directory if it does not exist).
3. Codex invokes Claude Code non-interactively from the repo root, normally:
   `claude -p --model sonnet --permission-mode auto "<handoff path + task>"`
4. Claude Code (Sonnet 5 via `--model sonnet`, permission auto) reads the
   handoff file, implements, and reports back.
5. Codex reviews the report and/or diff.
6. After review is complete, move the handoff to `docs/handoffs/archive/`;
   keep only active handoffs at the `docs/handoffs/` root.

Handoffs must state: Goal / Background / Files To Inspect / Files To Edit /
Constraints / Non Goals / Verification / Expected Report
(same template as `CLAUDEmdStrage/_base/AGENTS.md`).

## Codex Review Checklist
- Did the diff stay inside the handoff? Did constraints and non-goals hold?
- Any new dependency, build tooling, packaging, or startup-flow change?
- Any real `config/*.json`, credentials, or local settings touched?
- Resident-stability check: does the change add unbounded state, per-event
  blocking work, or an unthrottled event source?
- Did verification run (`python -m py_compile`, `python -m unittest discover -s tests`, `python -m ruff check .` where applicable, and manual checks named in the handoff),
  and are blocked checks explained?
- Does any discovery need to become a new decision here or in `docs/*.md`?

## Knowledge Persistence
- `docs/api.md`: JSON API reference (keep in sync with receiver dispatch
  tables, `sender/http_api.py`, and `sender/monitor_ws.py`).
- `docs/improvements.md`: improvement checklist (check-to-implement flow).
- `docs/handoffs/`: active Codex handoffs at the directory root; completed
  handoffs under `docs/handoffs/archive/`.

## Decision Log

### 2026-07-08: Standalone workspace + Gitea-only remote

Context:
- Work used to happen inside the secretary-bot submodule checkout, which
  mixes parent-repo state with tool development.

Decision:
- Development happens in the standalone clone `D:/Git/input-relay`.
- The only push remote for this workspace is `gitea:iniwa/input-relay`.
- secretary-bot consumes the tool as a submodule pinned to a commit; after
  pushing here, bump the submodule pointer in secretary-bot to deploy to the
  Sub PC agent.
- `CLAUDE.md` / `AGENTS.md` are tracked in this repo (the old
  `.gitignore` entry treating `CLAUDE.md` as personal config was removed).

Constraints Introduced:
- Do not push to the GitHub mirror from this workspace unless explicitly
  requested.

Do Not Change Casually:
- Remote layout, submodule consumption model, ports (8081 receiver HTTP,
  8888 receiver WS, 8082 sender HTTP, 8083 sender monitor WS — see
  `docs/api.md` for authoritative values).

### 2026-07-11: Per-PC sender config ownership

Context:
- Main PC and Sub PC use separate workspaces and separate gitignored config
  files. The receiver's `/api/sender-config` endpoint writes only the Sub PC
  workspace, while the resident sender reads the Main PC workspace.

Decision:
- The Main PC sender GUI/API (default port 8082) is the live configuration
  path for the sender process.
- Receiver-local `sender_config.json` data must be described as a local copy,
  not as live control of the Main PC sender.
- Keep the receiver `/api/sender-config` contract for compatibility until a
  dedicated handoff checks all external consumers; do not add implicit config
  synchronization between PCs.

Constraints Introduced:
- A sender config UI must identify which PC/file it changes.
- Removing or repurposing `/api/sender-config` requires API review and a
  `docs/api.md` update.
