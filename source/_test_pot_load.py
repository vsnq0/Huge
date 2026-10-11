"""Verify PotPlayer CLI load strategies against real PotPlayerMini64."""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_STOPPED,
    find_potplayer_exe,
    pot_current,
    pot_pause,
    pot_status,
    pot_total,
    pot_window,
)

MEDIA = os.path.join(ROOT, '_test_media', 'sample.mp4')
EXE = find_potplayer_exe() or r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe'


def kill_pot():
    subprocess.run(
        ['taskkill', '/F', '/IM', 'PotPlayerMini64.exe'],
        capture_output=True,
        check=False,
    )
    time.sleep(0.8)


def wait_hwnd(timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pot_window():
            return True
        time.sleep(0.1)
    return False


def probe(label, seconds=3.0):
    time.sleep(0.3)
    samples = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        samples.append((pot_status(), pot_current(), pot_total()))
        time.sleep(0.25)
    print(f'[{label}] samples={samples[-5:]}')
    last = samples[-1]
    return last


def test_add_only():
    kill_pot()
    subprocess.Popen([EXE, MEDIA, '/add'], close_fds=True)
    ok_hwnd = wait_hwnd()
    status, pos, total = probe('add_only', 4.0)
    print(f'add_only hwnd={ok_hwnd} status={status} pos={pos} total={total}')
    # Empty playlist-add typically stays STOPPED with total -1/0
    loaded = status in (STATUS_PLAYING, STATUS_PAUSED) and isinstance(total, int) and total > 0
    return loaded


def test_current_immediate_pause():
    kill_pot()
    subprocess.Popen([EXE, MEDIA, '/current'], close_fds=True)
    if not wait_hwnd(20):
        print('FAIL no hwnd')
        return False
    # Immediate pause race — no 1s sleep
    deadline = time.time() + 20.0
    paused = False
    while time.time() < deadline:
        st = pot_status()
        if st == STATUS_PLAYING:
            pot_pause()
        elif st == STATUS_PAUSED:
            paused = True
            break
        time.sleep(0.05)
    # seek zero
    from engine.session import pot_set_current_sync
    pot_set_current_sync(0, timeout=2.0)
    time.sleep(0.3)
    if pot_status() == STATUS_PLAYING:
        pot_pause()
        time.sleep(0.2)
    status, pos, total = probe('current_pause', 2.0)
    print(f'current_pause paused_seen={paused} status={status} pos={pos} total={total}')
    hold = True
    t0 = time.time()
    while time.time() - t0 < 3.0:  # short hold check (full 15s in integration)
        if pot_status() == STATUS_PLAYING:
            hold = False
            break
        time.sleep(0.2)
    loaded = status == STATUS_PAUSED and isinstance(total, int) and total > 0 and hold
    return loaded


def main():
    if not os.path.isfile(MEDIA):
        print('FAIL missing media', MEDIA)
        return 1
    if not os.path.isfile(EXE):
        print('FAIL missing pot', EXE)
        return 1
    print('EXE', EXE)
    print('MEDIA', MEDIA, os.path.getsize(MEDIA))
    add_loads = test_add_only()
    print('add_only LOADS_MEDIA =', add_loads)
    cur_loads = test_current_immediate_pause()
    print('current_immediate_pause LOADS_MEDIA =', cur_loads)
    kill_pot()
    if add_loads:
        print('NOTE: /add unexpectedly loaded media on this build')
    if not cur_loads:
        print('FAIL current+pause did not load')
        return 1
    print('STRATEGY_OK use /current + immediate pause')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
