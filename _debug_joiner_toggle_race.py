"""Reproduce joiner toggle Pause→Play and late Pause-relay race."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def states(peers):
    return [(p.name, p.call({'cmd': 'state'})['state']['playing']) for p in peers]


def run_toggle_burst(n=40):
    work = os.path.join(ROOT, 'test-results', 'joiner-toggle')
    os.makedirs(work, exist_ok=True)
    fails = 0
    for i in range(n):
        host, clients = ml.fresh_peers(1, 46000 + i * 3, os.path.join(work, f't{i}'))
        peers = [host] + clients
        j = clients[0]
        try:
            ml.bootstrap_playing_lobby(host, clients)
            # Ensure playing
            host.call({'cmd': 'play'})
            ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
            # Real UI path: toggle_pause twice quickly
            j.call({'cmd': 'toggle'})  # should pause
            time.sleep(0.02)
            j.call({'cmd': 'toggle'})  # should play
            ok, sts = ml.wait_all(peers, lambda s: all(x['playing'] for x in s), timeout=4)
            if not ok:
                fails += 1
                print(f'FAIL toggle-burst {i} final={states(peers)}')
            elif i % 10 == 0:
                print(f'OK {i} {states(peers)}')
        finally:
            ml.stop_peers(host, clients)
            time.sleep(0.2)
    print(f'toggle_burst fails={fails}/{n}')
    return fails


def run_late_pause_relay():
    """Simulate: joiner already playing after Pause→Play while host Pause relay is late."""
    work = os.path.join(ROOT, 'test-results', 'joiner-late-pause')
    os.makedirs(work, exist_ok=True)
    host, clients = ml.fresh_peers(1, 47000, work)
    peers = [host] + clients
    j = clients[0]
    fails = 0
    try:
        ml.bootstrap_playing_lobby(host, clients)
        # Joiner pause then play via Huge
        j.call({'cmd': 'pause'})
        ml.wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=5)
        j.call({'cmd': 'play'})
        ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
        # Inject a stale Pause as if a delayed host relay arrived at joiner
        # (older pause after newer play) — joiner must NOT stick host via re-propose,
        # and host must stay playing.
        eng_state = j.call({'cmd': 'inject_command', 'action': {
            'action': 'set_playing',
            'playing': False,
            'remote': True,
            'origin': 'potplayer',
            'lobby_seq': 1,  # stale
            'play_seq': 1,
            'sent_at': time.time() - 5,
        }})
        print('inject', eng_state)
        time.sleep(0.8)
        sts = states(peers)
        print('after stale pause inject', sts)
        if not all(playing for _, playing in sts):
            fails += 1
            print('FAIL stale pause undid play')
        else:
            print('OK stale pause ignored')
    finally:
        ml.stop_peers(host, clients)
    return fails


def main():
    f = 0
    f += run_toggle_burst(30)
    f += run_late_pause_relay()
    print(f'TOTAL fails={f}')
    return 1 if f else 0


if __name__ == '__main__':
    raise SystemExit(main())
