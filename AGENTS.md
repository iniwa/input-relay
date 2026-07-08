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

## Role Split
Codex is responsible for:
- clarifying requirements and success criteria
- preserving design intent and responsibility boundaries
- preparing concrete Claude Code handoffs (`docs/handoffs/`)
- reviewing Claude Code output against this file and the handoff
- recording durable decisions in this file or `docs/*.md`

Claude Code is responsible for:
- executing clear, scoped handoffs
- following the project `CLAUDE.md`
- running in auto mode (automatic model selection) and executing the coding
  work at Sonnet level
- returning design questions to Codex instead of deciding locally
- running requested verification where possible and reporting results

Codex may implement small or design-sensitive changes directly.

## Claude Code Model Policy
Claude Code normally runs in auto mode (automatic model selection), and coding
work is expected to be executed at Sonnet level. Codex owns design; Sonnet
owns implementation.

Consequences for Codex when writing handoffs:
- Write each handoff so a Sonnet implementer can complete it without design
  judgment: explicit goal, file scope, constraints, non-goals, verification,
  and concrete data sources.
- Resolve ambiguity in the handoff itself; do not rely on Claude Code
  escalating to a larger model or interpreting intent.
- Expect design-sensitive questions to come back as report items rather than
  local decisions.

Rules for Claude Code execution:
- Follow the handoff and `CLAUDE.md`; return design questions to Codex
  instead of deciding locally.
- Subagents are optional, used only for clearly parallelizable mechanical
  work, and inherit all handoff constraints.
- For very small edits, direct implementation without extra orchestration is
  preferred.

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

## Handoff Workflow
1. Codex reads project context, `AGENTS.md`, `CLAUDE.md`, and relevant files.
2. Codex writes a concrete handoff file under `docs/handoffs/`
   (create the directory if it does not exist).
3. The user gives that file path to Claude Code.
4. Claude Code (auto mode, Sonnet-level execution) reads the handoff file,
   implements, and reports back.
5. Codex reviews the report and/or diff.

Handoffs must state: Goal / Background / Files To Inspect / Files To Edit /
Constraints / Non Goals / Verification / Expected Report
(same template as `CLAUDEmdStrage/_base/AGENTS.md`).

## Codex Review Checklist
- Did the diff stay inside the handoff? Did constraints and non-goals hold?
- Any new dependency, build tooling, packaging, or startup-flow change?
- Any real `config/*.json`, credentials, or local settings touched?
- Resident-stability check: does the change add unbounded state, per-event
  blocking work, or an unthrottled event source?
- Did verification run (`python -m py_compile`, manual checks named in the
  handoff), and are blocked checks explained?
- Does any discovery need to become a new decision here or in `docs/*.md`?

## Knowledge Persistence
- `docs/api.md`: JSON API reference (keep in sync with the dispatch tables
  in `receiver/input_server.py` and the sender HTTP handler).
- `docs/improvements.md`: improvement checklist (check-to-implement flow).
- `docs/handoffs/`: active Codex handoffs only.

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
