"""Host + 3 independent FakePot processes over real localhost TCP.

Not a multi-PC test. Measures command convergence, duplicate-free seeks,
and position spread. Does not claim PotPlayer hardware timing.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from _test_multi_lobby import (  # noqa: E402
    bootstrap_playing_lobby,
    fresh_peers,
    stop_peers,
    wait_all,
)


def positions(peers):
    return [p.call({'cmd': 'state'})['state']['pot_position'] for p in peers]


def playing_flags(peers):
    return [bool(p.call({'cmd': 'state'})['state']['playing']) for p in peers]


def main():
    cycles = int(os.environ.get('HUGE_SYNC_CYCLES', '100'))
    seeks = int(os.environ.get('HUGE_SYNC_SEEKS', '100'))
    idle_s = int(os.environ.get('HUGE_SYNC_IDLE', '0'))
    work = tempfile.mkdtemp(prefix='huge_sync_acc_')
    host, clients = fresh_peers(3, 39340, work)
    peers = [host] + clients
    fails = []
    pp_ms = []
    seek_spread = []
    try:
        bootstrap_playing_lobby(host, clients)
        print(f'BOOTSTRAP ok peers={len(peers)} work={work}')

        for i in range(cycles):
            actor = peers[i % len(peers)]
            want_pause = (i % 2 == 0)
            cmd = 'pause' if want_pause else 'play'
            t0 = time.perf_counter()
            actor.call({'cmd': cmd})
            ok, states = wait_all(
                peers,
                lambda sts, wp=want_pause: all((not s['playing']) if wp else s['playing'] for s in sts),
                timeout=4,
            )
            dt = (time.perf_counter() - t0) * 1000
            pp_ms.append(dt)
            if not ok:
                fails.append(f'pp#{i} {actor.name} {cmd} states={[(s["name"], s["playing"]) for s in states]}')
                break
        print(
            f'PLAYPAUSE cycles={len(pp_ms)} fails={len(fails)} '
            f'avg={sum(pp_ms)/len(pp_ms):.1f}ms max={max(pp_ms):.1f}ms'
            if pp_ms else 'PLAYPAUSE none'
        )

        if not fails:
            # Move everyone well above zero with synced +30s skips so later
            # backward seeks cannot clamp independently at 0.
            for _ in range(12):
                host.call({'cmd': 'skip', 'delta': 30000})
                time.sleep(0.05)
            ok_base, _ = wait_all(
                peers,
                lambda sts: min(s['pot_position'] for s in sts) >= 300000,
                timeout=8,
            )
            if not ok_base:
                fails.append(f'could not reach seek baseline positions={positions(peers)}')
            for i in range(seeks):
                actor = peers[i % len(peers)]
                delta = 5000 if i % 3 else 30000
                if i % 2:
                    delta = -delta
                t0 = time.perf_counter()
                before = positions(peers)
                actor.call({'cmd': 'skip', 'delta': int(delta)})
                target_hint = before[0] + delta
                ok, states = wait_all(
                    peers,
                    lambda sts, hint=target_hint: (
                        max(s['pot_position'] for s in sts) - min(s['pot_position'] for s in sts)
                    ) <= 8000 and all(abs(s['pot_position'] - hint) <= 12000 for s in sts),
                    timeout=4,
                )
                spread = 0
                if states:
                    poss = [s['pot_position'] for s in states]
                    spread = max(poss) - min(poss)
                    seek_spread.append(spread)
                if not ok:
                    fails.append(f'seek#{i} {actor.name} d={delta} spread={spread}')
                    break
            if seek_spread:
                print(
                    f'SEEK n={len(seek_spread)} fails_so_far={len(fails)} '
                    f'spread_avg={sum(seek_spread)/len(seek_spread):.0f}ms '
                    f'spread_max={max(seek_spread)}ms'
                )

        if idle_s and not fails:
            samples = []
            t0 = time.time()
            next_mark = 0
            while time.time() - t0 < idle_s:
                time.sleep(min(5, idle_s - (time.time() - t0)))
                elapsed = time.time() - t0
                if elapsed + 0.2 < next_mark:
                    continue
                poss = positions(peers)
                spread = max(poss) - min(poss)
                samples.append((round(elapsed, 1), spread, poss))
                print(f'IDLE t={elapsed:.0f}s spread={spread}ms pos={poss}')
                next_mark += 30
                if spread > 2000:
                    fails.append(f'idle drift spread {spread}ms at {elapsed:.0f}s')
                    break
            if samples:
                worst = max(s[1] for s in samples)
                print(f'IDLE samples={len(samples)} worst_spread={worst}ms')

        print('FAILS', len(fails))
        for line in fails[:12]:
            print(' ', line)
        return 1 if fails else 0
    finally:
        stop_peers(host, clients)


if __name__ == '__main__':
    raise SystemExit(main())
