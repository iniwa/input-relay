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

Default to primary design, implementation, related discovery, verification, corrections, and final acceptance at any task size. Delegate autonomously within existing authority only when replacing primary work lowers expected total effort, including handoff, communication, waiting, integration, verification, and corrections, or a named material risk or existing mandatory independent verification gate warrants it. Size or technical uncertainty alone is insufficient; routine direct work needs no per-task justification.

- Before implementation, decide whether to delegate, then choose the role and initial route: `small-primary` for direct work of any size, `bounded` for a settled delegated outcome, `adaptive` for delegated material technical uncertainty, or `non-implementation` for analysis, design, review, or operations. Reclassify only after a material scope change or contract reset.
- The user chooses the primary runtime model and effort. The primary owns interpretation, material design, authority, integration, final acceptance, and communication. Use configured roles without inherited history or model/effort overrides where supported. If selection is unavailable or unobservable, use the primary or an observable equivalent and record only exposed execution facts.
- When delegation meets the rule, use one `bounded_implementer` for settled cohesive work, `adaptive_implementer` directly for material unresolved native/platform or cross-layer acceptance uncertainty, and `bounded_explorer` only for independently valuable read-only discovery that is not cheap for the writer to perform. Do not force a predictable bounded-writer failure first.
- Only the primary delegates; children do not redelegate or invoke Claude Code. Choose parent permissions first, respect live overrides, and do not mix legacy sandbox settings with permission profiles. Read-only roles remain read-only even with write tools. Keep one writer for overlapping files or behavior.
- Settle the outcome, protected behavior, authority, acceptance mechanics, and focused and required affected checks before delegation. Ordinary delegation uses a short inline task; persist a handoff only for cross-session, interruption-sensitive, operationally risky, or separately executed work. The writer owns related discovery, implementation, verification, and corrections.
- Before acceptance review, self-review the stable diff against every criterion, relevant reference, and protected regression; run the required checks and return per-item passed/blocked/unmet evidence. Unchecked required items are not success. Candidate changes invalidate acceptance review; restabilize before a fresh final review if risk or a mandatory gate still warrants it.
- Use `bounded_reviewer` only for a named material risk or an existing mandatory independent review gate. Localized low-risk documents normally need self-review only. Normally use one reviewer; a second needs a distinct material risk, an unusable/blocked first review, or an existing mandatory multi-reviewer gate. Record the reason and preserve those mandatory gates.
- Consolidate findings for the same writer; integrate from stable diffs and evidence without repeating discovery merely to restore context. Keep one outcome and its corrections together; use a fresh task boundary for an independent phase with separate acceptance and verification.
- While children run, continue useful work within ownership and parallelism rules or wait for notifications. Do not add research/checks, inspect changing candidates, or repeat liveness polling, rereads, or state updates merely to fill the wait. Respond to errors, inconsistent state, user steering, and host progress rules.
- The primary may reclaim work of any size before correction thresholds when direct execution lowers remaining total effort or delegation is unavailable, after confirming child writes stopped and ownership returned, then resetting acceptance, protected boundaries, authority, environment, and evidence.
- At the second correction round for one outcome, or after two blocked/partial implementation returns caused by unresolved acceptance, authority, or environment, pause corrective delegation and reset that contract. Choose primary execution, or justified delegation to the same bounded writer if still bounded or an adaptive writer for material technical uncertainty. Resolve missing authority with user input and keep substantive corrections with one selected writer. Do not weaken verification or abandon safe blocked work.

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

Personal-use iteration is the default unless the user or verified project
requirements establish stronger obligations. Make the smallest normal-path
change, use a brief useful check, perform routine reversible
deployment/application and any necessary restart through the known existing
user-controlled target and procedure, smoke normal use, fix observed errors,
and finish when normal operation works. Do not require speculative edge-case
coverage, hardening, abstractions, new tests, an offline harness, or a full
suite for ordinary changes. Required live-input, security, data, and approval
gates remain in force; a required pre-application review receives a stable
source/diff candidate before runtime application. The initial implementation or
fix request supplies standing permission for this bounded routine cycle, so no
fresh confirmation is needed. This does not infer Git commit/push/merge,
publication/release/registry or hosted-config changes, credentials/permissions/
exposure, destructive data or migrations, new targets or cost, or
project-specific protected operations. If a target or check is unavailable,
report readiness separately; record only required deferred checks in the
existing issue or ledger with verification, approval, and resume conditions.

- Preserve unrelated user and other-agent changes. Treat unexpected diffs as having unknown authorship and keep them outside the current task unless confirmed.
- Do not inspect secrets, credentials, or personal data unless their contents are strictly necessary for the approved task.
- Do not edit secrets, credentials, `.env`, local settings, production data, runtime state, or generated heavy artifacts unless the approved task explicitly requires the change.
- Never reproduce secrets, credentials, personal data, or private infrastructure values in prompts, handoffs, reports, or external tools.
- Persistent user settings and legacy `config/*.json` migration inputs, machine-specific addresses, startup registration, live input hooks, input injection, suppression state, sockets, and resident processes are protected. Inspect or operate them only when the approved task explicitly requires the corresponding live or integration work.
- Do not add dependencies or change protocols, default ports, launchers, packaging, CI/CD, deployment procedure or configuration, submodule pointers, authentication, firewall behavior, or external exposure outside the approved task scope.
- Do not commit or push unless explicitly requested. Routine reversible deployment/application and necessary restart may use the bounded personal-use allowance above on the established target and known procedure; other deployment requires explicit authorization. The separate secretary-bot submodule and its deployment gate remain independently controlled.

## Handoff Workflow

- Default to primary execution under the delegation rule above; ordinary delegation uses a compact inline task.
- For work requiring durable resume conditions, create `docs/handoffs/YYYY-MM-DD-<short-task>.md` after the goal, background, files, constraints, non-goals, data sources, acceptance criteria, verification, and expected report are clear.
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
