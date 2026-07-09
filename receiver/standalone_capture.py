"""
Standalone input capture - captures keyboard/mouse/gamepad input locally.
Used by input_server.py in standalone mode (--standalone).
"""

import json
import sys
import time
import threading
from pathlib import Path

from pynput import keyboard, mouse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from input_common.gamepad import Gamepad
from input_common.input_events import get_vk as _get_vk
from input_common.input_events import key_to_str as _key_to_str
from input_common.input_events import make_event as _make_event

_running = True
_loop = None
_callback = None  # callable(json_str)

_pressed_keys = set()
_pressed_lock = threading.Lock()

# 起動時に生成されるリスナー参照 (stop() で明示停止するため保持)
_kb_listener = None
_mouse_listener = None
_gamepad_thread = None


def _emit(msg):
    if _loop and _callback:
        _loop.call_soon_threadsafe(_callback, msg)


# --- Keyboard ---
def _on_press(key):
    key_str = _key_to_str(key)
    vk = _get_vk(key)
    with _pressed_lock:
        if key_str not in _pressed_keys:
            _pressed_keys.add(key_str)
            _emit(_make_event("key_down", key_str, vk=vk))


def _on_release(key):
    key_str = _key_to_str(key)
    vk = _get_vk(key)
    with _pressed_lock:
        _pressed_keys.discard(key_str)
    _emit(_make_event("key_up", key_str, vk=vk))


# --- Mouse ---
def _on_click(x, y, button, pressed):
    btn_map = {
        mouse.Button.left: 'mouse_left',
        mouse.Button.right: 'mouse_right',
        mouse.Button.middle: 'mouse_middle',
        mouse.Button.x1: 'mouse_x1',
        mouse.Button.x2: 'mouse_x2',
    }
    key_str = btn_map.get(button)
    if not key_str:
        return
    etype = "key_down" if pressed else "key_up"
    _emit(_make_event(etype, key_str, "mouse"))


def _on_scroll(x, y, dx, dy):
    _emit(json.dumps({
        "type": "mouse_scroll", "dx": dx, "dy": dy,
        "source": "mouse", "timestamp": time.time(),
    }))


# --- Gamepad ---
# コントローラ選択/再スキャン API は standalone では未使用のため、共有
# Gamepad はデフォルトの selected_id=0（常に最初のコントローラ）のまま動く。
def _gamepad_loop():
    try:
        import pygame  # noqa: F401  presence check before handing off to the shared poller
    except ImportError:
        print("[Standalone] pygame not found - gamepad disabled")
        return

    gp = Gamepad(emit_callback=_emit, is_running=lambda: _running)
    gp.run()


def start(loop, callback):
    """Start all input capture threads. callback(json_str) is called for each event."""
    global _loop, _callback, _running, _kb_listener, _mouse_listener, _gamepad_thread
    _loop = loop
    _callback = callback
    _running = True

    _kb_listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    _kb_listener.start()

    _mouse_listener = mouse.Listener(on_click=_on_click, on_scroll=_on_scroll)
    _mouse_listener.start()

    _gamepad_thread = threading.Thread(target=_gamepad_loop, daemon=True)
    _gamepad_thread.start()

    print("[Standalone] Input capture started (keyboard + mouse + gamepad)")


def stop():
    """Stop all capture threads and release resources. Idempotent."""
    global _running
    _running = False
    for listener in (_kb_listener, _mouse_listener):
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
    if _gamepad_thread is not None:
        _gamepad_thread.join(timeout=1.0)
