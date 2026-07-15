# AGENTS.md

## Purpose

This is the Codex-side working agreement for `input-relay`. It records design intent, delegation policy, review rules, and durable project constraints. `CLAUDE.md` contains Claude Code execution rules.

## Project Summary

- Windows input relay and OBS browser-source overlay for keyboard, mouse, and gamepad input.
- Normal operation uses resident sender and receiver processes on separate Windows PCs over a private LAN. Standalone one-PC mode is also supported.
- The receiver serves overlay/config pages and relays input. The sender captures input and exposes its local configuration and monitor interfaces.
- Remote-control mode can inject sender input at the receiver and is safety-sensitive.
- Python 3.11 with standard-library modules plus the existing `websockets`, `pynput`, and optional `pygame` dependencies.

## Read First

Before meaningful work, inspect:

- `CLAUDE.md`.
- `README.md`.
- `docs/api.md` for route, payload, and port contracts.
- Relevant active records under `docs/`.
- The affected sender, receiver, shared module, launcher, example config, and tests.

## Model and Role Policy

- Use GPT-5.3-Codex-Spark (`gpt-5.3-codex-spark`) proactively, when available, for low-risk, well-scoped, independently verifiable supporting work that requires no material design judgment or source-code implementation.
- GPT-5.6 Terra (`gpt-5.6-terra`) or Sol (`gpt-5.6-sol`) owns requirements and design. Whenever Terra is used, set its reasoning level to `high`. Prefer Sol for substantial ambiguity, risk, or cross-boundary reasoning.
- After design is fixed, delegate source-code implementation first to Claude Code Sonnet 5 at effort medium from the repository root.
- Only when Sonnet 5 is unavailable because of usage limits or service availability, use GPT-5.6 Luna (`gpt-5.6-luna`) with reasoning level `max` for the same implementation slice.
- Implementation failure, failed verification, or a design question is not model unavailability. Return it to Codex.
- Apply this policy to every coordinating Codex model and its subagents. Do not create coordinator-specific exceptions.
- Codex may retain requirements, design, read-only investigation, synthesis, review, and small documentation-consistency changes in one context.

## Durable Project Rules

- Resident stability takes priority: keep queues and buffers bounded, reconnect with backoff, clean up tasks and input state on disconnect, and avoid uptime-dependent growth.
- Do not add blocking work to capture, relay, injection, or other per-event paths. Preserve throttling for high-frequency mouse and gamepad input.
- Preserve fail-closed remote-control behavior, stuck-key prevention, disconnect auto-disable, and mouse suppression.
- Keep the runtime stack minimal. Do not add packaging, CI/CD, a frontend build system, or new dependencies without an approved design.
- Single-file HTML interfaces are intentional.
- The `.bat` launchers own startup preparation, dependency installation, and their existing update behavior. Keep them compatible when dependencies or entry points change.
- Real `config/*.json` files are user-local and ignored. Change committed `*.example.json` files when the configuration shape changes.
- In two-PC mode, each PC owns its local configuration. The live sender uses the sender PC's file and HTTP interface; a receiver-local sender-config copy does not configure that process.
- Keep the receiver-local sender-config endpoint for compatibility until an approved API review checks all consumers. Do not add implicit cross-PC synchronization.
- `docs/api.md` is authoritative for public routes, payloads, and default ports. Update it with any approved contract change.
- The standalone repository is the development source. Its configured origin is the normal push target; do not push a mirror unless explicitly requested.
- `secretary-bot` consumes this repository as a pinned submodule. Updating that pointer and deploying it are separate explicitly approved tasks.
- Preserve the private-LAN exposure boundary. Do not add internet exposure or authentication assumptions without design review.

## Safety and Scope

- Preserve unrelated user and other-agent changes. Treat unexpected diffs as having unknown authorship and keep them outside the current task or commit.
- Do not edit secrets, credentials, `.env` files, real local config, startup registration state, live runtime state, or generated heavy artifacts unless explicitly required.
- Do not add dependencies or change protocols, default ports, launchers, packaging, CI/CD, deployment, submodule pointers, or external exposure outside the approved scope.
- Do not commit, push, or deploy unless explicitly requested.

## Handoff Workflow

- Keep work in Codex when its main value is policy, design, review, synthesis, read-only investigation, or a small documentation-only correction.
- For substantive implementation, create `docs/handoffs/YYYY-MM-DD-<short-task>.md` with the goal, background, files to inspect, files to edit, constraints, non-goals, verification, and expected report.
- One handoff covers one cohesive, independently verifiable change and its direct regression coverage. Run unresolved discovery as a separate read-only slice.
- Size the slice so the first intended edit is reachable after reading the listed files. Do not combine broad discovery, unresolved design, and implementation.
- If a handoff times out before its intended edit, do not rerun it unchanged. Narrow the behavior, files, and verification first.
- Sonnet 5 implements only the approved slice. Luna at reasoning level `max` may implement that same slice only under the model-unavailability condition above.
- Codex reviews the report and diff before preparing a later slice. Material design questions return to Terra or Sol.
- Keep only active or blocked handoffs in `docs/handoffs/`. Move a handoff to `docs/handoffs/archive/` only after implementation, verification, review, required runtime work, and follow-up are complete.

## Codex Review

Verify that:

- Only approved files and behavior changed and unrelated diffs remain untouched.
- Resident stability, latency, throttling, disconnect cleanup, and remote-control safety were preserved.
- No real config, startup registration, live hook, socket, or runtime process was touched unexpectedly.
- No dependency, protocol, port, API, launcher, deployment, submodule, or exposure change appeared outside scope.
- `docs/api.md` and example config remain synchronized with approved contract changes.
- Focused automated checks ran and any PC-specific live check is identified or reported as blocked.

## Documentation Lifecycle

- Keep `AGENTS.md` limited to short, current, durable rules and links.
- Keep API contracts in `docs/api.md` and improvement candidates in `docs/improvements.md`.
- Put detailed decisions, evidence, rejected options, and rollout history in `docs/decisions/` when such a record is needed.
- Move a decision to `docs/decisions/archive/` only after it is fully implemented and no longer needed as current guidance.
- Keep active or blocked handoffs in `docs/handoffs/` and completed handoffs in `docs/handoffs/archive/`.
- Do not rewrite completed handoffs or archived decisions merely to match a newer shared policy.
