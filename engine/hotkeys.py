"""Custom playback keyboard shortcuts for Huge Watch Party.

Uses a Windows low-level keyboard hook (WH_KEYBOARD_LL) so bindings work while
Huge or PotPlayer is focused — not a JavaScript-only handler. Keys are consumed
only when the foreground window is Huge or PotPlayer, so typing in Discord,
browsers, or other apps is unaffected. When a binding fires, the key is eaten
so PotPlayer does not also handle the same press (avoids duplicate sync events).
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

ACTIONS = ('play_pause', 'seek_back', 'seek_forward')

# Defaults match common media navigation; scoped to Huge/PotPlayer only.
DEFAULT_SHORTCUTS = {
    'play_pause': 'Space',
    'seek_back': 'Left',
    'seek_forward': 'Right',
}

SEEK_BACK_MS = -5000
SEEK_FORWARD_MS = 5000

# Virtual-key map for display + parsing (Windows).
_VK_BY_NAME = {
    'SPACE': 0x20,
    'LEFT': 0x25,
    'UP': 0x26,
    'RIGHT': 0x27,
    'DOWN': 0x28,
    'ESCAPE': 0x1B,
    'ESC': 0x1B,
    'TAB': 0x09,
    'ENTER': 0x0D,
    'RETURN': 0x0D,
    'BACKSPACE': 0x08,
    'DELETE': 0x2E,
    'INSERT': 0x2D,
    'HOME': 0x24,
    'END': 0x23,
    'PAGEUP': 0x21,
    'PAGEDOWN': 0x22,
    'PAUSE': 0x13,
    'CAPITAL': 0x14,
    'CAPSLOCK': 0x14,
    'PRINTSCREEN': 0x2C,
    'MULTIPLY': 0x6A,
    'ADD': 0x6B,
    'SUBTRACT': 0x6D,
    'DECIMAL': 0x6E,
    'DIVIDE': 0x6F,
    'OEM_PLUS': 0xBB,
    'OEM_MINUS': 0xBD,
    'OEM_COMMA': 0xBC,
    'OEM_PERIOD': 0xBE,
    'MEDIA_PLAY_PAUSE': 0xB3,
    'MEDIA_STOP': 0xB2,
    'MEDIA_NEXT': 0xB0,
    'MEDIA_PREV': 0xB1,
}
for i in range(1, 13):
    _VK_BY_NAME[f'F{i}'] = 0x70 + (i - 1)
for i in range(10):
    _VK_BY_NAME[str(i)] = 0x30 + i
    _VK_BY_NAME[f'NUM{i}'] = 0x60 + i
for code in range(ord('A'), ord('Z') + 1):
    _VK_BY_NAME[chr(code)] = code

_NAME_BY_VK = {}
for name, vk in _VK_BY_NAME.items():
    # Prefer shortest canonical names for letters/digits.
    prev = _NAME_BY_VK.get(vk)
    if prev is None or (len(name) < len(prev) and name not in ('ESC', 'RETURN', 'CAPITAL')):
        _NAME_BY_VK[vk] = name
_NAME_BY_VK[0x20] = 'Space'
_NAME_BY_VK[0x25] = 'Left'
_NAME_BY_VK[0x26] = 'Up'
_NAME_BY_VK[0x27] = 'Right'
_NAME_BY_VK[0x28] = 'Down'
_NAME_BY_VK[0x1B] = 'Esc'
_NAME_BY_VK[0x0D] = 'Enter'
_NAME_BY_VK[0x09] = 'Tab'

MOD_CTRL = 0x0001
MOD_ALT = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_MOD_NAMES = (
    (MOD_CTRL, 'Ctrl'),
    (MOD_ALT, 'Alt'),
    (MOD_SHIFT, 'Shift'),
    (MOD_WIN, 'Win'),
)


def normalize_shortcut(text):
    """Parse 'Ctrl+Alt+0' / 'space' into (mods_mask, vk) or None if empty/invalid."""
    raw = str(text or '').strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace('-', '+').split('+') if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for part in parts:
        upper = part.upper().replace(' ', '')
        if upper in ('CTRL', 'CONTROL', 'CONTROLKEY'):
            mods |= MOD_CTRL
        elif upper in ('ALT', 'MENU', 'ALTKEY'):
            mods |= MOD_ALT
        elif upper in ('SHIFT', 'SHIFTKEY'):
            mods |= MOD_SHIFT
        elif upper in ('WIN', 'WINDOWS', 'META', 'CMD', 'SUPER'):
            mods |= MOD_WIN
        else:
            if key is not None:
                return None
            vk = _VK_BY_NAME.get(upper)
            if vk is None and len(upper) == 1:
                vk = _VK_BY_NAME.get(upper)
            if vk is None:
                return None
            # Modifiers alone are not valid bindings.
            if vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
                return None
            key = int(vk)
    if key is None:
        return None
    return mods, key


def format_shortcut(mods, vk):
    if vk is None:
        return ''
    bits = [label for mask, label in _MOD_NAMES if mods & mask]
    key_name = _NAME_BY_VK.get(int(vk))
    if not key_name:
        key_name = f'VK_{int(vk)}'
    # Title-case single letters already upper; keep digits/F-keys as mapped.
    if len(key_name) == 1:
        key_name = key_name.upper()
    elif key_name.lower() == 'space':
        key_name = 'Space'
    bits.append(key_name)
    return '+'.join(bits)


def shortcut_label(value):
    parsed = normalize_shortcut(value)
    if not parsed:
        return ''
    return format_shortcut(parsed[0], parsed[1])


def default_shortcuts_dict():
    return {action: DEFAULT_SHORTCUTS[action] for action in ACTIONS}


def validate_shortcuts(mapping):
    """Return (normalized_dict, errors_list, warnings_list).

    errors: invalid combos / unknown actions
    warnings: conflicts between actions
    """
    out = default_shortcuts_dict()
    errors = []
    warnings = []
    data = mapping if isinstance(mapping, dict) else {}
    seen = {}
    for action in ACTIONS:
        raw = data.get(action, out[action])
        if raw is None or str(raw).strip() == '':
            out[action] = ''
            continue
        parsed = normalize_shortcut(raw)
        if not parsed:
            errors.append(f'Invalid shortcut for {action}: {raw!r}')
            out[action] = shortcut_label(out[action]) or DEFAULT_SHORTCUTS[action]
            continue
        label = format_shortcut(parsed[0], parsed[1])
        out[action] = label
        key = parsed
        if key in seen:
            warnings.append(
                f'Conflict: {label} is assigned to both {seen[key]} and {action}'
            )
        else:
            seen[key] = action
    return out, errors, warnings


def seek_delta_for_action(action):
    if action == 'seek_back':
        return SEEK_BACK_MS
    if action == 'seek_forward':
        return SEEK_FORWARD_MS
    return 0


class ShortcutStore:
    """Persistent shortcut map with conflict checks."""

    def __init__(self, initial=None):
        normalized, errors, warnings = validate_shortcuts(initial or default_shortcuts_dict())
        self._map = normalized
        self.errors = list(errors)
        self.warnings = list(warnings)

    def as_dict(self):
        return {k: self._map.get(k, '') for k in ACTIONS}

    def get(self, action):
        return self._map.get(action, '')

    def set(self, action, value):
        if action not in ACTIONS:
            return False, f'Unknown action: {action}', list(self.warnings)
        trial = self.as_dict()
        trial[action] = '' if value is None else str(value)
        normalized, errors, warnings = validate_shortcuts(trial)
        if errors:
            self.errors = errors
            return False, errors[0], warnings
        self._map = normalized
        self.errors = []
        self.warnings = warnings
        return True, '', warnings

    def clear(self, action):
        return self.set(action, '')

    def reset(self, action=None):
        if action is None:
            self._map = default_shortcuts_dict()
            self.errors = []
            self.warnings = []
            return True, '', []
        return self.set(action, DEFAULT_SHORTCUTS.get(action, ''))

    def binding_table(self):
        """Map (mods, vk) -> action for armed (non-empty) shortcuts."""
        table = {}
        for action in ACTIONS:
            parsed = normalize_shortcut(self._map.get(action))
            if parsed:
                table[parsed] = action
        return table


def _current_mods_from_keys(vk_code, flags_down_set):
    """Build mods mask from currently held modifiers (excluding the key itself)."""
    mods = 0
    # Use GetAsyncKeyState for live modifier state.
    user32 = ctypes.windll.user32
    if user32.GetAsyncKeyState(0x11) & 0x8000:  # VK_CONTROL
        mods |= MOD_CTRL
    if user32.GetAsyncKeyState(0x12) & 0x8000:  # VK_MENU (Alt)
        mods |= MOD_ALT
    if user32.GetAsyncKeyState(0x10) & 0x8000:  # VK_SHIFT
        mods |= MOD_SHIFT
    if (user32.GetAsyncKeyState(0x5B) & 0x8000) or (user32.GetAsyncKeyState(0x5C) & 0x8000):
        mods |= MOD_WIN
    return mods


def combo_from_key_event(vk_code):
    """Build (mods, vk) / label for a physical key-down (recording helper)."""
    vk = int(vk_code) & 0xFF
    # Ignore pure modifier presses while recording.
    if vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
        return None
    mods = _current_mods_from_keys(vk, None)
    # If the key is a modifier-only, skip.
    label = format_shortcut(mods, vk)
    return mods, vk, label


class PlaybackHotkeyManager:
    """Native WH_KEYBOARD_LL manager scoped to Huge + PotPlayer windows."""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_KEYUP = 0x0101
    WM_SYSKEYUP = 0x0105
    HC_ACTION = 0
    LLKHF_INJECTED = 0x10

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ('vkCode', wintypes.DWORD),
            ('scanCode', wintypes.DWORD),
            ('flags', wintypes.DWORD),
            ('time', wintypes.DWORD),
            ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
        ]

    def __init__(self, on_action, get_huge_hwnds=None, is_text_entry_active=None):
        self._on_action = on_action
        self._get_huge_hwnds = get_huge_hwnds or (lambda: [])
        self._is_text_entry_active = is_text_entry_active or (lambda: False)
        self._store = ShortcutStore()
        self._lock = threading.RLock()
        self._hook = None
        self._thread = None
        self._tid = None
        self._running = False
        self._recording_action = None
        self._recording_callback = None
        self._last_fire = {}
        self._debounce_s = 0.12
        self._enabled = True
        self._warning = ''
        self._proc = None

    @property
    def warning(self):
        return self._warning

    def shortcuts(self):
        with self._lock:
            return self._store.as_dict()

    def load_dict(self, data):
        with self._lock:
            self._store = ShortcutStore(data)
            self._warning = '; '.join(self._store.warnings)
            return self._store.as_dict(), list(self._store.warnings)

    def set_shortcut(self, action, value):
        with self._lock:
            ok, err, warnings = self._store.set(action, value)
            self._warning = err or '; '.join(warnings)
            return ok, err, self._store.as_dict(), warnings

    def clear_shortcut(self, action):
        return self.set_shortcut(action, '')

    def reset_shortcut(self, action=None):
        with self._lock:
            ok, err, warnings = self._store.reset(action)
            self._warning = err or '; '.join(warnings)
            return ok, err, self._store.as_dict(), warnings

    def begin_record(self, action, callback=None):
        if action not in ACTIONS:
            return False
        with self._lock:
            self._recording_action = action
            self._recording_callback = callback
        return True

    def cancel_record(self):
        with self._lock:
            self._recording_action = None
            self._recording_callback = None

    def is_recording(self):
        with self._lock:
            return self._recording_action is not None

    def start(self):
        if os.name != 'nt':
            self._warning = 'Keyboard shortcuts require Windows.'
            return False
        with self._lock:
            if self._running:
                return True
            self._running = True
        self._thread = threading.Thread(target=self._hook_loop, name='HugeHotkeys', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        with self._lock:
            self._running = False
            tid = self._tid
        if tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._tid = None
        self._hook = None

    def _foreground_allows_hotkeys(self):
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        # PotPlayer
        try:
            from engine.session import _find_pot_hwnd
            pot = _find_pot_hwnd()
            if pot and (fg == pot or user32.IsChild(pot, fg)):
                return 'potplayer'
        except Exception:
            pass
        # Huge window(s)
        try:
            for hwnd in self._get_huge_hwnds() or []:
                if not hwnd:
                    continue
                hwnd = int(hwnd)
                if fg == hwnd or user32.IsChild(hwnd, fg):
                    return 'huge'
        except Exception:
            pass
        # Title fallback for Huge
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(fg, buf, 256)
            title = (buf.value or '').strip()
            if title == 'Huge' or title.startswith('Huge '):
                return 'huge'
            if 'PotPlayer' in title:
                return 'potplayer'
        except Exception:
            pass
        return False

    def _hook_loop(self):
        user32 = ctypes.windll.user32
        self._tid = threading.get_ident()
        CMPFUNC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        def _handler(nCode, wParam, lParam):
            try:
                if nCode == self.HC_ACTION and wParam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                    info = ctypes.cast(lParam, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
                    if info.flags & self.LLKHF_INJECTED:
                        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                    if self._handle_key(int(info.vkCode)):
                        return 1  # eat key — prevent PotPlayer duplicate handling
            except Exception:
                pass
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._proc = CMPFUNC(_handler)
        self._hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            self._warning = (
                'Could not register keyboard hook. Choose another shortcut or '
                'run Huge again; Windows may be blocking global key capture.'
            )
            self._running = False
            return
        msg = wintypes.MSG()
        while self._running:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or result == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None

    def _handle_key(self, vk_code):
        with self._lock:
            recording = self._recording_action
            callback = self._recording_callback
            enabled = self._enabled
            table = self._store.binding_table() if not recording else {}

        if recording:
            combo = combo_from_key_event(vk_code)
            if not combo:
                return True  # swallow modifier-only while recording UI is open
            mods, vk, label = combo
            with self._lock:
                self._recording_action = None
                self._recording_callback = None
                action = recording
            ok, err, _mapping, warnings = self.set_shortcut(action, label)
            if callback:
                try:
                    callback({
                        'ok': ok,
                        'action': action,
                        'shortcut': label if ok else '',
                        'error': err,
                        'warnings': warnings,
                    })
                except Exception:
                    pass
            return True

        if not enabled:
            return False
        scope = self._foreground_allows_hotkeys()
        if not scope:
            return False
        if scope == 'huge' and self._is_text_entry_active():
            return False

        mods = _current_mods_from_keys(vk_code, None)
        # When the pressed key is itself a modifier, ignore.
        if vk_code in (0x10, 0x11, 0x12, 0x5B, 0x5C):
            return False
        action = table.get((mods, int(vk_code) & 0xFF))
        if not action:
            return False

        now = time.monotonic()
        last = self._last_fire.get(action, 0.0)
        if now - last < self._debounce_s:
            return True  # still eat duplicate key-repeat
        self._last_fire[action] = now
        try:
            self._on_action(action, scope=scope, mono=now)
        except Exception:
            pass
        return True
