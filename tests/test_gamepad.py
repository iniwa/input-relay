import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from input_common import gamepad as gamepad_mod
from input_common.gamepad import Gamepad


class FakeJoystick:
    """Stand-in for pygame.joystick.Joystick: no SDL, no real hardware."""

    def __init__(self, idx, name="FakePad", buttons=None, axes=None, hats=None):
        self.idx = idx
        self._name = name
        self.buttons = list(buttons if buttons is not None else [])
        self.axes = list(axes if axes is not None else [])
        self.hats = list(hats if hats is not None else [])
        self.inited = False

    def init(self):
        self.inited = True

    def get_name(self):
        return self._name

    def get_numbuttons(self):
        return len(self.buttons)

    def get_button(self, i):
        return self.buttons[i]

    def get_numhats(self):
        return len(self.hats)

    def get_hat(self, i):
        return self.hats[i]

    def get_numaxes(self):
        return len(self.axes)

    def get_axis(self, i):
        return self.axes[i]


class FakeJoystickModule:
    """Stand-in for pygame.joystick."""

    def __init__(self, controllers=None):
        self._controllers = list(controllers or [])
        self.quit_calls = 0

    def init(self):
        pass

    def quit(self):
        self.quit_calls += 1

    def get_count(self):
        return len(self._controllers)

    def Joystick(self, idx):
        return self._controllers[idx]


class FakePygame:
    """Stand-in for the top-level pygame module."""

    def __init__(self, controllers=None):
        self.joystick = FakeJoystickModule(controllers)
        self.event = self
        self.pump_calls = 0
        self.quit_calls = 0

    def pump(self):
        self.pump_calls += 1

    def init(self):
        pass

    def quit(self):
        self.quit_calls += 1


class NeutralizeStateTests(unittest.TestCase):
    """_reset_joy (used for disconnect and refresh-triggered switch) must
    emit the exact key_up for every active button/hat/threshold-axis and
    axis_update(0) for every non-neutral tracked raw axis, before clearing
    the buffers and releasing the joystick reference."""

    def setUp(self):
        self.emitted = []
        self.gp = Gamepad(emit_callback=self.emitted.append, is_running=lambda: True)

    def _events(self):
        return [json.loads(m) for m in self.emitted]

    def test_disconnect_neutralizes_active_buttons_hats_axes(self):
        state = {
            "joy": object(), "joy_id": 0,
            "prev_buttons": {0: 1, 1: 0, 2: 1},
            "prev_axes": {0: -1, 1: 1, "hat_0": (1, -1)},
            "prev_axes_raw": {0: -0.8, 1: 0.75, 2: 0.0},
            "last_reinit": 0.0,
        }

        self.gp._reset_joy(state)

        events = self._events()
        keys_up = {e["key"] for e in events if e["type"] == "key_up"}
        self.assertEqual(keys_up, {
            "btn_0", "btn_2",
            "hat_0_right", "hat_0_down",
            "axis_0_neg", "axis_1_pos",
        })
        axis_updates = {(e["axis"], e["value"]) for e in events if e["type"] == "axis_update"}
        # axis 2's raw value was already 0 -> no redundant axis_update.
        self.assertEqual(axis_updates, {(0, 0), (1, 0)})

        self.assertIsNone(state["joy"])
        self.assertEqual(state["prev_buttons"], {})
        self.assertEqual(state["prev_axes"], {})
        self.assertEqual(state["prev_axes_raw"], {})

    def test_neutral_state_emits_nothing(self):
        state = {
            "joy": object(), "joy_id": 0,
            "prev_buttons": {0: 0},
            "prev_axes": {0: 0, "hat_0": (0, 0)},
            "prev_axes_raw": {0: 0.0},
            "last_reinit": 0.0,
        }
        self.gp._reset_joy(state)
        self.assertEqual(self.emitted, [])
        self.assertIsNone(state["joy"])


class SwitchNeutralizationTests(unittest.TestCase):
    """The direct controller-switch path (selected_id() differs from the
    currently polled joystick while both are already connected) must
    neutralize the outgoing controller's buffered state before the new
    joystick is assigned -- previously this path never neutralized at all
    and swapped the joystick reference before clearing buffers."""

    def test_switch_emits_key_up_before_assigning_new_joystick(self):
        emitted = []
        gp = Gamepad(emit_callback=emitted.append, is_running=lambda: True)
        j0 = FakeJoystick(0, buttons=[0])   # matches prev_buttons below (no re-emit from poll)
        j1 = FakeJoystick(1, buttons=[0])
        gp._pygame = FakePygame(controllers=[j0, j1])
        gp.select(1)

        state = {
            "joy": j0, "joy_id": 0,
            "prev_buttons": {0: 1},  # button 0 is currently held down
            "prev_axes": {},
            "prev_axes_raw": {},
            "last_reinit": 0.0,
        }

        calls = {"n": 0}

        def is_running():
            calls["n"] += 1
            return calls["n"] <= 1

        gp._is_running = is_running
        gp._loop(state)

        events = [json.loads(m) for m in emitted]
        self.assertEqual(events, [{
            "type": "key_up", "key": "btn_0", "source": "gamepad",
            "timestamp": events[0]["timestamp"],
        }])
        self.assertIs(state["joy"], j1)
        self.assertEqual(state["joy_id"], 1)
        self.assertEqual(state["prev_buttons"], {})


class RecoveryAfterExceptionTests(unittest.TestCase):
    """Gamepad.run() must survive exceptions raised from pygame
    init/scan/event-pump/joystick creation-init/getters: tear down the
    failed session best-effort, neutralize any buffered state, and retry
    with exponential backoff while is_running() stays true. A session that
    reaches the polling loop resets the backoff for the next failure."""

    def test_backoff_resets_after_session_reaches_polling(self):
        # Three sessions: (1) fails immediately at init -- backoff escalates
        # 0.1 -> 0.2 for the *next* failure; (2) reaches polling then fails
        # mid-session -- because it reached polling, backoff must reset to
        # 0.1 instead of continuing to escalate; (3) fails immediately at
        # init again -- confirms escalation still works normally (0.1 -> 0.2)
        # starting fresh from the reset value.
        counters = {"init_calls": 0, "pump_calls": 0}

        class FlakyPygame:
            def __init__(self):
                self.joystick = FakeJoystickModule([FakeJoystick(0, buttons=[], axes=[], hats=[])])
                self.event = self

            def init(self):
                counters["init_calls"] += 1
                if counters["init_calls"] in (1, 3):
                    raise RuntimeError("transient init failure")

            def pump(self):
                counters["pump_calls"] += 1
                # Session 2's second pump call fails, simulating a
                # mid-session transient failure *after* polling was reached
                # (on_polling already fired on the first pump of session 2).
                if counters["init_calls"] == 2 and counters["pump_calls"] == 2:
                    raise RuntimeError("transient pump failure")

            def quit(self):
                pass

        fake_pg = FlakyPygame()
        orig_pygame = sys.modules.get("pygame")
        sys.modules["pygame"] = fake_pg

        call_count = {"n": 0}

        def is_running():
            call_count["n"] += 1
            # True for calls 1-8 (session1 outer+post-check, session2
            # outer+2 inner checks+post-check, session3 outer+post-check),
            # False afterward so no session4 starts.
            return call_count["n"] <= 8

        emitted = []
        gp = Gamepad(emit_callback=emitted.append, is_running=is_running)

        sleep_calls = []
        try:
            with patch.object(gamepad_mod.time, "sleep", lambda s: sleep_calls.append(s)), \
                 patch.object(gamepad_mod.logger, "exception") as mock_log_exc:
                gp.run()  # must not raise
        finally:
            if orig_pygame is not None:
                sys.modules["pygame"] = orig_pygame
            else:
                sys.modules.pop("pygame", None)

        self.assertEqual(counters["init_calls"], 3)
        self.assertEqual(mock_log_exc.call_count, 3)  # all three sessions failed

        # Backoff sleeps are >= _BACKOFF_INITIAL; _POLL_INTERVAL (~0.0167s)
        # sleeps from session2's successful poll iteration are much smaller,
        # so this filter isolates the retry-backoff waits specifically.
        backoff_sleeps = [s for s in sleep_calls if s >= gamepad_mod._BACKOFF_INITIAL]
        self.assertEqual(len(backoff_sleeps), 3)
        self.assertAlmostEqual(backoff_sleeps[0], gamepad_mod._BACKOFF_INITIAL)  # session1 -> session2
        self.assertAlmostEqual(backoff_sleeps[1], gamepad_mod._BACKOFF_INITIAL)  # reset: session2 reached polling
        self.assertAlmostEqual(backoff_sleeps[2], gamepad_mod._BACKOFF_INITIAL * 2)  # normal escalation again


class InitFailingPygame(FakePygame):
    """pygame stand-in whose pg.init() raises -- used to verify that
    Gamepad._load_pygame assigns self._pygame before calling pg.init(), so a
    partial-init failure still leaves something for teardown to act on."""

    def init(self):
        raise RuntimeError("pg.init failed")


class JoystickQuitFailingModule(FakeJoystickModule):
    def quit(self):
        self.quit_calls += 1
        raise RuntimeError("joystick quit failed")


def _run_one_session(gp, extra_running_calls=0):
    """Run Gamepad.run() for exactly one outer session: is_running() is True
    just long enough for the session body (and its inner loop, if any) to
    execute once, then False so run() returns without a retry sleep."""
    calls = {"n": 0}
    limit = 1 + extra_running_calls

    def is_running():
        calls["n"] += 1
        return calls["n"] <= limit

    gp._is_running = is_running
    with patch.object(gamepad_mod.time, "sleep", lambda s: None), \
         patch.object(gamepad_mod.logger, "exception"):
        gp.run()


class SessionTeardownTests(unittest.TestCase):
    """Gamepad.run()'s outer per-session finally must: neutralize buffered
    state (best-effort), clear all three state buffers and joy regardless of
    whether neutralization raised, and always tear down pygame -- including
    when init only partially succeeded or a teardown step itself fails."""

    def setUp(self):
        self._orig_pygame = sys.modules.get("pygame")

    def tearDown(self):
        if self._orig_pygame is not None:
            sys.modules["pygame"] = self._orig_pygame
        else:
            sys.modules.pop("pygame", None)

    def test_session_failure_clears_buffers_and_joy_reference(self):
        fake_pg = FakePygame(controllers=[])
        sys.modules["pygame"] = fake_pg
        emitted = []
        gp = Gamepad(emit_callback=emitted.append, is_running=lambda: True)

        captured = {}

        def raising_loop(state, on_polling=None):
            state["joy"] = object()
            state["prev_buttons"][0] = 1
            state["prev_axes"][1] = 1
            state["prev_axes_raw"][1] = 0.9
            captured["state"] = state
            raise RuntimeError("session boom")

        gp._loop = raising_loop
        _run_one_session(gp)

        state = captured["state"]
        self.assertIsNone(state["joy"])
        self.assertEqual(state["prev_buttons"], {})
        self.assertEqual(state["prev_axes"], {})
        self.assertEqual(state["prev_axes_raw"], {})
        # The buffered button/axis were neutralized before being cleared.
        events = [json.loads(m) for m in emitted]
        types_keys = {(e["type"], e.get("key")) for e in events}
        self.assertIn(("key_up", "btn_0"), types_keys)
        self.assertIn(("key_up", "axis_1_pos"), types_keys)

    def test_partial_init_failure_is_still_torn_down(self):
        fake_pg = InitFailingPygame(controllers=[])
        sys.modules["pygame"] = fake_pg
        gp = Gamepad(emit_callback=lambda m: None, is_running=lambda: True)

        _run_one_session(gp)

        # pg.init() raised, but _load_pygame assigns self._pygame first, so
        # teardown still has a reference to act on.
        self.assertEqual(fake_pg.quit_calls, 1)
        self.assertEqual(fake_pg.joystick.quit_calls, 1)
        self.assertIsNone(gp._pygame)

    def test_teardown_continues_after_neutral_emission_failure(self):
        fake_pg = FakePygame(controllers=[])
        sys.modules["pygame"] = fake_pg
        gp = Gamepad(emit_callback=lambda m: None, is_running=lambda: True)
        gp._scan = lambda: []  # isolate teardown's own joystick.quit() call
        gp._loop = lambda state, on_polling=None: (_ for _ in ()).throw(RuntimeError("session boom"))
        gp._neutralize_state = lambda state: (_ for _ in ()).throw(RuntimeError("neutralize boom"))

        _run_one_session(gp)  # must not raise

        self.assertEqual(fake_pg.quit_calls, 1)
        self.assertEqual(fake_pg.joystick.quit_calls, 1)
        self.assertIsNone(gp._pygame)

    def test_teardown_continues_after_joystick_quit_failure(self):
        fake_pg = FakePygame(controllers=[])
        fake_pg.joystick = JoystickQuitFailingModule([])
        sys.modules["pygame"] = fake_pg
        gp = Gamepad(emit_callback=lambda m: None, is_running=lambda: True)
        gp._scan = lambda: []  # bypass mid-session scan so joystick.quit() is only hit by teardown
        gp._loop = lambda state, on_polling=None: None  # session succeeds, no exception

        _run_one_session(gp)  # must not raise despite joystick.quit() failing

        self.assertEqual(fake_pg.joystick.quit_calls, 1)
        self.assertEqual(fake_pg.quit_calls, 1)  # global quit still ran
        self.assertIsNone(gp._pygame)


if __name__ == "__main__":
    unittest.main()
