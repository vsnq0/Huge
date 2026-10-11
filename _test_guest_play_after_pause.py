"""Unit + lobby: joiner Pause then Play must not be dropped by host relay timestamps."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'

from engine.fake_pot import install_fake_pot  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine  # noqa: E402


def test_accept_play_not_poisoned_by_host_relay_clock():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng._playback_phase = session.PHASE_PLAYING
    eng._playback_seq = 1
    # Simulate: accepted guest Pause, then host relay stamped wall time into
    # _last_play_sent_at (the old bug).
    eng._last_play_sent_at = time.time()
    eng._last_guest_play_sent_at = time.time() - 0.05
    play = {
        'action': 'set_playing',
        'playing': True,
        'detected': True,
        'origin': 'huge',
        'sent_at': eng._last_guest_play_sent_at + 0.01,  # after pause, before host relay stamp
        'cmd_id': 'guest-play-1',
    }
    assert eng._accept_play_cmd(play), 'guest Play must accept despite newer host relay wall clock'
    print('OK unit: guest Play accepted after host relay clock stamp')


def test_lobby_pause_play_100():
    import _test_multi_lobby as ml

    work = os.path.join(ROOT, 'test-results', 'guest-pp-100')
    os.makedirs(work, exist_ok=True)
    fails = 0
    latencies = []
    # 50 runs × (pause+play) from joiner = 100 actions; also host+2 subset.
    for n_clients, rounds in ((1, 40), (2, 20)):
        for i in range(rounds):
            host, clients = ml.fresh_peers(n_clients, 48000 + n_clients * 1000 + i * 4, os.path.join(work, f'n{n_clients}_{i}'))
            peers = [host] + clients
            j = clients[0]
            try:
                ml.bootstrap_playing_lobby(host, clients)
                host.call({'cmd': 'play'})
                ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
                gap = [0, 0.02, 0.05, 0.1, 0.2][i % 5]
                t0 = time.time()
                j.call({'cmd': 'pause'})
                ok_p, _ = ml.wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=5)
                time.sleep(gap)
                j.call({'cmd': 'play'})
                ok_r, sts = ml.wait_all(peers, lambda s: all(x['playing'] for x in s), timeout=5)
                latencies.append((time.time() - t0) * 1000)
                if not ok_p or not ok_r:
                    fails += 1
                    print(f'FAIL n={n_clients} i={i} pause={ok_p} play={ok_r} gap={gap} states={[(s["name"], s["playing"]) for s in sts]}')
                elif i % 10 == 0:
                    print(f'OK n={n_clients} i={i} gap={gap}ms lat={latencies[-1]:.0f}')
            finally:
                ml.stop_peers(host, clients)
                time.sleep(0.15)
    if latencies:
        s = sorted(latencies)
        print(
            f'latency pause+play n={len(s)} median={s[len(s)//2]:.0f}ms '
            f'p95={s[int(len(s)*0.95)]:.0f}ms max={s[-1]:.0f}ms'
        )
    print(f'lobby fails={fails}')
    return fails


def main():
    test_accept_play_not_poisoned_by_host_relay_clock()
    fails = test_lobby_pause_play_100()
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
