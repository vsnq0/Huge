"""Live PotPlayer focus test for reserved Huge playback shortcuts.

Launches real PotPlayer (if installed), focuses it, installs the WH_KEYBOARD_LL
hook, and verifies Space / < / > fire exactly one Huge action each while
unrelated keys are not consumed.
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

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine.hotkeys import (  # noqa: E402
    MOD_SHIFT,
    PlaybackHotkeyManager,
    VK_OEM_COMMA,
    VK_OEM_PERIOD,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_SPACE = 0x20
VK_SHIFT = 0x10
KEYEVENTF_KEYUP = 0x0002
GA_ROOT = 2

PP_CANDIDATES = [
    r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe',
    r'C:\Program Files\DAUM\PotPlayer\PotPlayer.exe',
    r'C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe',
]


def ok(name, cond, detail=None):
    mark = 'PASS' if cond else 'FAIL'
    extra = f' ({detail})' if detail is not None else ''
    print(f'{mark} - {name}{extra}')
    return bool(cond)


def find_pp_exe():
    for path in PP_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def find_hwnd_for_pid(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(out))
        if out.value == pid:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else 0


def force_foreground(hwnd):
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_tid = kernel32.GetCurrentThreadId()
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, True)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    finally:
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def tap(vk, up=False):
    # keybd_event is marked injected but is reliable across focus changes;
    # HUGE_HOTKEY_ALLOW_INJECTED=1 lets the LL hook accept these test events.
    user32.keybd_event(int(vk) & 0xFF, 0, KEYEVENTF_KEYUP if up else 0, 0)


def chord_shift(vk):
    tap(VK_SHIFT, False)
    time.sleep(0.04)
    tap(vk, False)
    time.sleep(0.02)
    tap(vk, True)
    time.sleep(0.02)
    tap(VK_SHIFT, True)
    time.sleep(0.06)


def wait_actions(fired, before_len, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(fired) > before_len:
            time.sleep(0.08)  # let debounce/worker settle
            return
        time.sleep(0.02)


def main():
    exe = find_pp_exe()
    if not exe:
        print('SKIP - PotPlayer not installed')
        return 0

    proc = subprocess.Popen([exe])
    hwnd = 0
    for _ in range(50):
        hwnd = find_hwnd_for_pid(proc.pid)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        print('FAIL - no PotPlayer window')
        try:
            proc.kill()
        except Exception:
            pass
        return 1

    force_foreground(hwnd)
    root = int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)
    fg = int(user32.GetForegroundWindow() or 0)
    fg_root = int(user32.GetAncestor(fg, GA_ROOT) or fg) if fg else 0
    focused = fg == hwnd or fg_root == root
    # Title fallback still works even if SetForegroundWindow is blocked.
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value or ''
    print(f'pp hwnd={hwnd} root={root} fg={fg} title={title!r} focused={focused}')

    fired = []

    def on_action(action, scope=None, mono=None):
        fired.append((action, scope))

    mgr = PlaybackHotkeyManager(
        on_action=on_action,
        get_huge_hwnds=lambda: [],
        is_text_entry_active=lambda: False,
        find_pot_hwnd=lambda: hwnd,
    )
    if not mgr._allow_injected:
        print('FAIL - injected keys not allowed')
        proc.kill()
        return 1

    mgr.start()
    time.sleep(0.9)
    mgr._cached_pot_root = root
    mgr._cached_huge_roots = frozenset()
    force_foreground(hwnd)
    time.sleep(0.2)

    passed = True

    before_n = len(fired)
    tap(VK_SPACE, False)
    tap(VK_SPACE, True)
    wait_actions(fired, before_n)
    space_hits = [a for a in fired[before_n:] if a[0] == 'play_pause']
    passed &= ok('Space -> one play_pause', len(space_hits) == 1, fired[before_n:])

    before_n = len(fired)
    chord_shift(VK_OEM_COMMA)
    wait_actions(fired, before_n)
    back_hits = [a for a in fired[before_n:] if a[0] == 'seek_back']
    passed &= ok('< -> one seek_back', len(back_hits) == 1, fired[before_n:])

    before_n = len(fired)
    chord_shift(VK_OEM_PERIOD)
    wait_actions(fired, before_n)
    fwd_hits = [a for a in fired[before_n:] if a[0] == 'seek_forward']
    passed &= ok('> -> one seek_forward', len(fwd_hits) == 1, fired[before_n:])

    before_n = len(fired)
    tap(ord('V'), False)
    tap(ord('V'), True)
    time.sleep(0.35)
    passed &= ok('V does not fire Huge action', fired[before_n:] == [], fired[before_n:])

    passed &= ok(
        'Space consumed when PotPlayer scoped',
        mgr.decide_consume_for_test(VK_SPACE, mods=0, scope='potplayer') is True,
    )
    # Reset debounce/side effects from decide helper — re-snapshot bindings.
    mgr._last_fire.clear()
    passed &= ok(
        'Shift+comma consumed',
        mgr.decide_consume_for_test(VK_OEM_COMMA, mods=MOD_SHIFT, scope='potplayer') is True,
    )
    mgr._last_fire.clear()
    passed &= ok(
        'bare comma not consumed',
        mgr.decide_consume_for_test(VK_OEM_COMMA, mods=0, scope='potplayer') is False,
    )
    mgr._last_fire.clear()
    passed &= ok(
        'F key not consumed (fullscreen-like)',
        mgr.decide_consume_for_test(ord('F'), mods=0, scope='potplayer') is False,
    )
    mgr._last_fire.clear()
    passed &= ok(
        'Up arrow not consumed (volume-like)',
        mgr.decide_consume_for_test(0x26, mods=0, scope='potplayer') is False,
    )

    mgr.stop()
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    print('=== REAL POTPLAYER SUMMARY ===')
    print('ALL PASS' if passed else 'FAILED')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
