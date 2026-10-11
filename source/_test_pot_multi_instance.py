"""Probe whether two PotPlayer Mini64 processes can be targeted independently.

Does NOT use Huge lobby networking. Reports honestly if Win32 targeting
cannot isolate instances (common: FindWindow finds one class window).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    CMD_PLAY_PAUSE,
    STATUS_PAUSED,
    STATUS_PLAYING,
    find_potplayer_exe,
    pot_cmd,
    pot_current,
    pot_set_current,
    pot_status,
    pot_window,
)


def _kill_all():
    subprocess.run(
        ['taskkill', '/F', '/IM', 'PotPlayerMini64.exe'],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ['taskkill', '/F', '/IM', 'PotPlayer64.exe'],
        capture_output=True,
        check=False,
    )
    time.sleep(0.8)


def _launch(exe: str, media: str) -> subprocess.Popen:
    # Separate working dirs; Mini64 still often shares one Win32 target.
    return subprocess.Popen(
        [exe, '/current', media],
        cwd=os.path.dirname(exe),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _make_media(path: str, seconds: int = 12):
    # Tiny silent mp4 via ffmpeg if present; else skip with clear message.
    ffmpeg = None
    for cand in ('ffmpeg', r'C:\ffmpeg\bin\ffmpeg.exe'):
        try:
            subprocess.run([cand, '-version'], capture_output=True, check=True)
            ffmpeg = cand
            break
        except Exception:
            continue
    if not ffmpeg:
        return False
    subprocess.run(
        [
            ffmpeg, '-y', '-f', 'lavfi', '-i', f'color=c=black:s=320x240:d={seconds}',
            '-f', 'lavfi', '-i', f'anullsrc=r=44100:cl=mono',
            '-shortest', '-c:v', 'libx264', '-t', str(seconds), '-pix_fmt', 'yuv420p', path,
        ],
        capture_output=True,
        check=True,
    )
    return True


def main():
    exe = find_potplayer_exe()
    print('PotPlayer exe:', exe or 'NOT FOUND')
    if not exe:
        print('RESULT: real PotPlayer multi-instance = NOT TESTED (exe missing)')
        return 2

    work = tempfile.mkdtemp(prefix='huge_pot_multi_')
    media = os.path.join(work, 'probe.mp4')
    if not _make_media(media):
        # Fall back to any short local sample if present
        samples = [
            os.path.join(ROOT, 'test_movie.mp4'),
            os.path.join(ROOT, 'assets', 'test_movie.mp4'),
        ]
        media = next((p for p in samples if os.path.isfile(p)), '')
        if not media:
            print('RESULT: real PotPlayer multi-instance = NOT TESTED (no media/ffmpeg)')
            return 2

    _kill_all()
    p1 = _launch(exe, media)
    time.sleep(2.5)
    hwnd1 = pot_window()
    st1 = pot_status()
    print(f'instance1 hwnd={hwnd1} status={st1} pid={p1.pid}')

    p2 = _launch(exe, media)
    time.sleep(2.5)
    hwnd2 = pot_window()
    st2 = pot_status()
    print(f'after 2nd launch hwnd={hwnd2} status={st2} pid={p2.pid}')

    # Count processes
    try:
        out = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Process PotPlayerMini64,PotPlayer64 -ErrorAction SilentlyContinue).Count'],
            text=True,
        ).strip()
        proc_count = int(out or '0')
    except Exception:
        proc_count = -1
    print('running PotPlayer processes:', proc_count)

    # Try to pause via Huge helpers (targets FindWindow class — usually one window).
    before = pot_status()
    pot_cmd(CMD_PLAY_PAUSE)
    time.sleep(0.3)
    after = pot_status()
    print(f'toggle via pot_cmd: before={before} after={after}')

    # Seek probe
    pot_set_current(5000)
    time.sleep(0.4)
    pos = pot_current()
    print(f'seek via pot_set_current -> {pos}')

    isolated = proc_count >= 2 and hwnd1 and hwnd2 and int(hwnd1) != int(hwnd2)
    print('distinct hwnds after dual launch:', bool(isolated), f'({hwnd1} vs {hwnd2})')
    if proc_count >= 2 and not isolated:
        print(
            'RESULT: real PotPlayer multi-instance = PROCESSES STARTED BUT NOT '
            'INDEPENDENTLY TARGETABLE via Huge pot_window()/SendMessage path'
        )
        code = 1
    elif isolated:
        print('RESULT: real PotPlayer multi-instance = DISTINCT HWNDS OBSERVED')
        code = 0
    else:
        print('RESULT: real PotPlayer multi-instance = FAILED TO START TWO PROCESSES')
        code = 1

    _kill_all()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
