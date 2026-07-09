"""Shared keyboard event normalization, used by sender/input_sender.py
(pynput listener, live capture) and receiver/standalone_capture.py
(standalone 1-PC mode).
"""

import json
import time

from pynput import keyboard

# Modifier key mapping: normalize left/right variants to base name
_MODIFIER_MAP = {
    keyboard.Key.shift: 'shift',
    keyboard.Key.shift_l: 'shift',
    keyboard.Key.shift_r: 'shift',
    keyboard.Key.ctrl: 'ctrl',
    keyboard.Key.ctrl_l: 'ctrl',
    keyboard.Key.ctrl_r: 'ctrl',
    keyboard.Key.alt: 'alt',
    keyboard.Key.alt_l: 'alt',
    keyboard.Key.alt_r: 'alt',
}


def get_vk(key):
    """Extract Windows virtual key code from a pynput key."""
    vk = getattr(key, 'vk', None)
    if vk is not None:
        return vk
    # pynput Key enum members have a value with vk
    value = getattr(key, 'value', None)
    if value is not None:
        return getattr(value, 'vk', None)
    return None


def key_to_str(key):
    # Check modifier map first
    if key in _MODIFIER_MAP:
        return _MODIFIER_MAP[key]
    # Use vk (virtual key code) to get modifier-independent key name
    # This prevents Shift+1 becoming '!' or Ctrl+C becoming '\x03'
    vk = getattr(key, 'vk', None)
    if vk is not None:
        if 0x30 <= vk <= 0x39:  # 0-9
            return chr(vk)
        if 0x41 <= vk <= 0x5A:  # A-Z
            return chr(vk).lower()
    if hasattr(key, "char") and key.char is not None:
        return key.char.lower()
    if hasattr(key, "name"):
        return key.name
    # Fallback: use VK code as identifier (e.g. Japanese IME keys)
    if vk is not None:
        return f"vk_{vk}"
    return str(key)


def make_event(event_type, key, source="keyboard", vk=None):
    ev = {
        "type": event_type,
        "key": key,
        "source": source,
        "timestamp": time.time(),
    }
    if vk is not None:
        ev["vk"] = vk
    return json.dumps(ev)
