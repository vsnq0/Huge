"""Reproduce: Joiner1 Pause then immediate Play — host must resume."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def dump(tag, peers):
    rows = []
    for p in peers:
        st = p.call({'cmd': 'state'})['state']
        rows.append(
            f"{st['name']}: pot_playing={st['playing']} phase={st['phase']} "
            f"lobby_seq={st.get('lobby_seq')} pos={st.get('pot_position')}"
        )
    print(tag, ' | '.join(rows))


def one_shot(interval_ms: float, base_port: int, work: str) -> bool:
    host, clients = ml.fresh_peers(1, base_port, work)
    peers = [host] + clients
    j = clients[0]
    try:
        ml.bootstrap_playing_lobby(host, clients)
        dump('pre', peers)
        t0 = time.time()
        j.call({'cmd': 'pause'})
        ok_p, _ = ml.wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=5)
        t_pause = (time.time() - t0) * 1000
        dump(f'after_pause ok={ok_p} {t_pause:.0f}ms', peers)
        if not ok_p:
            print('FAIL pause sync')
            return False
        time.sleep(interval_ms / 1000.0)
        t1 = time.time()
        j.call({'cmd': 'play'})
        ok_r, sts = ml.wait_all(peers, lambda s: all(x['playing'] for x in s), timeout=5)
        t_play = (time.time() - t1) * 1000
        dump(f'after_play ok={ok_r} {t_play:.0f}ms', peers)
        if not ok_r:
            print('FAIL resume — host stuck paused?', [(s['name'], s['playing']) for s in sts])
            # Extra wait to see if late
            time.sleep(2)
            dump('late+2s', peers)
            return False
        print(f'OK interval={interval_ms}ms pause={t_pause:.0f} play={t_play:.0f}')
        return True
    finally:
        ml.stop_peers(host, clients)


def main():
    work = os.path.join(ROOT, 'test-results', 'joiner-pp')
    os.makedirs(work, exist_ok=True)
    fails = 0
    # Stress the exact user scenario: pause then quick play
    for i, interval in enumerate([0, 20, 50, 100, 200, 500] * 5):
        ok = one_shot(interval, 45000 + i * 3, os.path.join(work, f'r{i}_{interval}'))
        if not ok:
            fails += 1
        time.sleep(0.25)
    print(f'\nDONE fails={fails}/30')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
