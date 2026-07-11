"""
Gamepad capture loop.

pygame.joystick で接続されたコントローラを 60Hz で polling し、ボタン /
ハット / 軸の状態変化をイベントとしてコールバックへ流す。HTTP API から
コントローラ選択 / 再スキャンを依頼するため、状態は Gamepad クラスに
まとめてスレッド間で共有する。

sender/input_sender.py（選択/再スキャン API 経由の常駐運用）と
receiver/standalone_capture.py（1PC 常駐、選択 API 未使用で常に
controller 0 を使う）の両方から使われる共有実装。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("gamepad")

POLL_HZ = 60
DEADZONE = 0.5
AXIS_EPS = 0.01            # raw 軸の最小変化幅
RESCAN_INTERVAL = 2.0      # 切断時の再 init 間隔 (秒)
DISCONNECT_SLEEP = 0.1     # 接続なし時のスリープ (秒)
_AXIS_RAW_SENTINEL = 2.0   # 軸範囲 (-1..1) 外、初回判定用
_POLL_INTERVAL = 1.0 / POLL_HZ
_BACKOFF_INITIAL = 0.1     # session 失敗時の初期リトライ待ち (秒)
_BACKOFF_MAX = 2.0         # リトライ待ちの上限 (秒)


class Gamepad:
    """Threaded gamepad poller. emit_callback(json_str) を通じてイベント送信。"""

    def __init__(self, emit_callback: Callable[[str], None], is_running: Callable[[], bool]):
        self._emit = emit_callback
        self._is_running = is_running
        self._lock = threading.Lock()
        self._selected_id = 0
        self._request_refresh = False
        self._info: list[dict] = []
        self._pygame = None  # 遅延 import

    # --- public API (thread-safe) ---
    def selected_id(self) -> int:
        with self._lock:
            return self._selected_id

    def info(self) -> list[dict]:
        with self._lock:
            return list(self._info)

    def select(self, controller_id: int) -> None:
        with self._lock:
            self._selected_id = controller_id

    def request_refresh(self) -> None:
        with self._lock:
            self._request_refresh = True

    def _consume_refresh(self) -> bool:
        with self._lock:
            pending = self._request_refresh
            self._request_refresh = False
        return pending

    # --- internal ---
    def _load_pygame(self):
        import pygame as pg
        self._pygame = pg
        pg.init()
        pg.joystick.init()

    def _scan(self) -> list[dict]:
        pg = self._pygame
        if pg is None:
            return []
        pg.joystick.quit()
        pg.joystick.init()
        controllers = []
        for i in range(pg.joystick.get_count()):
            try:
                j = pg.joystick.Joystick(i)
                j.init()
                controllers.append({
                    "id": i,
                    "name": j.get_name(),
                    "buttons": j.get_numbuttons(),
                    "axes": j.get_numaxes(),
                    "hats": j.get_numhats(),
                })
            except Exception:
                logger.debug("scan: skipping joystick %d", i, exc_info=True)
        with self._lock:
            self._info = controllers
        return controllers

    def _emit_btn(self, name: str, is_down: bool) -> None:
        etype = "key_down" if is_down else "key_up"
        self._emit(json.dumps({
            "type": etype, "key": name, "source": "gamepad",
            "timestamp": time.time(),
        }))

    def run(self) -> None:
        """Blocking poll loop. Run in a daemon thread.

        Each iteration of this outer loop is one pygame "session": init,
        scan, then poll until either `is_running()` goes false (shutdown) or
        something raises (pygame init/scan/event-pump/joystick
        creation-init/getters are all uncaught by design inside `_loop`).
        A failed session is torn down best-effort, any buffered controller
        state is neutralized (so a stuck-looking key/axis on the overlay
        never survives a crash), and — while still running — retried with
        exponential backoff (0.1s to 2.0s). Backoff resets to 0.1s once a
        session has proven itself by actually reaching the polling loop.
        """
        backoff = _BACKOFF_INITIAL
        while self._is_running():
            state = {
                "joy": None,
                "joy_id": -1,
                "prev_buttons": {},
                "prev_axes": {},       # leverless: threshold-based axis state
                "prev_axes_raw": {},   # controller: raw float values
                "last_reinit": 0.0,
            }
            reached_polling = [False]
            try:
                self._load_pygame()
                self._scan()
                self._loop(state, on_polling=lambda: reached_polling.__setitem__(0, True))
            except Exception:
                logger.exception("gamepad session failed; will retry")
            finally:
                try:
                    self._neutralize_state(state)
                except Exception:
                    logger.exception("neutral event emission failed during session teardown")
                finally:
                    self._reset_joy_buffers(state)
                    state["joy"] = None
                    self._teardown_pygame()

            if reached_polling[0]:
                backoff = _BACKOFF_INITIAL
            if not self._is_running():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    def _teardown_pygame(self) -> None:
        if self._pygame is None:
            return
        pg = self._pygame
        try:
            pg.joystick.quit()
        except Exception:
            logger.debug("pygame joystick quit failed", exc_info=True)
        try:
            pg.quit()
        except Exception:
            logger.debug("pygame quit failed", exc_info=True)
        self._pygame = None

    def _loop(self, state: dict, on_polling: Callable[[], None] | None = None) -> None:
        pg = self._pygame
        while self._is_running():
            pg.event.pump()
            if on_polling is not None:
                on_polling()

            if self._consume_refresh():
                self._scan()
                target_id = self.selected_id()
                if state["joy"] is not None and state["joy_id"] != target_id:
                    self._reset_joy(state)

            target_id = self.selected_id()

            if state["joy"] is None and time.time() - state["last_reinit"] > RESCAN_INTERVAL:
                pg.joystick.quit()
                pg.joystick.init()
                state["last_reinit"] = time.time()
                self._scan()

            count = pg.joystick.get_count()
            if count == 0:
                if state["joy"] is not None:
                    self._reset_joy(state)
                    state["joy_id"] = -1
                time.sleep(DISCONNECT_SLEEP)
                continue

            if state["joy"] is None:
                use_id = target_id if target_id < count else 0
                state["joy"] = pg.joystick.Joystick(use_id)
                state["joy"].init()
                state["joy_id"] = use_id
                self.select(use_id)
                print(f"[Gamepad] Connected: {state['joy'].get_name()} (ID: {use_id})")

            if target_id != state["joy_id"] and target_id < count:
                # Neutralize the outgoing controller's buffered state before
                # replacing the joystick reference, so any key_up/axis-zero
                # events refer to the controller that was actually active.
                self._neutralize_state(state)
                self._reset_joy_buffers(state)
                state["joy"] = pg.joystick.Joystick(target_id)
                state["joy"].init()
                state["joy_id"] = target_id
                print(f"[Gamepad] Switched to: {state['joy'].get_name()} (ID: {target_id})")

            self._emit_state(state)
            time.sleep(_POLL_INTERVAL)

    def _emit_state(self, state: dict) -> None:
        joy = state["joy"]
        prev_buttons = state["prev_buttons"]
        prev_axes = state["prev_axes"]
        prev_axes_raw = state["prev_axes_raw"]

        for i in range(joy.get_numbuttons()):
            val = joy.get_button(i)
            if val != prev_buttons.get(i, 0):
                prev_buttons[i] = val
                self._emit_btn(f"btn_{i}", bool(val))

        for i in range(joy.get_numhats()):
            hat = joy.get_hat(i)
            prev_hat = prev_axes.get(f"hat_{i}", (0, 0))
            if hat != prev_hat:
                if prev_hat[0] != 0:
                    self._emit_btn(f"hat_{i}_{'left' if prev_hat[0] < 0 else 'right'}", False)
                if prev_hat[1] != 0:
                    self._emit_btn(f"hat_{i}_{'down' if prev_hat[1] < 0 else 'up'}", False)
                if hat[0] != 0:
                    self._emit_btn(f"hat_{i}_{'left' if hat[0] < 0 else 'right'}", True)
                if hat[1] != 0:
                    self._emit_btn(f"hat_{i}_{'down' if hat[1] < 0 else 'up'}", True)
                prev_axes[f"hat_{i}"] = hat

        for i in range(joy.get_numaxes()):
            raw = joy.get_axis(i)
            if raw < -DEADZONE:
                val = -1
            elif raw > DEADZONE:
                val = 1
            else:
                val = 0
            prev = prev_axes.get(i, 0)
            if val != prev:
                if prev != 0:
                    self._emit_btn(f"axis_{i}_{'neg' if prev < 0 else 'pos'}", False)
                if val != 0:
                    self._emit_btn(f"axis_{i}_{'neg' if val < 0 else 'pos'}", True)
                prev_axes[i] = val
            if abs(raw - prev_axes_raw.get(i, _AXIS_RAW_SENTINEL)) > AXIS_EPS:
                prev_axes_raw[i] = raw
                self._emit(json.dumps({
                    "type": "axis_update",
                    "axis": i,
                    "value": round(raw, 3),
                    "source": "gamepad",
                    "timestamp": time.time(),
                }))

    def _neutralize_state(self, state: dict) -> None:
        """Emit the exact key_up for every active button/hat/threshold-axis
        and an axis_update(0) for every non-neutral tracked raw axis, based
        on the currently buffered state. Must run before that state is
        cleared -- called on disconnect, controller switch, session
        exception, and shutdown, so a displayed key/axis can never remain
        stuck just because the underlying state buffer was silently reset."""
        prev_buttons = state["prev_buttons"]
        prev_axes = state["prev_axes"]
        prev_axes_raw = state["prev_axes_raw"]

        for i, val in prev_buttons.items():
            if val:
                self._emit_btn(f"btn_{i}", False)

        for key, value in prev_axes.items():
            if isinstance(value, tuple):
                hx, hy = value
                idx = str(key)[len("hat_"):]
                if hx != 0:
                    self._emit_btn(f"hat_{idx}_{'left' if hx < 0 else 'right'}", False)
                if hy != 0:
                    self._emit_btn(f"hat_{idx}_{'down' if hy < 0 else 'up'}", False)
            elif value != 0:
                self._emit_btn(f"axis_{key}_{'neg' if value < 0 else 'pos'}", False)

        for i, raw in prev_axes_raw.items():
            if raw != 0:
                self._emit(json.dumps({
                    "type": "axis_update",
                    "axis": i,
                    "value": 0,
                    "source": "gamepad",
                    "timestamp": time.time(),
                }))

    @staticmethod
    def _reset_joy_buffers(state: dict) -> None:
        state["prev_buttons"].clear()
        state["prev_axes"].clear()
        state["prev_axes_raw"].clear()

    def _reset_joy(self, state: dict) -> None:
        """Disconnect/refresh-switch path: neutralize active buffered state,
        clear the buffers, then release the joystick reference."""
        self._neutralize_state(state)
        self._reset_joy_buffers(state)
        state["joy"] = None
