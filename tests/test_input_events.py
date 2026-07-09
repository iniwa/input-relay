import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from input_common.input_events import get_vk, key_to_str, make_event


class FakeKey:
    """Minimal stand-in for a pynput key/keycode object.

    Only sets the attributes explicitly requested, so hasattr() checks in
    key_to_str/get_vk behave the same way they would for a real pynput
    object that lacks that attribute.
    """

    def __init__(self, vk=None, char=None, name=None, value=None):
        if vk is not None:
            self.vk = vk
        if char is not None:
            self.char = char
        if name is not None:
            self.name = name
        if value is not None:
            self.value = value


class KeyToStrTests(unittest.TestCase):
    def test_vk_digit_normalization(self):
        self.assertEqual(key_to_str(FakeKey(vk=0x30)), "0")
        self.assertEqual(key_to_str(FakeKey(vk=0x39)), "9")

    def test_vk_letter_normalization(self):
        self.assertEqual(key_to_str(FakeKey(vk=0x41)), "a")
        self.assertEqual(key_to_str(FakeKey(vk=0x5A)), "z")

    def test_char_fallback(self):
        self.assertEqual(key_to_str(FakeKey(char="X")), "x")

    def test_name_fallback(self):
        self.assertEqual(key_to_str(FakeKey(name="space")), "space")

    def test_vk_code_fallback(self):
        # vk outside the A-Z/0-9 ranges, no char/name attribute available
        # (e.g. Japanese IME keys) falls back to a "vk_<code>" identifier.
        self.assertEqual(key_to_str(FakeKey(vk=244)), "vk_244")


class GetVkTests(unittest.TestCase):
    def test_direct_vk_attribute(self):
        self.assertEqual(get_vk(FakeKey(vk=65)), 65)

    def test_value_vk_fallback(self):
        self.assertEqual(get_vk(FakeKey(value=FakeKey(vk=66))), 66)

    def test_no_vk_available(self):
        self.assertIsNone(get_vk(FakeKey(name="ctrl")))


class MakeEventTests(unittest.TestCase):
    def test_returns_valid_json_with_required_fields(self):
        raw = make_event("keydown", "a", source="keyboard")
        data = json.loads(raw)
        self.assertEqual(data["type"], "keydown")
        self.assertEqual(data["key"], "a")
        self.assertEqual(data["source"], "keyboard")
        self.assertIn("timestamp", data)
        self.assertNotIn("vk", data)

    def test_optional_vk_included_when_given(self):
        raw = make_event("keydown", "a", source="keyboard", vk=65)
        data = json.loads(raw)
        self.assertEqual(data["vk"], 65)


if __name__ == "__main__":
    unittest.main()
