"""Custom playback keyboard shortcuts for Huge Watch Party.

Uses a Windows low-level keyboard hook (WH_KEYBOARD_LL) so bindings work while
Huge or PotPlayer is focused — not a JavaScript-only handler.

CRITICAL safety rules for WH_KEYBOARD_LL (v2.0.23):
- The hook callback must return in microseconds. Never EnumWindows, SendMessage,
  file I/O, network, or long locks inside the callback — that freezes *all*
  keyboard input system-wide (the v2.0.22 failure mode).
- Only return a nonzero value (consume the key) when a registered shortcut
  actually matches and we queue an action. All other keys CallNextHookEx.
- Action work runs on a separate worker thread, never inside the hook.
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import threading
import time
from ctypes import wintypes

ACTIONS = ('play_pause', 'seek_back', 'seek_forward')

# Defaults reserved for Huge when PotPlayer is focused (consume so PotPlayer
# does not also handle the same physical key). Scoped to Huge/PotPlayer only.
DEFAULT_SHORTCUTS = {
    'play_pause': 'Space',
    'seek_back': 'Left Arrow',
    'seek_forward': 'Right Arrow',
}

# v2.0.24 briefly shipped '<' / '>' as defaults. Migrate those back to arrows
# so Left/Right are actually intercepted. Custom non-default bindings are kept.
_LEGACY_V2024_DEFAULTS = {
    'seek_back': '<',
    'seek_forward': '>',
}

SEEK_BACK_MS = -5000
SEEK_FORWARD_MS = 5000

VK_OEM_COMMA = 0xBC
VK_OEM_PERIOD = 0xBE

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
    'OEM_COMMA': VK_OEM_COMMA,
    'COMMA': VK_OEM_COMMA,
    ',': VK_OEM_COMMA,
    'OEM_PERIOD': VK_OEM_PERIOD,
    'PERIOD': VK_OEM_PERIOD,
    'DOT': VK_OEM_PERIOD,
    '.': VK_OEM_PERIOD,
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
    if name in (',', '.'):
        continue
    prev = _NAME_BY_VK.get(vk)
    if prev is None or (len(name) < len(prev) and name not in ('ESC', 'RETURN', 'CAPITAL')):
        _NAME_BY_VK[vk] = name
_NAME_BY_VK[0x20] = 'Space'
_NAME_BY_VK[0x25] = 'Left Arrow'
_NAME_BY_VK[0x26] = 'Up Arrow'
_NAME_BY_VK[0x27] = 'Right Arrow'
_NAME_BY_VK[0x28] = 'Down Arrow'
_NAME_BY_VK[0x1B] = 'Esc'
_NAME_BY_VK[0x0D] = 'Enter'
_NAME_BY_VK[0x09] = 'Tab'
_NAME_BY_VK[VK_OEM_COMMA] = ','
_NAME_BY_VK[VK_OEM_PERIOD] = '.'
_NAME_BY_VK[0xBB] = 'Plus'       # VK_OEM_PLUS
_NAME_BY_VK[0xBD] = 'Minus'      # VK_OEM_MINUS
# Readable aliases accepted by normalize_shortcut (spaces stripped → UPPER).
for _alias, _vk in (
    ('LEFT', 0x25), ('LEFTARROW', 0x25), ('ARROWLEFT', 0x25),
    ('RIGHT', 0x27), ('RIGHTARROW', 0x27), ('ARROWRIGHT', 0x27),
    ('UP', 0x26), ('UPARROW', 0x26), ('ARROWUP', 0x26),
    ('DOWN', 0x28), ('DOWNARROW', 0x28), ('ARROWDOWN', 0x28),
    ('PLUS', 0xBB), ('MINUS', 0xBD), ('EQUALS', 0xBB),
):
    _VK_BY_NAME[_alias] = _vk

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

_MODIFIER_VKS = frozenset({
    0x10, 0x11, 0x12, 0x5B, 0x5C,  # Shift/Ctrl/Alt/Win
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,  # left/right variants
})

# GA_ROOT — cheap ancestor walk, safe inside an LL hook (no EnumWindows).
GA_ROOT = 2

# On Win64, wintypes.LPARAM can still be 32-bit in some Python builds. Using a
# truncated LPARAM in CallNextHookEx raises OverflowError inside the hook
# callback; when that fails, keys never reach apps (v2.0.22 "dead keyboard").
_LRESULT = ctypes.c_ssize_t
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
_HHOOK = getattr(wintypes, 'HHOOK', wintypes.HANDLE)


def _configure_hook_prototypes():
    if os.name != 'nt':
        return
    user32 = ctypes.windll.user32
    user32.CallNextHookEx.argtypes = [_HHOOK, ctypes.c_int, _WPARAM, _LPARAM]
    user32.CallNextHookEx.restype = _LRESULT
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = _HHOOK
    user32.UnhookWindowsHookEx.argtypes = [_HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND


if os.name == 'nt':
    try:
        _configure_hook_prototypes()
    except Exception:
        pass


def normalize_shortcut(text):
    """Parse 'Ctrl+Alt+0' / 'space' / '<' / '>' into (mods_mask, vk) or None.

    '<' is Shift+OEM_COMMA and '>' is Shift+OEM_PERIOD on typical layouts.
    A bare comma or period (no Shift) is a different binding and must not
    collide with '<' / '>'.
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    # Character forms used in settings UI and defaults.
    if raw == '<':
        return MOD_SHIFT, VK_OEM_COMMA
    if raw == '>':
        return MOD_SHIFT, VK_OEM_PERIOD

    # "Shift+," / "Ctrl+Shift+." — the comma/period after '+' must not be lost to split.
    protected = re.sub(r'(?i)\+\s*,', '+OEM_COMMA', raw)
    protected = re.sub(r'(?i)\+\s*\.', '+OEM_PERIOD', protected)

    parts = [p.strip() for p in protected.replace('-', '+').split('+') if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for part in parts:
        # Character tokens for seek defaults / recording.
        if part == '<':
            if key is not None:
                return None
            key = VK_OEM_COMMA
            mods |= MOD_SHIFT
            continue
        if part == '>':
            if key is not None:
                return None
            key = VK_OEM_PERIOD
            mods |= MOD_SHIFT
            continue
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
            # Allow literal comma/period tokens from "Shift+," recording labels.
            if part in (',', '.'):
                vk = _VK_BY_NAME.get(part)
            else:
                vk = _VK_BY_NAME.get(upper)
                if vk is None:
                    vk = _VK_BY_NAME.get(part)
            if vk is None:
                return None
            if vk in _MODIFIER_VKS:
                return None
            key = int(vk)
    if key is None:
        return None
    return mods, key


def format_shortcut(mods, vk):
    if vk is None:
        return ''
    mods = int(mods) & 0x0F
    vk = int(vk)
    # Prefer the character labels users expect for seek defaults.
    if vk == VK_OEM_COMMA and mods == MOD_SHIFT:
        return '<'
    if vk == VK_OEM_PERIOD and mods == MOD_SHIFT:
        return '>'
    bits = [label for mask, label in _MOD_NAMES if mods & mask]
    key_name = _NAME_BY_VK.get(vk)
    if not key_name:
        key_name = f'VK_{vk}'
    # Never surface raw OEM_* tokens when a readable name exists above.
    if key_name.startswith('OEM_'):
        friendly = {
            'OEM_PLUS': 'Plus',
            'OEM_MINUS': 'Minus',
            'OEM_COMMA': ',',
            'OEM_PERIOD': '.',
        }.get(key_name)
        if friendly:
            key_name = friendly
    if len(key_name) == 1 and key_name not in (',', '.'):
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


def migrate_legacy_default_shortcuts(mapping):
    """Migrate v2.0.24 '<' / '>' defaults back to Left / Right.

    Profiles that still carry the brief v2.0.24 seek defaults are updated once
    so the hook actually intercepts the arrow keys PotPlayer uses natively.
    Any other custom binding is left untouched.
    """
    data = dict(mapping or {})
    changed = False
    for action in ('seek_back', 'seek_forward'):
        raw = str(data.get(action) or '').strip()
        legacy = _LEGACY_V2024_DEFAULTS.get(action, '')
        if not raw:
            continue
        cur = normalize_shortcut(raw)
        old = normalize_shortcut(legacy)
        if cur and old and cur == old:
            data[action] = DEFAULT_SHORTCUTS[action]
            changed = True
    return data, changed


def validate_shortcuts(mapping):
    """Return (normalized_dict, errors_list, warnings_list).

    Empty / missing entries stay empty — never silently revive defaults.
    Defaults apply only when constructing a brand-new ShortcutStore(None)
    or when the user explicitly resets.
    """
    errors = []
    warnings = []
    data = mapping if isinstance(mapping, dict) else {}
    out = {action: '' for action in ACTIONS}
    seen = {}
    for action in ACTIONS:
        raw = data.get(action, '')
        if raw is None or str(raw).strip() == '':
            out[action] = ''
            continue
        parsed = normalize_shortcut(raw)
        if not parsed:
            errors.append(f'Invalid shortcut for {action}: {raw!r}')
            # Fail closed — do not restore Space/Left/Right behind the user's back.
            out[action] = ''
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
        # None / empty mapping → factory defaults. A saved profile always writes
        # all action keys (possibly blank); blank means intentionally cleared.
        if initial is None or (isinstance(initial, dict) and len(initial) == 0):
            data = default_shortcuts_dict()
        else:
            src = dict(initial)
            migrated, _ = migrate_legacy_default_shortcuts(src)
            data = {}
            for action in ACTIONS:
                if action in src:
                    data[action] = migrated.get(action, src.get(action, ''))
                else:
                    data[action] = ''
        normalized, errors, warnings = validate_shortcuts(data)
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


def combo_from_key_event(vk_code, mods_mask):
    """Build (mods, vk, label) for a physical key-down (recording helper)."""
    vk = int(vk_code) & 0xFF
    if vk in _MODIFIER_VKS:
        return None
    label = format_shortcut(int(mods_mask) & 0x0F, vk)
    return int(mods_mask) & 0x0F, vk, label


def _vk_to_mod_bit(vk):
    vk = int(vk) & 0xFF
    if vk in (0x11, 0xA2, 0xA3):
        return MOD_CTRL
    if vk in (0x12, 0xA4, 0xA5):
        return MOD_ALT
    if vk in (0x10, 0xA0, 0xA1):
        return MOD_SHIFT
    if vk in (0x5B, 0x5C):
        return MOD_WIN
    return 0


class PlaybackHotkeyManager:
    """Native WH_KEYBOARD_LL manager scoped to Huge + PotPlayer windows."""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_KEYUP = 0x0101
    WM_SYSKEYUP = 0x0105
    WM_TIMER = 0x0113
    HC_ACTION = 0
    LLKHF_INJECTED = 0x10
    LLKHF_UP = 0x80
    # Refresh cached target HWNDs outside the hot path (ms).
    HWND_REFRESH_MS = 400
    TIMER_ID = 1

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ('vkCode', wintypes.DWORD),
            ('scanCode', wintypes.DWORD),
            ('flags', wintypes.DWORD),
            ('time', wintypes.DWORD),
            # ULONG_PTR — must be pointer-sized integer, not a POINTER type.
            ('dwExtraInfo', ctypes.c_size_t),
        ]

    def __init__(self, on_action, get_huge_hwnds=None, is_text_entry_active=None,
                 find_pot_hwnd=None):
        self._on_action = on_action
        self._get_huge_hwnds = get_huge_hwnds or (lambda: [])
        self._is_text_entry_active = is_text_entry_active or (lambda: False)
        # Optional injectable PotPlayer lookup — MUST NOT be called from the hook.
        self._find_pot_hwnd = find_pot_hwnd
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
        # Snapshot used only by the hook callback (updated on a timer).
        self._bind_snapshot = {}
        self._cached_huge_roots = frozenset()
        self._cached_pot_root = 0
        self._cached_pot_pid = 0
        self._cached_huge_pids = frozenset()
        self._mods_down = 0
        # VKs whose keydown we consumed — matching keyups must also be eaten
        # so apps do not see a lone key-up (stuck-key / phantom input).
        self._suppress_keyup = set()
        self._action_q = queue.Queue()
        self._worker = None
        # When set, injected keys (SendInput) are eligible — live tests only.
        self._allow_injected = os.environ.get('HUGE_HOTKEY_ALLOW_INJECTED') == '1'

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
            self._bind_snapshot = dict(self._store.binding_table())
            return self._store.as_dict(), list(self._store.warnings)

    def set_shortcut(self, action, value):
        with self._lock:
            ok, err, warnings = self._store.set(action, value)
            self._warning = err or '; '.join(warnings)
            self._bind_snapshot = dict(self._store.binding_table())
            return ok, err, self._store.as_dict(), warnings

    def clear_shortcut(self, action):
        return self.set_shortcut(action, '')

    def reset_shortcut(self, action=None):
        with self._lock:
            ok, err, warnings = self._store.reset(action)
            self._warning = err or '; '.join(warnings)
            self._bind_snapshot = dict(self._store.binding_table())
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
            self._bind_snapshot = dict(self._store.binding_table())
        self._worker = threading.Thread(
            target=self._action_worker_loop, name='HugeHotkeyActions', daemon=True,
        )
        self._worker.start()
        self._thread = threading.Thread(target=self._hook_loop, name='HugeHotkeys', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Remove the hook and stop worker threads. Safe to call from any thread."""
        with self._lock:
            self._running = False
            self._recording_action = None
            self._recording_callback = None
            tid = self._tid
            hook = self._hook
            self._hook = None
            self._suppress_keyup.clear()
            self._mods_down = 0
            self._bind_snapshot = {}
        # Unhook immediately so keys are never trapped after exit.
        if hook:
            try:
                ctypes.windll.user32.UnhookWindowsHookEx(hook)
            except Exception:
                pass
        if tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(int(tid), 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        try:
            self._action_q.put_nowait(None)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self._thread = None
        self._worker = None
        self._tid = None
        # Restore bindings snapshot for any late decide_consume_for_test callers.
        with self._lock:
            self._bind_snapshot = dict(self._store.binding_table())

    def _action_worker_loop(self):
        while True:
            item = self._action_q.get()
            if item is None:
                if not self._running:
                    break
                continue
            try:
                action, scope, mono = item
                # Recording finish is queued as ('__record__', callable, mono).
                if action == '__record__' and callable(scope):
                    scope()
                else:
                    self._on_action(action, scope=scope, mono=mono)
            except Exception:
                pass

    def _default_find_pot_hwnd(self):
        # Called ONLY from the refresh timer / non-hook context.
        try:
            from engine.session import _find_pot_hwnd
            return _find_pot_hwnd()
        except Exception:
            return 0

    def _pid_for_hwnd(self, hwnd):
        """Return process id for hwnd, or 0. Safe for timer + LL hook."""
        try:
            hwnd = int(hwnd or 0)
            if not hwnd:
                return 0
            pid = wintypes.DWORD(0)
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value or 0)
        except Exception:
            return 0

    def _sync_mods_from_os(self):
        """Reconcile tracked modifiers with OS state (timer only — not hook)."""
        try:
            user32 = ctypes.windll.user32
            mods = 0
            # High bit set means key is currently down.
            if int(user32.GetAsyncKeyState(0x10) or 0) & 0x8000:
                mods |= MOD_SHIFT
            if int(user32.GetAsyncKeyState(0x11) or 0) & 0x8000:
                mods |= MOD_CTRL
            if int(user32.GetAsyncKeyState(0x12) or 0) & 0x8000:
                mods |= MOD_ALT
            if (int(user32.GetAsyncKeyState(0x5B) or 0) & 0x8000) or (
                int(user32.GetAsyncKeyState(0x5C) or 0) & 0x8000
            ):
                mods |= MOD_WIN
            self._mods_down = mods
        except Exception:
            pass

    def _refresh_hwnd_cache(self):
        """Update cached roots/PIDs. Never call from the LL hook callback."""
        huge_roots = set()
        huge_pids = set()
        try:
            user32 = ctypes.windll.user32
            for hwnd in self._get_huge_hwnds() or []:
                try:
                    hwnd = int(hwnd or 0)
                except Exception:
                    continue
                if not hwnd:
                    continue
                try:
                    root = int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)
                except Exception:
                    root = hwnd
                if root:
                    huge_roots.add(root)
                huge_roots.add(hwnd)
                pid = self._pid_for_hwnd(root or hwnd)
                if pid:
                    huge_pids.add(pid)
        except Exception:
            pass

        pot_root = 0
        pot_pid = 0
        try:
            finder = self._find_pot_hwnd or self._default_find_pot_hwnd
            pot = finder()
            pot = int(pot or 0)
            if pot:
                try:
                    pot_root = int(ctypes.windll.user32.GetAncestor(pot, GA_ROOT) or pot)
                except Exception:
                    pot_root = pot
                pot_pid = self._pid_for_hwnd(pot_root or pot)
        except Exception:
            pot_root = 0
            pot_pid = 0

        self._cached_huge_roots = frozenset(huge_roots)
        self._cached_huge_pids = frozenset(huge_pids)
        self._cached_pot_root = int(pot_root or 0)
        self._cached_pot_pid = int(pot_pid or 0)
        self._sync_mods_from_os()

    def _scope_from_cache(self):
        """Fast foreground check using cached HWNDs/PIDs — safe in the LL hook."""
        try:
            user32 = ctypes.windll.user32
            fg = int(user32.GetForegroundWindow() or 0)
            if not fg:
                return False
            try:
                root = int(user32.GetAncestor(fg, GA_ROOT) or fg)
            except Exception:
                root = fg
            fg_pid = self._pid_for_hwnd(fg) or self._pid_for_hwnd(root)

            pot = int(self._cached_pot_root or 0)
            pot_pid = int(self._cached_pot_pid or 0)
            if pot and (fg == pot or root == pot):
                return 'potplayer'
            if pot_pid and fg_pid and fg_pid == pot_pid:
                return 'potplayer'

            roots = self._cached_huge_roots or frozenset()
            if fg in roots or root in roots:
                return 'huge'
            huge_pids = self._cached_huge_pids or frozenset()
            if fg_pid and fg_pid in huge_pids:
                return 'huge'

            # Cheap title fallback without EnumWindows (GetWindowText on FG only).
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(fg, buf, 256)
            title = (buf.value or '').strip()
            if title == 'Huge' or title.startswith('Huge '):
                return 'huge'
            if 'PotPlayer' in title:
                return 'potplayer'
            # Class-name fallback for focused PotPlayer chrome / video surface.
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(fg, cls, 64)
            cname = (cls.value or '')
            if cname.startswith('PotPlayer'):
                return 'potplayer'
        except Exception:
            return False
        return False

    def _hook_loop(self):
        user32 = ctypes.windll.user32
        _configure_hook_prototypes()
        self._tid = threading.get_ident()
        # Pointer-sized LRESULT/WPARAM/LPARAM — required on Win64.
        HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)

        def _pass(nCode, wParam, lParam):
            try:
                return int(user32.CallNextHookEx(self._hook or 0, nCode, wParam, lParam) or 0)
            except Exception:
                # Last resort: 0 means "pass to next hook / default processing".
                return 0

        def _handler(nCode, wParam, lParam):
            # Absolute rule: never block; never EnumWindows; never call app logic.
            # Absolute rule: on any failure, pass the key through (return 0 / CallNext).
            try:
                if int(nCode) < 0:
                    return _pass(nCode, wParam, lParam)
                if int(nCode) == self.HC_ACTION:
                    info = ctypes.cast(lParam, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
                    flags = int(info.flags)
                    vk = int(info.vkCode) & 0xFF
                    is_up = bool(flags & self.LLKHF_UP) or int(wParam) in (
                        self.WM_KEYUP, self.WM_SYSKEYUP,
                    )
                    # Track modifiers locally — avoids GetAsyncKeyState in the hot path.
                    mod_bit = _vk_to_mod_bit(vk)
                    if mod_bit:
                        if is_up:
                            self._mods_down &= ~mod_bit
                        else:
                            self._mods_down |= mod_bit
                        # Modifiers always pass through (never consume).
                        return _pass(nCode, wParam, lParam)

                    if is_up:
                        if vk in self._suppress_keyup:
                            self._suppress_keyup.discard(vk)
                            # Nonzero LRESULT = do not deliver this key to any app.
                            return 1
                        return _pass(nCode, wParam, lParam)

                    # Recording must observe the key even if another layer marked it
                    # injected; Escape cancels without saving.
                    if self.is_recording() and int(wParam) in (
                        self.WM_KEYDOWN, self.WM_SYSKEYDOWN,
                    ):
                        if vk == 0x1B:  # VK_ESCAPE — cancel, keep previous binding
                            with self._lock:
                                self._recording_action = None
                                self._recording_callback = None
                            self._suppress_keyup.add(vk)
                            return 1
                        if self._decide_consume(vk):
                            self._suppress_keyup.add(vk)
                            return 1

                    if (flags & self.LLKHF_INJECTED) and not self._allow_injected:
                        return _pass(nCode, wParam, lParam)

                    # Key-down / key-repeat (auto-repeat arrives as extra KEYDOWNs).
                    if int(wParam) in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN) or not is_up:
                        if self._decide_consume(vk):
                            self._suppress_keyup.add(vk)
                            # Critical: nonzero return suppresses the event BEFORE
                            # PotPlayer (or any target window) receives it.
                            return 1
            except Exception:
                return _pass(nCode, wParam, lParam)
            return _pass(nCode, wParam, lParam)

        self._proc = HOOKPROC(_handler)
        # Keep a reference so ctypes does not GC the callback.
        self._proc_ref = self._proc

        self._refresh_hwnd_cache()
        self._hook = user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL, self._proc, None, 0,
        )
        if not self._hook:
            self._warning = (
                'Could not register keyboard hook. Choose another shortcut or '
                'run Huge again; Windows may be blocking global key capture.'
            )
            self._running = False
            return

        # Periodic HWND refresh on this thread (not inside the hook callback).
        user32.SetTimer(None, self.TIMER_ID, int(self.HWND_REFRESH_MS), None)
        msg = wintypes.MSG()
        while self._running:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or result == -1:
                break
            if msg.message == self.WM_TIMER:
                try:
                    self._refresh_hwnd_cache()
                except Exception:
                    pass
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.KillTimer(None, self.TIMER_ID)
        except Exception:
            pass
        hook = self._hook
        self._hook = None
        if hook:
            try:
                user32.UnhookWindowsHookEx(hook)
            except Exception:
                pass

    def _decide_consume(self, vk_code):
        """Return True only when this keydown should be eaten. Hook-safe."""
        with self._lock:
            recording = self._recording_action
            callback = self._recording_callback
            enabled = self._enabled
            table = self._bind_snapshot
            mods = int(self._mods_down) & 0x0F

        if recording:
            combo = combo_from_key_event(vk_code, mods)
            if not combo:
                # Modifier-only already passed through; other incomplete events pass too.
                return False
            _mods, _vk, label = combo
            with self._lock:
                self._recording_action = None
                self._recording_callback = None
                action = recording
            # Persist off-hook via the action worker so we return immediately.
            def _finish_record():
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
            try:
                self._action_q.put_nowait(('__record__', _finish_record, time.monotonic()))
            except Exception:
                # Fallback: still must not block the hook for long.
                threading.Thread(target=_finish_record, daemon=True).start()
            return True

        if not enabled:
            return False

        action = table.get((mods, int(vk_code) & 0xFF))
        if not action:
            return False

        scope = self._scope_from_cache()
        if not scope:
            return False
        if scope == 'huge':
            try:
                if self._is_text_entry_active():
                    return False
            except Exception:
                # If the probe fails, do not steal typing keys.
                return False

        now = time.monotonic()
        last = self._last_fire.get(action, 0.0)
        if now - last < self._debounce_s:
            # Eat key-repeat for an already-handled shortcut only.
            return True
        self._last_fire[action] = now
        try:
            self._action_q.put_nowait((action, scope, now))
        except Exception:
            return False
        return True

    # --- Test / diagnostic helpers (not used by the LL callback) ---

    def decide_consume_for_test(self, vk_code, mods=0, scope='huge', text_entry=False):
        """Pure decision helper for unit tests (no Win32)."""
        self._mods_down = int(mods) & 0x0F
        self._cached_huge_roots = frozenset({1}) if scope == 'huge' else frozenset()
        self._cached_pot_root = 2 if scope == 'potplayer' else 0
        # Monkey the scope + text probes without touching real windows.
        self._scope_from_cache = lambda: scope  # type: ignore
        self._is_text_entry_active = lambda: bool(text_entry)
        return self._decide_consume(int(vk_code))
