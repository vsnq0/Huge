"""Regression: Huge shortcuts must work while PotPlayer is foreground (v2.0.28 bug).

v2.0.28 starved the WH_KEYBOARD_LL thread by OpenProcess-per-window inside
EnumWindows during HWND refresh, and live tests injected a fixed find_pot_hwnd
so they never caught it. Huge-focused shortcuts still worked via Huge HWND
cache; PotPlayer-focused ones did not.

This test uses the REAL _find_pot_hwnd / _find_pot_pids path (no injected HWND).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

os.environ['HUGE_HOTKEY_ALLOW_INJECTED'] = '1'
os.environ.pop('HUGE_DISABLE_HOTKEYS', None)
os.environ.pop('HUGE_FAKE_POT', None)

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine.hotkeys import PlaybackHotkeyManager, default_shortcuts_dict  # noqa: E402
from engine.session import _find_pot_hwnd, _find_pot_pids  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_SPACE = 0x20
VK_LEFT = 0x25
VK_RIGHT = 0x27
KEYEVENTF_KEYUP = 2
GA_ROOT = 2

PP_CANDIDATES = [
    r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe',
    r'C:\Program Files\DAUM\PotPlayer\PotPlayer.exe',
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
        time.sleep(0.25)
    finally:
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def tap(vk):
    user32.keybd_event(int(vk) & 0xFF, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(int(vk) & 0xFF, 0, KEYEVENTF_KEYUP, 0)


def wait_actions(fired, before_len, timeout=1.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(fired) > before_len:
            time.sleep(0.08)
            return
        time.sleep(0.02)


def main():
    exe = find_pp_exe()
    media = media_path()
    if not exe:
        print('SKIP - PotPlayer not installed')
        return 0

    args = [exe]
    if media:
        args.append(media)
    proc = subprocess.Popen(args)
    hwnd = 0
    for _ in range(60):
        hwnd = int(_find_pot_hwnd() or 0)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        print('FAIL - real _find_pot_hwnd returned nothing')
        try:
            proc.kill()
        except Exception:
            pass
        return 1

    pids = _find_pot_pids()
    print(f'real find hwnd={hwnd} pot_pids={sorted(pids)} proc.pid={proc.pid}')

    force_foreground(hwnd)
    fg = int(user32.GetForegroundWindow() or 0)
    root = int(user32.GetAncestor(fg, GA_ROOT) or fg)
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(fg, buf, 256)
    print(f'foreground hwnd={fg} root={root} title={buf.value!r}')

    fired = []

    def on_action(action, scope=None, mono=None):
        fired.append((action, scope))

    # CRITICAL: use the real session finders — do NOT inject a fixed HWND.
    mgr = PlaybackHotkeyManager(
        on_action=on_action,
        get_huge_hwnds=lambda: [],  # Huge is "in background"
        is_text_entry_active=lambda: False,
        find_pot_hwnd=_find_pot_hwnd,
    )
    mgr.load_dict(default_shortcuts_dict())
    # Custom binding must also work while PotPlayer is focused.
    mgr.set_shortcut('play_pause', 'F8')
    mgr.start()
    time.sleep(0.8)
    mgr._refresh_hwnd_cache()
    force_foreground(hwnd)
    time.sleep(0.3)
    mgr._refresh_hwnd_cache()

    passed = True
    scope = mgr._scope_from_cache()
    passed &= ok('scope is potplayer with real finder', scope == 'potplayer', scope)
    passed &= ok('cached pot_pids non-empty', bool(mgr._cached_pot_pids), mgr._cached_pot_pids)
    passed &= ok(
        'proc pid in cached pot_pids',
        int(proc.pid) in set(mgr._cached_pot_pids) or int(mgr._cached_pot_pid) == int(proc.pid),
        {'cached': set(mgr._cached_pot_pids), 'pid': mgr._cached_pot_pid, 'proc': proc.pid},
    )

    # Default Left/Right still bound; Play/Pause rebound to F8.
    before = len(fired)
    tap(VK_LEFT)
    wait_actions(fired, before)
    passed &= ok(
        'Left while PP foreground -> one seek_back',
        [a for a, s in fired[before:] if a == 'seek_back'] == ['seek_back']
        and all(s == 'potplayer' for a, s in fired[before:] if a == 'seek_back'),
        fired[before:],
    )

    before = len(fired)
    tap(VK_RIGHT)
    wait_actions(fired, before)
    passed &= ok(
        'Right while PP foreground -> one seek_forward',
        [a for a, s in fired[before:] if a == 'seek_forward'] == ['seek_forward'],
        fired[before:],
    )

    before = len(fired)
    tap(0x77)  # F8
    wait_actions(fired, before)
    passed &= ok(
        'custom F8 while PP foreground -> one play_pause',
        [a for a, s in fired[before:] if a == 'play_pause'] == ['play_pause'],
        fired[before:],
    )

    # Former Space must NOT fire Huge after rebind.
    before = len(fired)
    tap(VK_SPACE)
    time.sleep(0.35)
    passed &= ok('Space no longer fires Huge after rebind', fired[before:] == [], fired[before:])

    # Hold-repeat must not spam.
    before = len(fired)
    user32.keybd_event(VK_LEFT, 0, 0, 0)
    time.sleep(0.45)  # long enough for OS auto-repeat
    user32.keybd_event(VK_LEFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)
    left_hits = [a for a, _s in fired[before:] if a == 'seek_back']
    passed &= ok('held Left fires once only', len(left_hits) == 1, left_hits)

    mgr.stop()
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    print('=== POTPLAYER FOREGROUND REGRESSION ===')
    print('ALL PASS' if passed else 'FAILED')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
