# AGENTS.md

## Purpose

This is the Codex-side working agreement for `input-relay`. It records design intent, delegation policy, review rules, and durable project constraints. `CLAUDE.md` provides compatibility guidance for implementation, verification, and reporting.

## Project Summary

- Windows input relay and OBS browser-source overlay for keyboard, mouse, and gamepad input.
- Normal operation uses resident sender and receiver processes on separate Windows PCs over a private LAN. Standalone one-PC mode is also supported.
- The receiver serves overlay/config pages and relays input. The sender captures input and exposes its local configuration and monitor interfaces.
- Remote-control mode can inject sender input at the receiver and is safety-sensitive.
- Python 3.11 with standard-library modules plus the existing `websockets`, `pynput`, and optional `pygame` dependencies.
- Receiver and sender HTTP/WebSocket interfaces bind to all local interfaces without application authentication. They are for a trusted private LAN only.

## Read First

Before meaningful work, inspect:

- `CLAUDE.md`.
- `README.md`.
- `docs/api.md` for route, payload, and port contracts.
- Relevant active records under `docs/`.
- The affected sender, receiver, shared module, launcher, example config, and tests.

## Instruction Precedence

When instructions conflict, apply them in this order:

1. Runtime, tool, organization, and safety policy.
2. Explicit user instructions that change project policy.
3. Durable project instructions.
4. Other instructions for the current user task and the approved task scope.

The active handoff or equivalent inline prompt is the approved task scope. Verified project facts override generation-source defaults. Only an explicit user instruction to change project policy may revise a durable project rule; other task instructions and approved scopes may narrow durable rules but may not weaken them. Report unresolved conflicts instead of guessing.

## Delegation and Role Policy

- The user selects the primary model at runtime. Use native Codex delegation: one `bounded_implementer` for settled, cohesive work; use `adaptive_implementer` directly when acceptance depends on unresolved native/platform or cross-layer lifecycle behavior.
- Use `bounded_explorer` only for independent read-only discovery and `bounded_reviewer` only for a concrete correctness, security, compatibility, or verification risk after the writer's stable self-review gate. If implementation changes after review, treat the review as diagnostic and request at most one fresh final review when risk warrants it.
- Keep one writer for overlapping files. A second correction round, or two blocked/partial returns, triggers a contract reset before further delegation. If a role is unavailable or its selection is unobservable, continue in the primary session or use an observable agent with equivalent constraints; Claude Code is unapproved unless the user explicitly changes this policy.
- Prefer the smallest correct change, reuse existing/platform-native capabilities, and make approval boundaries and definition of done explicit in the handoff. Verify the final diff and required checks before reporting completion.

Before implementation, classify the initial route from acceptance evidence: `small-primary` for small or transfer-negative work, `bounded` for settled multi-step work with one verifiable writer, `adaptive` when unresolved native/platform/runtime or cross-subsystem behavior is material, or `non-implementation` for analysis, design, review, or operations. Classification does not force delegation; reclassify only after a material scope change or contract reset. Name any material reviewer risk after the writer's stable self-review (pre-stable review is diagnostic only), reset the contract at the second correction round or after two blocked/partial returns, and use a fresh task boundary for an independent phase. The primary reintegrates through the stable diff and evidence rather than repeating discovery.

## Durable Project Rules

- Resident stability takes priority: keep queues and buffers bounded, reconnect with backoff, clean up tasks and input state on disconnect, and avoid uptime-dependent growth.
- Do not add blocking work to capture, relay, injection, or other per-event paths. Preserve throttling for high-frequency mouse and gamepad input.
- Preserve fail-closed remote-control behavior, stuck-key prevention, disconnect auto-disable, and mouse suppression.
- Keep the runtime stack minimal. Do not add packaging, CI/CD, a frontend build system, or new dependencies without an approved design.
- Single-file HTML interfaces are intentional.
- The `.bat` launchers own startup preparation, dependency installation, and their existing update behavior. Keep them compatible when dependencies or entry points change.
- Persistent user settings live outside the checkout; legacy `config/*.json` files are migration input only. Change committed `*.example.json` files when the configuration shape changes.
- In two-PC mode, each PC owns its local configuration. The live sender uses the sender PC's file and HTTP interface; a receiver-local sender-config copy does not configure that process.
- Keep the receiver-local sender-config endpoint for compatibility until an approved API review checks all consumers. Do not add implicit cross-PC synchronization.
- `docs/api.md` is authoritative for public routes, payloads, and default ports. Update it with any approved contract change.
- The standalone repository is the development source. Its configured origin is the normal push target; do not push a mirror unless explicitly requested.
- `secretary-bot` consumes this repository as a pinned submodule. Updating that pointer and deploying it are separate explicitly approved tasks.
- Preserve the private-LAN exposure and unauthenticated-client boundary. Do not change listen addresses, firewall behavior, ports, authentication, or internet exposure without explicit approval and design review.

## Safety and Approval Boundaries

- Preserve unrelated user and other-agent changes. Treat unexpected diffs as having unknown authorship and keep them outside the current task unless confirmed.
- Do not inspect secrets, credentials, or personal data unless their contents are strictly necessary for the approved task.
- Do not edit secrets, credentials, `.env`, local settings, production data, runtime state, or generated heavy artifacts unless the approved task explicitly requires the change.
- Never reproduce secrets, credentials, personal data, or private infrastructure values in prompts, handoffs, reports, or external tools.
- Persistent user settings and legacy `config/*.json` migration inputs, machine-specific addresses, startup registration, live input hooks, input injection, suppression state, sockets, and resident processes are protected. Inspect or operate them only when the approved task explicitly requires the corresponding live or integration work.
- Do not add dependencies or change protocols, default ports, launchers, packaging, CI/CD, deployment, submodule pointers, authentication, firewall behavior, or external exposure outside the approved task scope.
- Do not commit, push, or deploy unless explicitly requested.

## Handoff Workflow

- Keep work in Codex when its main value is policy, design, review, synthesis, read-only investigation, or a small documentation-only correction.
- For substantive implementation, create `docs/handoffs/YYYY-MM-DD-<short-task>.md` after the goal, background, files to inspect, files to edit, constraints, non-goals, data sources, acceptance criteria, verification, and expected report are clear.
- One handoff covers one cohesive, independently verifiable change and its direct regression coverage. Run unresolved discovery as a separate read-only slice.
- Size the slice so the first intended edit is reachable after reading the listed files. Do not combine broad discovery, unresolved design, and implementation.
- Treat a delegated run that ends before meeting its acceptance criteria as `status=interrupted`, even if its process exits normally. Record usable partial results, completed verification, remaining scope, and the resume condition; narrow a broad handoff before rerunning it.
- The writer implements only the approved slice; review follows the stable self-review gate before any later slice.
- Keep only active or blocked handoffs in `docs/handoffs/`. Move a handoff to `docs/handoffs/archive/` only after implementation, verification, review, required runtime work, and follow-up are complete.

## Codex Review

Verify that:

- Only approved files and behavior changed and unrelated diffs remain untouched.
- Resident stability, latency, throttling, disconnect cleanup, and remote-control safety were preserved.
- No real config, startup registration, live hook, input injection, suppression state, socket, or runtime process was touched unexpectedly.
- No dependency, protocol, port, API, launcher, authentication, firewall, deployment, submodule, or exposure change appeared outside scope.
- `docs/api.md` and example config remain synchronized with approved contract changes.
- Focused automated checks ran and any PC-specific live check is identified or reported as blocked.
- The report identifies partial edits, interrupted work, remaining scope, and its safe resume condition.

## Documentation Lifecycle

- Keep `AGENTS.md` limited to short, current, durable rules and links.
- Keep API contracts in `docs/api.md` and improvement candidates in `docs/improvements.md`.
- Put detailed decisions, evidence, rejected options, and rollout history in `docs/decisions/` when such a record is needed.
- Move a decision to `docs/decisions/archive/` only after it is fully implemented and no longer needed as current guidance.
- Keep active or blocked handoffs in `docs/handoffs/` and completed handoffs in `docs/handoffs/archive/`.
- Do not rewrite completed handoffs or archived decisions merely to match a newer shared policy.
