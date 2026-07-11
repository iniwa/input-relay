import re
import unittest
from pathlib import Path

# overlay.html is a single-file HTML/JS GUI with no build step and no JS
# test harness (no node/pytest-js dependency in this project's stack), so
# these are static source-level regression assertions rather than executed
# JS tests. See docs/handoffs/2026-07-11-sender-disconnect-browser-input-reset.md.
OVERLAY_PATH = Path(__file__).resolve().parent.parent / "receiver" / "overlay.html"


def _read_overlay():
    return OVERLAY_PATH.read_text(encoding="utf-8")


def _function_body(source, name):
    """Return the full brace-matched body (including braces) of a top-level
    `function name() { ... }` declaration in the given JS source text."""
    marker = f"function {name}("
    start = source.index(marker)
    brace_start = source.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return source[brace_start:i + 1]


class OverlayInputResetStaticTests(unittest.TestCase):
    def setUp(self):
        self.source = _read_overlay()

    def test_onmessage_dispatches_input_reset_to_reset_function(self):
        match = re.search(
            r"data\.type === 'input_reset'\)\s*\{\s*([^}]*)\}",
            self.source,
        )
        self.assertIsNotNone(match, "no input_reset branch found in ws.onmessage")
        self.assertIn("resetDisplayedInput()", match.group(1))

    def test_reset_function_clears_all_displayed_input_state(self):
        body = _function_body(self.source, "resetDisplayedInput")
        # Pending display-delay timers must be cancelled immediately.
        self.assertIn("displayDelayTimers.forEach(clearTimeout)", body)
        self.assertIn("displayDelayTimers = []", body)
        # Afterglow timers cancelled and their DOM classes cleared.
        self.assertIn("clearTimeout(id)", body)
        self.assertIn("classList.remove('afterglow')", body)
        # Pressed-key DOM state cleared.
        self.assertIn("classList.remove('active')", body)
        # Pressed/direction/axis state cleared via the shared helper.
        self.assertIn("clearInputState()", body)
        # Controller stick/trigger visuals refreshed to neutral.
        self.assertIn("updateStickVisuals()", body)
        # Mouse-trail accumulated points reset without stopping the loop.
        self.assertIn("trailPoints = []", body)
        self.assertNotIn("cancelAnimationFrame", body)

    def test_reset_function_does_not_rebuild_layout_or_add_history(self):
        body = _function_body(self.source, "resetDisplayedInput")
        self.assertNotIn("buildLayout()", body)
        self.assertNotIn("recordCurrentState()", body)

    def test_clear_input_state_helper_shared_by_mode_switch(self):
        body = _function_body(self.source, "clearInputState")
        self.assertIn("pressedKeys.clear()", body)
        self.assertIn("dirState = {", body)
        self.assertIn("axisState = {}", body)

        mode_switch_match = re.search(
            r"data\.type === 'mode_switch'\)\s*\{\s*([^}]*)\}",
            self.source,
        )
        self.assertIsNotNone(mode_switch_match, "no mode_switch branch found")
        self.assertIn("clearInputState()", mode_switch_match.group(1))
        self.assertIn("buildLayout()", mode_switch_match.group(1))


if __name__ == "__main__":
    unittest.main()
