# Goal

Finalize `docs/improvements.md` after all 13 current improvement candidates
have been implemented and independently verified by Codex.

# Background

All code/API/GUI/launcher/test changes from the phase-1 and phase-2 handoffs
are present in the working tree. Codex reviewed the diffs, added two small
consistency fixes (atomic legacy-preset migration and receiver debug-port upper
bound), and independently ran the final verification listed below. The
checklist's own operating rule says completed items move from 改善候補 to
完了アーカイブ.

# Files To Inspect

- `AGENTS.md`, `CLAUDE.md`
- `docs/improvements.md`
- archived handoffs:
  - `docs/handoffs/archive/2026-07-11-all-improvements-phase-1-resident-stability.md`
  - `docs/handoffs/archive/2026-07-11-phase-1-review-fixes.md`
  - `docs/handoffs/archive/2026-07-11-all-improvements-phase-2-api-launcher-gui.md`
- current `git diff --stat` only as needed to confirm file names

# Files To Edit

- `docs/improvements.md` only

# Constraints

- Remove all 13 implemented entries from `## 改善候補`. Leave an explicit
  Japanese statement that there are currently no remaining candidates.
- Add one dated `2026-07-11` completion archive section (or clearly grouped
  subsections under that date) covering every former candidate by its original
  title:
  1. sender monitor queue/send boundedness
  2. receiver browser fan-out/RC independence
  3. standalone bounded queue and overflow input-reset policy
  4. Raw Input fallback/resource cleanup
  5. shared Gamepad neutralize/retry
  6. browser registration finally cleanup
  7. receiver DELETE restart contract/one-shot/safe cleanup
  8. receiver-local sender config ownership/UI/merge/8888/debug port
  9. standalone launcher pygame dependency
  10. preset/layout-preset transaction + atomic save
  11. sender variable HTTP/monitor ports end-to-end
  12. central resident-flow regression tests
  13. duplicate `switchOverlayMode` and unused `_standalone` removal
- Summaries must be concise but factual: name key files/behavior and the tests
  that protect them. Record the monitor close review fix (bounded concurrent
  close), Raw Input `GetModuleHandleW` cleanup edge, Gamepad partial-init/
  teardown edge, atomic legacy migration, and receiver debug 1..65535 check.
- Record final verification exactly:
  - explicit Python modules compiled with `python -m py_compile`: OK
  - `python -m unittest discover -s tests`: OK, 130 tests
  - `python -m ruff check .`: OK
  - `git diff --check`: OK (line-ending warnings only, no errors)
- Record blocked live checks: real Main-PC sender/Sub-PC receiver input,
  physical gamepad/Raw Input, actual WebSocket slow clients, launcher package
  installation/firewall/browser open, and real receiver restart were not run.
- Record that no real `config/*.json` was read/written; only example config and
  temp/fake/static tests were used.
- Preserve all older completion archive entries exactly. Do not rewrite or
  delete historical records.
- Do not edit code, README, API docs, AGENTS/CLAUDE, tests, config, or handoffs.
- Do not re-run live checks, commit, or push.

# Non Goals

- No new improvement survey or new candidate invention.
- No code cleanup or wording changes outside the current-candidate move and
  new completion record.

# Verification

```powershell
git diff --check
```

Do not rerun the full code suite; Codex already ran it after the final code
changes and will rerun/inspect as needed after this documentation-only edit.

# Expected Report

Confirm all 13 titles were moved, older archive history was preserved, the
final verification/live-block/config facts were recorded, and only
`docs/improvements.md` changed in this turn. Do not commit or push.
