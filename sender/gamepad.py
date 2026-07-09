"""Compatibility wrapper.

The actual gamepad polling implementation moved to input_common/gamepad.py
so receiver/standalone_capture.py can share it. This module re-exports it
so existing `import gamepad as gamepad_mod` (direct `python
sender/input_sender.py` execution, sender/ on sys.path[0]) keeps working.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from input_common.gamepad import (  # noqa: F401 (re-exported for compatibility)
    AXIS_EPS,
    DEADZONE,
    DISCONNECT_SLEEP,
    POLL_HZ,
    RESCAN_INTERVAL,
    Gamepad,
)
