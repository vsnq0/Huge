"""Real PotPlayer test: Huge owns Space/Left/Right while PotPlayer is focused.

1. Apply MainShortCutList no-ops for Huge defaults, then relaunch PotPlayer
   (overrides load at process start).
2. With PotPlayer focused and NO Huge hook: Space must NOT toggle play state
   (proves PotPlayer native binding is disabled).
3. Install Huge's WH_KEYBOARD_LL: Space/Left/Right each fire exactly one Huge
   action; PotPlayer play/position must not also move from those keys.
4. An unrelated letter must not be consumed by Huge.
5. Restore PotPlayer shortcut backup.

Requires installed PotPlayer + a short media file.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

os.environ['HUGE_HOTKEY_ALLOW_INJECTED'] = '1'
os.environ.pop('HUGE_DISABLE_HOTKEYS', None)
os.environ.pop('HUGE_SKIP_POT_SHORTCUT_SYNC', None)

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine import potplayer_shortcuts as pps  # noqa: E402
from engine.hotkeys import DEFAULT_SHORTCUTS, PlaybackHotkeyManager  # noqa: E402
from engine.session import (  # noqa: E402
    STATUS_PLAYING,
    PotController,
    _find_pot_hwnd,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_SPACE = 0x20
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_A = 0x41
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
        time.sleep(0.35)
    finally:
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def tap(vk):
    user32.keybd_event(int(vk) & 0xFF, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(int(vk) & 0xFF, 0, KEYEVENTF_KEYUP, 0)


def kill_proc(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_playing(pot, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = pot.snapshot()
        if snap.get('status') == STATUS_PLAYING:
            return snap
        time.sleep(0.15)
    return pot.snapshot()


def main():
    exe = find_pp_exe()
    media = media_path()
    if not exe:
        print('SKIP - PotPlayer not installed')
        return 0
    if not media:
        print('SKIP - no short media file found')
        return 0
    support = pps.is_supported_on_this_machine()
    if not support.get('ok'):
        print('SKIP - PotPlayer shortcut storage not identified')
        return 0

    passed = True
    tmp = tempfile.mkdtemp(prefix='huge-pp-live-')
    prev_profile = os.environ.get('HUGE_PROFILE_DIR')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    live_snap = pps.snapshot_all(exe)
    proc = None
    pot = None
    mgr = None
    try:
        for p in ('PotPlayerMini64', 'PotPlayer', 'PotPlayerMini'):
            subprocess.run(['taskkill', '/F', '/IM', f'{p}.exe'], capture_output=True)

        apply = pps.apply_huge_conflicts(DEFAULT_SHORTCUTS, potplayer_exe=exe)
        passed &= ok('apply overrides', apply.get('ok'), apply)

        proc = subprocess.Popen([exe, media])
        hwnd = 0
        for _ in range(60):
            hwnd = int(_find_pot_hwnd() or 0)
            if hwnd:
                break
            time.sleep(0.15)
        passed &= ok('PotPlayer launched', bool(hwnd), hwnd)
        if not hwnd:
            return 1 if not passed else 0

        pot = PotController()
        snap = wait_playing(pot)
        if snap.get('status') != STATUS_PLAYING:
            pot.cmd(10000)  # play
            snap = wait_playing(pot)
        passed &= ok('media playing', snap.get('status') == STATUS_PLAYING, snap)

        # --- native Space disabled (no Huge hook) ---
        force_foreground(hwnd)
        before = pot.snapshot()
        tap(VK_SPACE)
        time.sleep(0.55)
        after = pot.snapshot()
        passed &= ok(
            'native Space disabled (no Huge hook)',
            before.get('status') == after.get('status') == STATUS_PLAYING,
            f'before={before.get("status")} after={after.get("status")}',
        )

        # --- Huge hook while PotPlayer focused ---
        fired = []

        def on_action(action, scope='huge', mono=None):
            fired.append((action, scope))

        mgr = PlaybackHotkeyManager(
            on_action=on_action,
            get_huge_hwnds=lambda: [],
            is_text_entry_active=lambda: False,
            find_pot_hwnd=lambda: int(_find_pot_hwnd() or hwnd),
        )
        mgr.load_dict(DEFAULT_SHORTCUTS)
        mgr.start()
        time.sleep(0.4)
        root = int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)
        mgr._cached_pot_root = root
        mgr._cached_pot_pid = proc.pid
        try:
            from engine.session import _find_pot_pids
            mgr._cached_pot_pids = frozenset(_find_pot_pids() or [proc.pid])
        except Exception:
            mgr._cached_pot_pids = frozenset([proc.pid])
        force_foreground(hwnd)
        time.sleep(0.25)

        fg = user32.GetForegroundWindow()
        fg_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
        passed &= ok(
            'PotPlayer foreground for hook test',
            int(fg_pid.value) == int(proc.pid),
            f'fg_pid={fg_pid.value} proc={proc.pid}',
        )

        n0 = len(fired)
        tap(VK_SPACE)
        time.sleep(0.45)
        passed &= ok(
            'Space → one Huge play_pause',
            len(fired) == n0 + 1 and fired[-1][0] == 'play_pause',
            fired[n0:],
        )
        if fired[n0:]:
            passed &= ok('Space scope potplayer', fired[-1][1] == 'potplayer', fired[-1])
        time.sleep(0.2)
        passed &= ok(
            'PP still playing after hooked Space (noop handler)',
            pot.snapshot().get('status') == STATUS_PLAYING,
            pot.snapshot(),
        )

        pos0 = int(pot.snapshot().get('position') or 0)
        n1 = len(fired)
        tap(VK_LEFT)
        time.sleep(0.45)
        passed &= ok(
            'Left → one seek_back',
            len(fired) == n1 + 1 and fired[-1][0] == 'seek_back',
            fired[n1:],
        )
        pos1 = int(pot.snapshot().get('position') or 0)
        passed &= ok('Left did not native-seek ~5s', abs(pos1 - pos0) < 2500, f'{pos0}->{pos1}')

        n2 = len(fired)
        tap(VK_RIGHT)
        time.sleep(0.45)
        passed &= ok(
            'Right → one seek_forward',
            len(fired) == n2 + 1 and fired[-1][0] == 'seek_forward',
            fired[n2:],
        )

        n3 = len(fired)
        user32.keybd_event(VK_SPACE, 0, 0, 0)
        time.sleep(0.55)
        user32.keybd_event(VK_SPACE, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.25)
        passed &= ok('held Space → single action', len(fired) == n3 + 1, fired[n3:])

        n4 = len(fired)
        tap(VK_A)
        time.sleep(0.25)
        passed &= ok('letter A not a Huge shortcut', len(fired) == n4, fired[n4:])

        mgr.stop()
        mgr = None

        custom = {
            'play_pause': 'F8',
            'seek_back': 'Left Arrow',
            'seek_forward': 'Right Arrow',
        }
        apply2 = pps.apply_huge_conflicts(custom, potplayer_exe=exe)
        passed &= ok('custom apply', apply2.get('ok'), apply2)
        f8_ok = False
        for base in pps._active_reg_bases():
            for list_name in pps._LIST_NAMES:
                values = pps._read_reg_list(base, list_name) or {}
                if any(str(v).startswith('119,0,0,') for v in values.values()):
                    f8_ok = True
        passed &= ok('F8 noop in registry', f8_ok)

        restored = pps.restore_original(potplayer_exe=exe)
        passed &= ok('restore_original', restored.get('ok') and restored.get('restored'), restored)
    except Exception as exc:
        passed = False
        print('FAIL - exception', exc)
        import traceback
        traceback.print_exc()
    finally:
        if mgr is not None:
            try:
                mgr.stop()
            except Exception:
                pass
        if pot is not None:
            try:
                pot.close()
            except Exception:
                pass
        if proc is not None:
            kill_proc(proc)
        try:
            for base, lists in (live_snap.get('registry') or {}).items():
                for list_name, values in (lists or {}).items():
                    if values is None:
                        pps._write_reg_list(base, list_name, None)
                    else:
                        ordered = [
                            values[k] for k in sorted(
                                values.keys(), key=lambda x: int(x) if str(x).isdigit() else 0,
                            )
                        ]
                        if ordered:
                            pps._write_reg_list_ordered(base, list_name, ordered)
                        else:
                            pps._write_reg_list(base, list_name, None)
        except Exception:
            pass
        if prev_profile is None:
            os.environ.pop('HUGE_PROFILE_DIR', None)
        else:
            os.environ['HUGE_PROFILE_DIR'] = prev_profile
        shutil.rmtree(tmp, ignore_errors=True)

    print('RESULT', 'PASS' if passed else 'FAIL')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
