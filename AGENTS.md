# AGENTS.md

## Purpose and authority

`input-relay` is a Windows input relay and OBS browser-source overlay. Resident
sender/receiver processes support standalone mode and two-PC private-LAN mode;
the sender captures keyboard, mouse, gamepad, and clipboard events, while the
receiver serves overlays/configuration and can inject approved remote input.
HTTP/WebSocket endpoints are intentionally unauthenticated and must remain on
the trusted private LAN.

This file is the current entry. `CLAUDE.md` is a thin compatibility pointer;
`README.md` describes operation and `docs/api.md` is authoritative for public
routes, payloads, and default ports. Read relevant handoffs/decisions and
affected code, launchers, examples, and tests before changing a contract.
Persistent settings are outside the checkout; `config/*.json` is migration
input only. The standalone repository is the development source; the
Secretary Bot checkout consumes a pinned submodule and its pointer/deployment
gate is separate.

Apply runtime, tool, organization, and safety policy first, then explicit user
policy and current task prohibitions/edit limits, durable rules, approved scope,
and verified facts. Task limits narrow standing permissions; facts do not
authorize operations. Preserve unrelated work, retained history, and one
writer for overlapping files. Do not commit, push, change the submodule,
deploy, or alter a live process without the required authorization.

## Durable runtime and safety contracts

- Keep resident queues, buffers, caches, and tasks bounded. Reconnect with
  backoff; clean up per-client tasks and pressed-input state on disconnect and
  shutdown. Do not block capture, relay, injection, or other per-event paths;
  preserve mouse/gamepad throttling and high-frequency coalescing.
- Remote control is safety-sensitive: preserve fail-closed connection state,
  exact pressed-input cleanup, disconnect auto-disable, cursor/input
  suppression, and the overlay indicating the active target. Do not weaken
  mouse suppression for unsupported background Raw Input games.
- Clipboard sharing is a separate capability: only explicitly enabled plain
  text may cross PCs, within the existing UTF-8 bound. Reject images, files,
  rich text, empty/oversized payloads, stale revisions, and malformed events;
  return sharing to OFF on startup, reconnect, disconnect, or process restart.
  Clipboard state must not change remote-control state.
- Preserve standalone and two-PC modes, direct-script entry points, `.bat`
  launcher preparation/update behavior, existing API routes/payloads/default
  ports, and single-file build-free HTML. A receiver-local sender-config copy
  never configures the live sender; do not add implicit synchronization.
- Keep all interfaces unauthenticated and private-LAN only. Do not change
  listen addresses, firewall behavior, ports, authentication, or exposure.
  Keep machine-specific addresses, accounts, paths, and settings in ignored
  local configuration.

## Scope and protected state

Choose the smallest reversible repository-local change and reuse existing
patterns. Do not inspect or edit secrets, credentials, personal data, real
settings, startup registration, live hooks, sockets, injection/suppression
state, resident processes, or generated heavy artifacts unless explicitly
required. Do not add dependencies, protocols, packaging, frontend build,
CI/CD, deployment procedure, or runtime configuration outside approved scope.
Do not add blocking work to input paths. Tests must not use real configuration,
live hooks, network services, or startup registration.

Routine bounded reversible personal-use work may follow the established known
procedure on the existing target: brief useful check, apply, normal-use smoke,
and correction of observed errors. This does not authorize protected live
input, network, exposure, deployment, credential, data, or repository actions;
a task prohibition overrides the standing allowance. Unavailable target checks
are reported separately as blocked, never passed.

## Work routing and review

Classify work as `small-primary`, `bounded`, `adaptive`, or
`non-implementation`; classification does not force delegation. Configuration
owns model, effort, and role-specific instructions; the user's runtime choice
remains authoritative. The primary owns interpretation, approvals, integration,
and communication. Use one configured writer for settled cohesive work; use an
explorer only for independent read-only discovery and a reviewer only for a
named material correctness, security, compatibility, or verification risk.
Parent permissions and live overrides remain authoritative; read-only roles
stay read-only. If role selection is unavailable or unobservable, use the
primary or an observable equivalent and record the route. Delegated agents do
not redelegate; Claude Code is not an approved route.

The writer performs related discovery, implementation, verification, and minor
corrections through a stable self-review. Review begins only after stability;
any candidate change invalidates it and requires restabilization. At the second
correction round, or after two qualifying blocked/partial returns, reset
acceptance, authority, permissions, environment, and evidence, then choose one
writer. Do not create a handoff for a small documentation correction; retain
active/blocked handoffs and archive only after the full lifecycle is complete.

## Verification and completion

For documentation-only work, verify changed references, API/launcher/example
consistency, Markdown fences, and `git diff --check`. For code, compile touched
Python files, run focused tests when available, then the broad unittest suite
for a broad change; run Ruff when available. Tests must remain offline and must
not auto-update golden expectations. Live sender/receiver, hook, socket, OBS,
or startup checks are separate and require their named environment.

Finish only with a stable task-owned diff, preserved safety/API/lifecycle
contracts, and evidence for every required criterion. Report changed files,
material effects, commands and outcomes, blocked/unmet checks, partial work,
and exact resume conditions. Keep API details in `docs/api.md`, improvements in
`docs/improvements.md`, and rationale/history in decisions or handoffs.
For incomplete delegated work, report the blocker, resume condition, and next owner/action. Requested model or effort is configuration context, not execution evidence; unknown stays unknown, with no diagnostic-only agents or probes to fill observation fields. After stable-diff review, read deeper only for gaps, conflicts, or concrete risk; rerun checks only for a mandatory contract, changed target or assumption, insufficient evidence, or integration risk. Return concise results and evidence references without raw logs or unchanged inventories. While children run, continue useful work within existing ownership and parallelism rules; otherwise wait for notifications. Avoid liveness-only polling, rereads, or state rewrites; respond to errors, inconsistent state, and user steering, and follow host progress rules.
