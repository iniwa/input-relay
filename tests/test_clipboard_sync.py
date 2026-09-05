import json
import unittest

from input_common import clipboard_sync


class ClipboardProtocolTests(unittest.TestCase):
    def test_unicode_text_round_trips_without_normalization(self):
        epoch = "00000000-0000-4000-8000-000000000001"
        text = "日本語\r\n\t😀  "
        message = {
            "type": "clipboard_propose",
            "epoch": epoch,
            "origin_seq": 1,
            "text": text,
        }
        self.assertEqual(
            clipboard_sync.decode_message(clipboard_sync.encode_message(message)),
            message,
        )

    def test_utf8_limit_is_exact_and_never_truncated(self):
        clipboard_sync.validate_text("a" * clipboard_sync.MAX_TEXT_BYTES)
        with self.assertRaises(clipboard_sync.ProtocolError):
            clipboard_sync.validate_text("a" * (clipboard_sync.MAX_TEXT_BYTES + 1))

    def test_bool_is_not_accepted_as_integer(self):
        with self.assertRaises(clipboard_sync.ProtocolError):
            clipboard_sync.validate_message({
                "type": "clipboard_set", "request_id": True, "enabled": False,
            })

    def test_invalid_unicode_nul_and_unknown_fields_are_rejected(self):
        epoch = "00000000-0000-4000-8000-000000000001"
        for text in ("has\x00nul", "\ud800"):
            with self.subTest(text=repr(text)), self.assertRaises(clipboard_sync.ProtocolError):
                clipboard_sync.validate_text(text)
        with self.assertRaises(clipboard_sync.ProtocolError):
            clipboard_sync.validate_message({
                "type": "clipboard_propose",
                "epoch": epoch,
                "origin_seq": 1,
                "text": "safe",
                "unexpected": "field",
            })

    def test_oversized_json_is_rejected_before_parse(self):
        raw = json.dumps({"type": "x", "padding": "x" * clipboard_sync.MAX_MESSAGE_BYTES})
        with self.assertRaisesRegex(clipboard_sync.ProtocolError, "400 KiB"):
            clipboard_sync.decode_message(raw)


class RevisionCoordinatorTests(unittest.TestCase):
    def test_receiver_ordering_converges_and_deduplicates_per_origin(self):
        coordinator = clipboard_sync.RevisionCoordinator()
        epoch = coordinator.enable()
        first = coordinator.propose(
            epoch=epoch, origin="main", origin_seq=1, text="main",
        )
        second = coordinator.propose(
            epoch=epoch, origin="sub", origin_seq=1, text="sub",
        )
        duplicate = coordinator.propose(
            epoch=epoch, origin="main", origin_seq=1, text="ignored",
        )
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertEqual(second.text, "sub")
        self.assertIsNone(duplicate)

    def test_stale_epoch_is_ignored_after_disable_and_reenable(self):
        coordinator = clipboard_sync.RevisionCoordinator()
        old_epoch = coordinator.enable()
        coordinator.disable()
        new_epoch = coordinator.enable()
        self.assertNotEqual(old_epoch, new_epoch)
        self.assertIsNone(coordinator.propose(
            epoch=old_epoch, origin="main", origin_seq=2, text="stale",
        ))


class LatestMailboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_value_is_bounded_to_latest_one(self):
        mailbox = clipboard_sync.LatestMailbox()
        for value in range(100):
            mailbox.put_latest(value)
        self.assertEqual(await mailbox.get(), 99)
        self.assertTrue(mailbox.empty())


if __name__ == "__main__":
    unittest.main()
