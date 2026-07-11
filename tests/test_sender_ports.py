import sys
import unittest
from pathlib import Path

_SENDER_DIR = Path(__file__).resolve().parent.parent / "sender"
if str(_SENDER_DIR) not in sys.path:
    sys.path.insert(0, str(_SENDER_DIR))

import input_sender


class NormalizePortTests(unittest.TestCase):
    def test_valid_int_is_returned_as_is(self):
        self.assertEqual(input_sender.normalize_port(8082, 9999), 8082)

    def test_valid_numeric_string_is_parsed(self):
        self.assertEqual(input_sender.normalize_port("8082", 9999), 8082)

    def test_boundaries_are_inclusive(self):
        self.assertEqual(input_sender.normalize_port(1, 9999), 1)
        self.assertEqual(input_sender.normalize_port(65535, 9999), 65535)
        self.assertEqual(input_sender.normalize_port("1", 9999), 1)
        self.assertEqual(input_sender.normalize_port("65535", 9999), 65535)

    def test_out_of_range_falls_back_to_default(self):
        self.assertEqual(input_sender.normalize_port(0, 9999), 9999)
        self.assertEqual(input_sender.normalize_port(65536, 9999), 9999)
        self.assertEqual(input_sender.normalize_port(-1, 9999), 9999)

    def test_bool_is_rejected_even_though_it_is_an_int_subclass(self):
        self.assertEqual(input_sender.normalize_port(True, 9999), 9999)
        self.assertEqual(input_sender.normalize_port(False, 9999), 9999)

    def test_malformed_string_falls_back_to_default(self):
        self.assertEqual(input_sender.normalize_port("8082.0", 9999), 9999)
        self.assertEqual(input_sender.normalize_port("+8082", 9999), 9999)
        self.assertEqual(input_sender.normalize_port("-1", 9999), 9999)
        self.assertEqual(input_sender.normalize_port("abc", 9999), 9999)
        self.assertEqual(input_sender.normalize_port("", 9999), 9999)
        self.assertEqual(input_sender.normalize_port(" ", 9999), 9999)

    def test_none_and_other_types_fall_back_to_default(self):
        self.assertEqual(input_sender.normalize_port(None, 9999), 9999)
        self.assertEqual(input_sender.normalize_port([8082], 9999), 9999)
        self.assertEqual(input_sender.normalize_port(3.5, 9999), 9999)

    def test_whitespace_padded_string_is_trimmed(self):
        self.assertEqual(input_sender.normalize_port(" 8082 ", 9999), 8082)


class SenderConfigDefaultsTests(unittest.TestCase):
    def test_defaults_include_http_and_monitor_port(self):
        self.assertEqual(input_sender._CONFIG_DEFAULTS["http_port"], 8082)
        self.assertEqual(input_sender._CONFIG_DEFAULTS["monitor_port"], 8083)


if __name__ == "__main__":
    unittest.main()
