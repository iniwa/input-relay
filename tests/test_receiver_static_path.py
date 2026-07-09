import sys
import unittest
from pathlib import Path

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

# input_server does not open sockets or start any server at import time
# (that only happens under `if __name__ == "__main__"` / main()), so
# importing it here is safe.
import input_server


def _resolve(path):
    # OverlayHandler is a BaseHTTPRequestHandler subclass whose __init__
    # expects a live request/socket; _resolve_static_path only touches
    # OVERLAY_DIR and the given path, so we can call it on an
    # uninitialized instance.
    handler = object.__new__(input_server.OverlayHandler)
    return handler._resolve_static_path(path)


class ResolveStaticPathTests(unittest.TestCase):
    def test_normal_file_resolves_under_receiver_dir(self):
        resolved = _resolve("overlay.html")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_relative_to(input_server.OVERLAY_DIR.resolve()))
        self.assertTrue(resolved.exists())

    def test_relative_traversal_outside_overlay_dir_is_rejected(self):
        self.assertIsNone(_resolve("../config/config.json"))

    def test_absolute_windows_path_is_rejected(self):
        self.assertIsNone(_resolve("C:/Windows/win.ini"))


if __name__ == "__main__":
    unittest.main()
