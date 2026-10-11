"""Integration: HugeEngine Select Movie must load real media, paused at zero."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    PHASE_READY,
    STATUS_PAUSED,
    STATUS_PLAYING,
    HugeEngine,
    find_potplayer_exe,
    pot_current,
    pot_status,
    pot_total,
    pot_window,
)

MEDIA = os.path.join(ROOT, '_test_media', 'sample.mp4')


def kill_pot():
    subprocess.run(['taskkill', '/F', '/IM', 'PotPlayerMini64.exe'], capture_output=True, check=False)
    time.sleep(0.6)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label, detail)
    return bool(cond)


def wait_phase(eng, phase, timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if eng._playback_phase == phase and eng.movie_loaded:
            return True
        if eng._playback_phase == 'idle' and not eng._movie_loading and not eng.movie_loaded:
            # failed
            return False
        time.sleep(0.15)
    return eng._playback_phase == phase and eng.movie_loaded


def main():
    if not os.path.isfile(MEDIA):
        print('FAIL missing', MEDIA)
        return 1
    exe = find_potplayer_exe()
    if not exe:
        print('FAIL no PotPlayer')
        return 1
    print('EXE', exe)
    print('MEDIA', MEDIA)

    kill_pot()
    eng = HugeEngine()
    eng.role = 'host'
    eng.username = 'tester'
    eng.potplayer_path = exe
    eng.users = {'host': {'id': 'host', 'name': 'tester', 'ready': False, 'movie_loaded': False, 'host': True}}

    abs_media = os.path.abspath(MEDIA)
    eng.begin_select_movie(abs_media)
    loaded = wait_phase(eng, PHASE_READY, 50.0)
    status = pot_status()
    total = pot_total()
    pos = pot_current()
    passed = True
    passed &= ok('engine marks movie_loaded', eng.movie_loaded and loaded, f'phase={eng._playback_phase} loaded={eng.movie_loaded}')
    passed &= ok('movie_path absolute', eng.movie_path == abs_media, eng.movie_path)
    passed &= ok('pot window open', bool(pot_window()))
    passed &= ok('status paused', status == STATUS_PAUSED, str(status))
    passed &= ok('duration valid', isinstance(total, int) and total > 0, str(total))
    passed &= ok('position near zero', isinstance(pos, int) and pos <= 400, str(pos))

    # Hold paused ~15s
    hold_ok = True
    t0 = time.time()
    while time.time() - t0 < 15.0:
        if pot_status() == STATUS_PLAYING:
            hold_ok = False
            break
        time.sleep(0.5)
    passed &= ok('stayed paused 15s', hold_ok, f'status={pot_status()}')

    # Select a second time (same file) must still work
    eng.begin_select_movie(abs_media)
    loaded2 = wait_phase(eng, PHASE_READY, 50.0)
    passed &= ok('second select loads', loaded2 and eng.movie_loaded and pot_total() > 0, f'phase={eng._playback_phase} total={pot_total()}')

    # Ready + start from zero (re-assert ready after select resets it)
    time.sleep(0.3)
    eng.users['host']['ready'] = True
    eng.users['host']['movie_loaded'] = True
    eng.movie_loaded = True
    eng._playback_phase = PHASE_READY
    eng.server.running = True
    eng.server.broadcast = lambda *a, **k: None  # type: ignore
    print('pre_start', 'phase', eng._playback_phase, 'ready', eng.all_ready(), 'status', pot_status())
    eng.start_movie()
    time.sleep(1.2)
    passed &= ok('start_movie playing', eng.started and pot_status() == STATUS_PLAYING, f'started={eng.started} status={pot_status()} pos={pot_current()} phase={eng._playback_phase}')
    passed &= ok('start near zero', pot_current() <= 2500, str(pot_current()))

    kill_pot()
    print('ALL PASSED' if passed else 'FAILED')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
