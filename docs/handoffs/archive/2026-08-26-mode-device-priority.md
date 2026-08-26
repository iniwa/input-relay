# Main PC mode-specific input-device priority

## Goal

Allow the Main PC sender to persist a preferred physical input device for the
`controller` and `leverless` overlay modes. When the receiver switches display
mode, the sender must select the matching preferred device automatically.

## Background

The receiver currently broadcasts `mode_switch` only to overlay browsers. The
Main PC sender therefore has no mode information and uses a single transient
pygame controller index. Device indices may change after a rescan.

## Scope

- Propagate a validated display-mode control message from receiver to sender,
  including the current mode for a newly connected sender.
- Persist one device identity per `controller` and `leverless` mode in the
  Main PC `sender_config.json`; use device GUID when available and otherwise a
  unique name/capability signature.
- Add Main PC GUI controls to choose or clear each mode preference.
- Resolve preferences after scans and on mode changes. If a configured
  preference is absent or ambiguous, do not fall back to a different device:
  neutralize the active device and capture no gamepad input until a unique
  match returns or the preference is cleared.
- Preserve the existing manual device selection API and all keyboard, mouse,
  remote-control, throttling, and disconnect safety behavior.

## Files to inspect and edit

- `input_common/gamepad.py`
- `sender/input_sender.py`
- `sender/http_api.py`
- `sender/sender_gui.html`
- `receiver/input_server.py`
- `config/sender_config.example.json`
- `docs/api.md`
- focused tests under `tests/`

## Constraints

- Do not inspect or alter real configuration, running processes, input hooks,
  ports, firewall rules, or deployment state.
- Use no new dependencies. Keep selection changes off input-event hot paths.
- Reject invalid mode/device preference payloads; do not broaden LAN exposure.
- Preserve unowned edits to `README.md` and `docs/improvements.md`.

## Acceptance criteria

1. The Main PC GUI can save or clear one preferred device for each supported
   gamepad display mode.
2. A receiver mode switch reaches a connected sender, and reconnecting sender
   receives the current mode.
3. Preference resolution survives index changes when a unique identity match
   exists; missing or ambiguous preferences capture no different device.
4. Existing unconfigured setups retain manual-selection behavior.
5. Config example and API documentation describe the new persisted shape and
   control-plane behavior.
6. Focused tests, the full unittest suite, Python compilation, and
   `git diff --check` pass.
