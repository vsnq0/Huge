"""Prove Space/Left/Right are swallowed by the LL hook before later hooks/apps.

Strategy:
1. Install a probe WH_KEYBOARD_LL first (called later in the chain).
2. Install Huge's PlaybackHotkeyManager (called first).
3. When Huge consumes a key it returns nonzero — the probe must NOT see that key.
4. With PotPlayer focused + noop Huge handler, PotPlayer play state must not
   change from Space, and position must not jump ~5s from Left/Right.
5. After Huge unhooks, the probe must see the same keys again.

Requires real PotPlayer + a short media file on Windows.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

os.environ['HUGE_HOTKEY_ALLOW_INJECTED'] = '1'
os.environ.pop('HUGE_DISABLE_HOTKEYS', None)

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine.hotkeys import (  # noqa: E402
    PlaybackHotkeyManager,
    _LPARAM,
    _LRESULT,
    _WPARAM,
    _configure_hook_prototypes,
)
from engine.session import PotController, STATUS_PLAYING, _find_pot_hwnd  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_SPACE = 0x20
VK_LEFT = 0x25
VK_RIGHT = 0x27
KEYEVENTF_KEYUP = 2
GA_ROOT = 2
WH_KEYBOARD_LL = 13
HC_ACTION = 0
LLKHF_UP = 0x80
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

PP_CANDIDATES = [
    r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe',
    r'C:\Program Files\DAUM\PotPlayer\PotPlayer.exe',
]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


def ok(name, cond, detail=None):
    print(('PASS' if cond else 'FAIL'), '-', name + (f' ({detail})' if detail is not None else ''))
    return bool(cond)


def find_pp_exe():
    for path in PP_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def media_path():
    for name in ('Alarm01.wav', 'Ring01.wav', 'Speech On.wav'):
        path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Media', name)
        if os.path.isfile(path):
            return path
    return None


def force_foreground(hwnd):
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_tid = kernel32.GetCurrentThreadId()
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    try:
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, True)
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, True)
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    finally:
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def tap(vk):
    user32.keybd_event(int(vk) & 0xFF, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(int(vk) & 0xFF, 0, KEYEVENTF_KEYUP, 0)


def wait_playing(pot, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = pot.snapshot()
        if snap.get('status') == STATUS_PLAYING:
            return snap
        time.sleep(0.15)
    return pot.snapshot()


class ProbeHook:
    """Installed first so Huge (installed second) is called before us."""

    def __init__(self):
        self.counts = {VK_SPACE: 0, VK_LEFT: 0, VK_RIGHT: 0, ord('F'): 0}
        self._hook = None
        self._thread = None
        self._running = False
        self._proc = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._hook:
                return True
            time.sleep(0.02)
        return False

    def stop(self):
        self._running = False
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        if self._thread and self._thread.is_alive():
            tid = int(getattr(self, '_tid', 0) or 0)
            if tid:
                user32.PostThreadMessageW(tid, 0x0012, 0, 0)
            self._thread.join(timeout=2.0)

    def _loop(self):
        _configure_hook_prototypes()
        self._tid = threading.get_ident()
        HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)

        def handler(nCode, wParam, lParam):
            try:
                if int(nCode) == HC_ACTION:
                    info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    flags = int(info.flags)
                    vk = int(info.vkCode) & 0xFF
                    is_up = bool(flags & LLKHF_UP) or int(wParam) in (0x0101, 0x0105)
                    if (not is_up) and int(wParam) in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if vk in self.counts:
                            self.counts[vk] += 1
            except Exception:
                pass
            return int(user32.CallNextHookEx(self._hook or 0, nCode, wParam, lParam) or 0)

        self._proc = HOOKPROC(handler)
        self._proc_ref = self._proc
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        msg = wintypes.MSG()
        while self._running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


def main():
    exe = find_pp_exe()
    media = media_path()
    if not exe or not media:
        print('SKIP - PotPlayer or media missing')
        return 0

    for p in ['PotPlayerMini64', 'PotPlayer', 'PotPlayerMini']:
        subprocess.run(['taskkill', '/F', '/IM', f'{p}.exe'], capture_output=True)

    proc = subprocess.Popen([exe, media])
    hwnd = 0
    for _ in range(50):
        hwnd = int(_find_pot_hwnd() or 0)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        print('FAIL - no PotPlayer hwnd')
        proc.kill()
        return 1

    pot = PotController()
    force_foreground(hwnd)
    print('initial', wait_playing(pot), 'hwnd', hwnd)

    probe = ProbeHook()
    if not probe.start():
        print('FAIL - probe hook not installed')
        pot.close()
        proc.kill()
        return 1
    time.sleep(0.2)

    fired = []

    def on_action(action, scope=None, mono=None):
        fired.append(action)

    mgr = PlaybackHotkeyManager(
        on_action=on_action,
        get_huge_hwnds=lambda: [],
        is_text_entry_active=lambda: False,
        find_pot_hwnd=lambda: hwnd,
    )
    mgr.start()
    time.sleep(1.0)
    root = int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)
    mgr._cached_pot_root = root
    mgr._cached_pot_pid = proc.pid
    force_foreground(hwnd)
    time.sleep(0.25)

    pot.cmd(10000)
    time.sleep(0.5)
    before = wait_playing(pot)
    print('before keys', before, 'scope', mgr._scope_from_cache())

    passed = True

    for label, vk, action in (
        ('Space', VK_SPACE, 'play_pause'),
        ('Left', VK_LEFT, 'seek_back'),
        ('Right', VK_RIGHT, 'seek_forward'),
    ):
        probe.counts[vk] = 0
        before_n = len(fired)
        pos0 = int(pot.snapshot().get('position') or 0)
        tap(vk)
        time.sleep(0.55)
        snap = pot.snapshot()
        print(f'after {label}+hook', snap, 'fired', fired[before_n:], 'probe', probe.counts[vk])
        passed &= ok(
            f'Huge {action} once for {label}',
            fired[before_n:].count(action) == 1,
            fired[before_n:],
        )
        passed &= ok(
            f'probe did not see {label} (swallowed by Huge)',
            probe.counts[vk] == 0,
            probe.counts[vk],
        )
        if label == 'Space':
            passed &= ok(
                'PotPlayer still playing after noop Space',
                snap.get('status') == STATUS_PLAYING,
                snap,
            )
        else:
            delta = abs(int(snap.get('position') or 0) - pos0)
            passed &= ok(
                f'PotPlayer no ~5s native seek on {label}',
                delta < 2500,
                f'pos {pos0}->{snap.get("position")}',
            )

    probe.counts[ord('F')] = 0
    fired.clear()
    tap(ord('F'))
    time.sleep(0.4)
    passed &= ok('F not claimed by Huge', fired == [], fired)
    passed &= ok('probe saw F (not swallowed)', probe.counts[ord('F')] >= 1, probe.counts[ord('F')])

    mgr.stop()
    time.sleep(0.4)
    force_foreground(hwnd)
    for label, vk in (('Space', VK_SPACE), ('Left', VK_LEFT), ('Right', VK_RIGHT)):
        probe.counts[vk] = 0
        tap(vk)
        time.sleep(0.4)
        passed &= ok(
            f'probe sees {label} after Huge unhook',
            probe.counts[vk] >= 1,
            probe.counts[vk],
        )

    probe.stop()
    pot.close()
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    print('=== POTPLAYER SUPPRESS SUMMARY ===')
    print('ALL PASS' if passed else 'FAILED')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
