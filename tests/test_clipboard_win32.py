import threading
import time
import unittest
import ctypes

from input_common import clipboard_win32


class NativeReadFixture:
    """Minimal native backend fixture backed by a real ctypes allocation."""

    def __init__(self, raw):
        self.buffer = ctypes.create_string_buffer(raw, len(raw))
        self.closed = 0
        self.unlocked = 0
        self.user32 = self.User32(self)
        self.kernel32 = self.Kernel32(self)
        self.hwnd = 1
        self.should_continue = lambda: True

    class User32:
        def __init__(self, owner):
            self.owner = owner

        def IsClipboardFormatAvailable(self, _format):
            return True

        def OpenClipboard(self, _hwnd):
            return True

        def GetClipboardData(self, _format):
            return 1

        def CloseClipboard(self):
            self.owner.closed += 1
            return True

    class Kernel32:
        def __init__(self, owner):
            self.owner = owner

        def GlobalSize(self, _handle):
            return len(self.owner.buffer.raw)

        def GlobalLock(self, _handle):
            return ctypes.addressof(self.owner.buffer)

        def GlobalUnlock(self, _handle):
            self.owner.unlocked += 1
            return True

    def read_text(self):
        backend = object.__new__(clipboard_win32._Win32Backend)
        backend.user32 = self.user32
        backend.kernel32 = self.kernel32
        backend.hwnd = self.hwnd
        backend.should_continue = self.should_continue
        return backend.read_text()


class FakeBackend:
    instances = []

    def __init__(self):
        type(self).instances.append(self)
        self.notify = None
        self.seq = 10
        self.text = None
        self.writes = []
        self.stopped = False

    def start(self, notify):
        self.notify = notify

    def pump(self):
        pass

    def stop(self):
        self.stopped = True

    def sequence(self):
        return self.seq

    def read_text(self):
        return self.text

    def write_text(self, text):
        self.writes.append(text)
        self.seq += 1
        return self.seq

    def copy(self, text):
        self.text = text
        self.seq += 1
        self.notify()


class ClipboardWorkerTests(unittest.TestCase):
    def setUp(self):
        FakeBackend.instances.clear()
        self.local = []
        self.local_event = threading.Event()
        self.errors = []
        self.worker = clipboard_win32.ClipboardWorker(
            self._on_local,
            lambda epoch, code: self.errors.append((epoch, code)),
            backend_factory=FakeBackend,
        )

    def tearDown(self):
        self.worker.stop()

    def _on_local(self, *value):
        self.local.append(value)
        self.local_event.set()

    def test_backend_is_not_constructed_until_explicit_start(self):
        self.assertEqual(FakeBackend.instances, [])
        self.assertTrue(self.worker.start())
        self.assertEqual(len(FakeBackend.instances), 1)

    def test_native_read_accepts_nonzero_allocation_after_first_utf16_nul(self):
        raw = "logical text".encode("utf-16-le") + b"\x00\x00\x01\x02\x03\x04"
        fixture = NativeReadFixture(raw)
        self.assertEqual(fixture.read_text(), "logical text")
        self.assertEqual(fixture.closed, 1)
        self.assertEqual(fixture.unlocked, 1)

    def test_off_discards_notifications_without_reading_text(self):
        self.worker.start()
        backend = FakeBackend.instances[0]
        backend.copy("private-before-on")
        time.sleep(0.06)
        self.assertEqual(self.local, [])

    def test_enable_baselines_existing_content_then_publishes_new_copy(self):
        self.worker.start()
        backend = FakeBackend.instances[0]
        backend.text = "old"
        self.worker.enable("epoch")
        time.sleep(0.06)
        backend.copy("日本語\r\n\t😀")
        self.assertTrue(self.local_event.wait(1.0))
        self.assertEqual(self.local[0][0], "epoch")
        self.assertEqual(self.local[0][1], 1)
        self.assertEqual(self.local[0][3], "日本語\r\n\t😀")

    def test_remote_write_reflection_is_ignored_by_sequence(self):
        self.worker.start()
        backend = FakeBackend.instances[0]
        self.worker.enable("epoch")
        time.sleep(0.06)
        self.worker.submit_write("epoch", 1, "remote")
        deadline = time.monotonic() + 1
        while not backend.writes and time.monotonic() < deadline:
            time.sleep(0.01)
        backend.notify()
        time.sleep(0.06)
        self.assertEqual(backend.writes, ["remote"])
        self.assertEqual(self.local, [])

    def test_write_mailbox_never_exceeds_one_pending_item(self):
        self.worker.start()
        self.worker.enable("epoch")
        for i in range(100):
            self.worker.submit_write("epoch", i, str(i))
            self.assertLessEqual(self.worker.pending_count(), 1)


class BlockingBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def read_text(self):
        self.release.wait()
        return self.text


class ClipboardWatchdogTests(unittest.TestCase):
    def test_hung_native_call_disables_without_spawning_a_replacement_worker(self):
        BlockingBackend.instances.clear()
        errors = []
        error_event = threading.Event()

        def on_error(epoch, code):
            errors.append((epoch, code))
            error_event.set()

        worker = clipboard_win32.ClipboardWorker(
            lambda *_value: None,
            on_error,
            backend_factory=BlockingBackend,
            operation_timeout=0.05,
        )
        self.assertTrue(worker.start())
        backend = BlockingBackend.instances[0]
        ready = worker.enable("epoch")
        self.assertTrue(ready.wait(1))
        backend.copy("blocked")
        self.assertTrue(error_event.wait(1))
        self.assertEqual(errors, [("epoch", "worker_failed")])
        self.assertFalse(worker.stop(timeout=0.01))
        self.assertFalse(worker.start())
        self.assertEqual(len(BlockingBackend.instances), 1)
        backend.release.set()
        deadline = time.monotonic() + 1
        while not worker.stop(timeout=0.05) and time.monotonic() < deadline:
            pass
        self.assertTrue(backend.stopped)


if __name__ == "__main__":
    unittest.main()
