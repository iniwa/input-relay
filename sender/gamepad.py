"""
Gamepad capture loop.

pygame.joystick で接続されたコントローラを 60Hz で polling し、ボタン /
ハット / 軸の状態変化をイベントとしてコールバックへ流す。HTTP API から
コントローラ選択 / 再スキャンを依頼するため、状態は Gamepad クラスに
まとめてスレッド間で共有する。
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
        pg.init()
        pg.joystick.init()
        self._pygame = pg

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
        """Blocking poll loop. Run in a daemon thread."""
        self._load_pygame()
        state = {
            "joy": None,
            "joy_id": -1,
            "prev_buttons": {},
            "prev_axes": {},       # leverless: threshold-based axis state
            "prev_axes_raw": {},   # controller: raw float values
            "last_reinit": 0.0,
        }
        self._scan()
        try:
            self._loop(state)
        finally:
            if self._pygame is not None:
                try:
                    self._pygame.joystick.quit()
                    self._pygame.quit()
                except Exception:
                    logger.debug("pygame shutdown failed", exc_info=True)

    def _loop(self, state: dict) -> None:
        pg = self._pygame
        while self._is_running():
            pg.event.pump()

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
                state["joy"] = pg.joystick.Joystick(target_id)
                state["joy"].init()
                state["joy_id"] = target_id
                self._reset_joy_buffers(state)
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

    @staticmethod
    def _reset_joy_buffers(state: dict) -> None:
        state["prev_buttons"].clear()
        state["prev_axes"].clear()
        state["prev_axes_raw"].clear()

    def _reset_joy(self, state: dict) -> None:
        state["joy"] = None
        self._reset_joy_buffers(state)
