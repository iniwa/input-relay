# CLAUDE.md

## Purpose

This file contains Claude Code execution rules for `input-relay`. `AGENTS.md` owns design intent, delegation policy, and Codex review.

## Read Before Editing

Read:

- `AGENTS.md`.
- The supplied handoff, when present.
- `README.md`, `docs/api.md`, and every file listed for inspection.
- Relevant active records under `docs/`.

## Project Facts

- Python 3.11 Windows application with resident sender and receiver processes plus a standalone mode.
- The sender captures keyboard, mouse, and gamepad input. The receiver serves OBS overlays and configuration pages and can inject approved remote-control input.
- Public route, payload, and default-port contracts are documented in `docs/api.md`.
- Runtime dependencies are intentionally limited to standard-library modules and the existing `websockets`, `pynput`, and optional `pygame` packages.
- The HTML interfaces are intentionally build-free single files.

## Execution Rules

- Implement and report only the current independently verifiable slice.
- A handoff defines task scope but does not override durable constraints in `AGENTS.md`.
- If the listed files are insufficient to reach the first scoped edit, stop and report the missing discovery or proposed split instead of broadening the task.
- Return unresolved requirements and design choices to Codex.
- Stop before adding a dependency or changing protocols, default ports, launchers, packaging, CI/CD, deployment, submodule pointers, or external exposure unless the task explicitly includes it.
- Subagents are optional and limited to clearly parallel mechanical work within the same files, scope, and constraints.
- Preserve unrelated user and other-agent changes. Treat unexpected diffs as having unknown authorship and exclude them from the current task.
- Do not commit, push, or deploy unless explicitly requested.

## Implementation Constraints

- Keep resident queues, buffers, caches, and tasks bounded and clean them up on disconnect or shutdown.
- Do not add blocking work to input capture, relay, injection, or other per-event paths. Preserve high-frequency throttling.
- Preserve fail-closed remote-control behavior, exact pressed-input cleanup, disconnect auto-disable, and mouse suppression.
- Preserve direct-script entry points and keep the `.bat` launchers compatible with dependency or entry-point changes.
- Do not introduce a frontend build system.
- Follow existing Python and single-file HTML patterns before adding abstractions.
- When routes, payloads, or default ports change within an approved task, update `docs/api.md` and direct regression coverage in the same slice.
- In two-PC mode, do not treat a receiver-local sender-config copy as live sender configuration or add implicit synchronization.

## Protected Files and State

Do not edit, delete, or inspect contents unless explicitly required:

- Real `config/*.json` files. Change committed `*.example.json` templates when a configuration shape changes.
- Secrets, credentials, `.env` files, keys, and local settings.
- Startup registration state.
- Live hooks, sockets, resident processes, and runtime state.
- Generated heavy artifacts.

## Verification

Run the smallest relevant checks:

- Compile each touched Python file explicitly with `python -m py_compile <files>`.
- Run focused unit tests when available, then `python -m unittest discover -s tests` for a broad code change.
- Run `python -m ruff check .` when Ruff is available in the established development environment.
- Run `git diff --check`.
- For documentation-only changes, use `git diff --check` and a focused reference scan.
- If verification requires a live sender, receiver, hook, socket, OBS page, or startup registration, identify the required process role and report the check as blocked when that environment is unavailable.

Tests must not read or write real config, open live input hooks or network services, or alter startup registration unless the handoff explicitly authorizes an integration check.

## Report

Report:

- Changed files.
- Concise summary.
- Verification commands and results.
- Blocked checks.
- Subagent usage.
- Design questions for Codex.
