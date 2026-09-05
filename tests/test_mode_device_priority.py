import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
_SENDER_DIR = Path(__file__).resolve().parent.parent / "sender"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))
if str(_SENDER_DIR) not in sys.path:
    sys.path.insert(0, str(_SENDER_DIR))

import input_server
import http_api


class _FakeSender:
    remote_address = ("127.0.0.1", 0)

    def __init__(self):
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(json.loads(message))


class ReceiverModeControlTests(unittest.TestCase):
    def setUp(self):
        with input_server._display_mode_lock:
            self._old_mode = input_server._display_mode
            input_server._display_mode = "keyboard"
        self._old_sender = input_server.sender_ws
        input_server.sender_ws = None

    def tearDown(self):
        with input_server._display_mode_lock:
            input_server._display_mode = self._old_mode
        input_server.sender_ws = self._old_sender

    def test_mode_switch_is_validated_persisted_for_connection_and_forwarded(self):
        forwarded = []
        with mock.patch.object(input_server, "_notify_sender_async", forwarded.append):
            result = input_server._api_post_mode_switch(None, b'{"mode":"leverless"}')
        self.assertEqual(result, {"ok": True})
        self.assertEqual(forwarded[0]["key"], "leverless")

        sender = _FakeSender()
        asyncio.run(input_server.sender_handler(sender))
        self.assertEqual(sender.sent[0]["type"], "mode_switch")
        self.assertEqual(sender.sent[0]["key"], "leverless")

    def test_invalid_mode_is_rejected_without_changing_current_mode(self):
        with self.assertRaises(input_server.ApiError):
            input_server._api_post_mode_switch(None, b'{"mode":"invalid"}')
        with input_server._display_mode_lock:
            self.assertEqual(input_server._display_mode, "keyboard")


class DeviceIdentityValidationTests(unittest.TestCase):
    def test_guid_or_complete_fallback_signature_is_required(self):
        self.assertEqual(http_api._valid_device_identity({"guid": "abc"}), {"guid": "abc"})
        fallback = {"name": "Pad", "buttons": 12, "axes": 4, "hats": 1}
        self.assertEqual(http_api._valid_device_identity(fallback), fallback)
        self.assertIsNone(http_api._valid_device_identity({"guid": ""}))
        self.assertIsNone(http_api._valid_device_identity({"name": "Pad"}))
        self.assertIsNone(http_api._valid_device_identity(
            {"name": "Pad", "buttons": True, "axes": 4, "hats": 1},
        ))

    def test_preference_payload_requires_explicit_valid_device_key(self):
        self.assertEqual(
            http_api._parse_mode_device_preference(
                {"mode": "controller", "device": {"guid": "abc"}},
            ),
            ("controller", {"guid": "abc"}),
        )
        for payload in (
            {},
            {"mode": "controller"},
            {"mode": "keyboard", "device": None},
            {"mode": ["controller"], "device": None},
            {"mode": "controller", "device": {"guid": ""}},
            [],
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    http_api._parse_mode_device_preference(payload)


class ModeDevicePreferenceHandlerTests(unittest.TestCase):
    """Exercise the endpoint handler without a live HTTP listener."""

    def _invoke(self, body):
        config = {}
        saved = []
        gamepad = mock.Mock()
        ctx = http_api.SenderContext(
            gui_path=Path(__file__),
            get_config=lambda: config,
            save_config=lambda data: saved.append(dict(data)),
            trigger_reconnect=lambda: None,
            get_gamepad=lambda: gamepad,
            valid_overlay_positions=(),
            get_ws_status=lambda: "disconnected",
            get_remote_mode=lambda: False,
            get_clipboard_status=lambda: {
                "state": "off", "enabled": False, "reason": "user",
            },
            get_input_timestamps=lambda: (0.0, 0.0),
        )
        handler_cls = http_api.make_handler(ctx)
        handler = object.__new__(handler_cls)
        responses = []
        handler._read_body = lambda: body
        handler._send_json = lambda payload, status=200: responses.append((status, payload))
        handler._handle_mode_device_preference()
        return config, saved, gamepad, responses

    def test_missing_device_key_is_rejected_without_mutation(self):
        config, saved, gamepad, responses = self._invoke({"mode": "controller"})
        self.assertEqual(config, {})
        self.assertEqual(saved, [])
        gamepad.set_mode_preference.assert_not_called()
        self.assertEqual(responses[0][0], 400)

    def test_valid_device_identity_is_persisted_and_applied(self):
        config, saved, gamepad, responses = self._invoke(
            {"mode": "leverless", "device": {"guid": "preferred"}},
        )
        self.assertEqual(config["mode_device_preferences"], {
            "leverless": {"guid": "preferred"},
        })
        self.assertEqual(len(saved), 1)
        gamepad.set_mode_preference.assert_called_once_with(
            "leverless", {"guid": "preferred"},
        )
        self.assertEqual(responses, [(200, {
            "ok": True, "mode": "leverless", "device": {"guid": "preferred"},
        })])


if __name__ == "__main__":
    unittest.main()
