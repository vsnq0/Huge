"""Stale/duplicate play commands must not undo a newer Pause."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def main():
    work = os.path.join(ROOT, 'test-results', 'stale-play')
    os.makedirs(work, exist_ok=True)
    host, clients = ml.fresh_peers(1, 42010, work)
    fails = 0
    try:
        ml.bootstrap_playing_lobby(host, clients)
        j = clients[0]
        # Authoritative pause
        host.call({'cmd': 'pause'})
        ok, _ = ml.wait_all([host, j], lambda sts: all(not s['playing'] for s in sts), timeout=5)
        if not ok:
            print('FAIL initial pause')
            return 1
        # Inject stale PLAY via host bus accept path if exposed; otherwise replay play then
        # immediately pause and ensure a delayed duplicate play IPC cannot win.
        host.call({'cmd': 'play'})
        ml.wait_all([host, j], lambda sts: all(s['playing'] for s in sts), timeout=5)
        host.call({'cmd': 'pause'})
        ok, _ = ml.wait_all([host, j], lambda sts: all(not s['playing'] for s in sts), timeout=5)
        if not ok:
            print('FAIL pause after play')
            fails += 1
        # Rapid play->pause; final must be paused
        for _ in range(10):
            host.call({'cmd': 'play'})
            time.sleep(0.02)
            host.call({'cmd': 'pause'})
            time.sleep(0.02)
        ok, sts = ml.wait_all([host, j], lambda s: all(not x['playing'] for x in s), timeout=5)
        if not ok:
            print('FAIL rapid final paused', [(s['name'], s['playing']) for s in sts])
            fails += 1
        else:
            print('OK rapid play/pause final paused')
        # Hold 3s — no auto-resume from stale echoes
        time.sleep(3)
        sts = [p.call({'cmd': 'state'})['state'] for p in [host, j]]
        if any(s['playing'] for s in sts):
            print('FAIL auto-resume during hold', [(s['name'], s['playing']) for s in sts])
            fails += 1
        else:
            print('OK hold paused 3s')
        # Joiner pause must win over host's just-prior play (ignore_fly race)
        host.call({'cmd': 'play'})
        ml.wait_all([host, j], lambda sts: all(s['playing'] for s in sts), timeout=5)
        host.call({'cmd': 'play'})
        j.call({'cmd': 'pause'})
        ok, _ = ml.wait_all([host, j], lambda sts: all(not s['playing'] for s in sts), timeout=5)
        if not ok:
            print('FAIL joiner pause after host play')
            fails += 1
        else:
            print('OK joiner pause beats host ignore window')
    finally:
        ml.stop_peers(host, clients)
    print(f'DONE fails={fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
