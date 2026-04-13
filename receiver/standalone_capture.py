"""
Standalone input capture - captures keyboard/mouse/gamepad input locally.
Used by input_server.py in standalone mode (--standalone).
"""

import json
import time
import threading

from pynput import keyboard, mouse

_running = True
_loop = None
_callback = None  # callable(json_str)

_pressed_keys = set()
_pressed_lock = threading.Lock()

# 起動時に生成されるリスナー参照 (stop() で明示停止するため保持)
_kb_listener = None
_mouse_listener = None
_gamepad_thread = None

_MODIFIER_MAP = {
    keyboard.Key.shift: 'shift', keyboard.Key.shift_l: 'shift', keyboard.Key.shift_r: 'shift',
    keyboard.Key.ctrl: 'ctrl', keyboard.Key.ctrl_l: 'ctrl', keyboard.Key.ctrl_r: 'ctrl',
    keyboard.Key.alt: 'alt', keyboard.Key.alt_l: 'alt', keyboard.Key.alt_r: 'alt',
}


def _key_to_str(key):
    if key in _MODIFIER_MAP:
        return _MODIFIER_MAP[key]
    vk = getattr(key, 'vk', None)
    if vk is not None:
        if 0x30 <= vk <= 0x39:
            return chr(vk)
        if 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
    if hasattr(key, "char") and key.char is not None:
        return key.char.lower()
    if hasattr(key, "name"):
        return key.name
    # Fallback: use VK code as identifier (e.g. Japanese IME keys)
    if vk is not None:
        return f"vk_{vk}"
    return str(key)


def _get_vk(key):
    vk = getattr(key, 'vk', None)
    if vk is not None:
        return vk
    value = getattr(key, 'value', None)
    if value is not None:
        return getattr(value, 'vk', None)
    return None


def _make_event(event_type, key, source="keyboard", vk=None):
    ev = {"type": event_type, "key": key, "source": source, "timestamp": time.time()}
    if vk is not None:
        ev["vk"] = vk
    return json.dumps(ev)


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
_GAMEPAD_POLL_INTERVAL = 1.0 / 60  # 60Hz


def _gamepad_loop():
    try:
        import pygame
    except ImportError:
        print("[Standalone] pygame not found - gamepad disabled")
        return

    # pygame の初期化はスレッド生存中に 1 度だけ。接続検出のための再初期化は
    # joystick サブシステムのみで行う。
    pygame.init()
    pygame.joystick.init()

    joy = None
    prev_buttons = {}
    prev_axes = {}
    prev_axes_raw = {}
    last_reinit = 0.0

    try:
        while _running:
            pygame.event.pump()

            if joy is None and time.time() - last_reinit > 2.0:
                # 新規ジョイスティック接続を検出するため joystick のみ再初期化
                pygame.joystick.quit()
                pygame.joystick.init()
                last_reinit = time.time()

            if pygame.joystick.get_count() > 0:
                if joy is None:
                    joy = pygame.joystick.Joystick(0)
                    joy.init()
                    print(f"[Standalone] Gamepad: {joy.get_name()}")
            else:
                if joy is not None:
                    joy = None
                    prev_buttons.clear()
                    prev_axes.clear()
                    prev_axes_raw.clear()
                time.sleep(0.1)
                continue

            # Buttons
            for i in range(joy.get_numbuttons()):
                val = joy.get_button(i)
                if val != prev_buttons.get(i, 0):
                    prev_buttons[i] = val
                    etype = "key_down" if val else "key_up"
                    _emit(_make_event(etype, f"btn_{i}", "gamepad"))

            # Hats
            for i in range(joy.get_numhats()):
                hat = joy.get_hat(i)
                prev_hat = prev_axes.get(f"hat_{i}", (0, 0))
                if hat != prev_hat:
                    if prev_hat[0] != 0:
                        _emit(_make_event("key_up", f"hat_{i}_{'left' if prev_hat[0] < 0 else 'right'}", "gamepad"))
                    if prev_hat[1] != 0:
                        _emit(_make_event("key_up", f"hat_{i}_{'down' if prev_hat[1] < 0 else 'up'}", "gamepad"))
                    if hat[0] != 0:
                        _emit(_make_event("key_down", f"hat_{i}_{'left' if hat[0] < 0 else 'right'}", "gamepad"))
                    if hat[1] != 0:
                        _emit(_make_event("key_down", f"hat_{i}_{'down' if hat[1] < 0 else 'up'}", "gamepad"))
                    prev_axes[f"hat_{i}"] = hat

            # Axes
            deadzone = 0.5
            for i in range(joy.get_numaxes()):
                raw = joy.get_axis(i)
                if raw < -deadzone:
                    val = -1
                elif raw > deadzone:
                    val = 1
                else:
                    val = 0
                prev = prev_axes.get(i, 0)
                if val != prev:
                    if prev != 0:
                        _emit(_make_event("key_up", f"axis_{i}_{'neg' if prev < 0 else 'pos'}", "gamepad"))
                    if val != 0:
                        _emit(_make_event("key_down", f"axis_{i}_{'neg' if val < 0 else 'pos'}", "gamepad"))
                    prev_axes[i] = val
                if abs(raw - prev_axes_raw.get(i, 2.0)) > 0.01:
                    prev_axes_raw[i] = raw
                    _emit(json.dumps({
                        "type": "axis_update", "axis": i,
                        "value": round(raw, 3), "source": "gamepad",
                        "timestamp": time.time(),
                    }))

            time.sleep(_GAMEPAD_POLL_INTERVAL)
    finally:
        try:
            pygame.joystick.quit()
            pygame.quit()
        except Exception:
            pass


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
