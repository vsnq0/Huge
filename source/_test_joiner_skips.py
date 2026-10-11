"""Joiner-initiated ±5/±30 skips must sync to host and peers."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def main():
    work = os.path.join(ROOT, 'test-results', 'joiner-skips')
    os.makedirs(work, exist_ok=True)
    fails = 0
    samples = []
    for n in (1, 2):
        host, clients = ml.fresh_peers(n, 49500 + n * 30, os.path.join(work, f'n{n}'))
        peers = [host] + clients
        j = clients[0]
        try:
            ml.bootstrap_playing_lobby(host, clients)
            host.call({'cmd': 'seek', 'position': 600_000, 'mode': 'detect'})
            ml.wait_all(peers, lambda sts: all(abs(s['pot_position'] - 600_000) <= 25_000 for s in sts), timeout=8)
            pattern = [5000, -5000, 30000, -30000] * 25  # 100 skips
            for i, d in enumerate(pattern):
                actor = j if i % 2 == 0 else (clients[-1] if n > 1 and i % 3 == 0 else j)
                t0 = time.time()
                actor.call({'cmd': 'skip', 'delta': int(d)})
                time.sleep(0.12)
                samples.append((time.time() - t0) * 1000)
            ok, sts = ml.wait_all(
                peers,
                lambda s: (max(x['pot_position'] for x in s) - min(x['pot_position'] for x in s)) <= 15_000,
                timeout=10,
            )
            spread = max(s['pot_position'] for s in sts) - min(s['pot_position'] for s in sts) if sts else -1
            if not ok:
                fails += 1
                print(f'FAIL host+{n} spread={spread} pos={[s["pot_position"] for s in sts]}')
            else:
                print(f'OK host+{n} 100 skips spread={spread}ms')
        finally:
            ml.stop_peers(host, clients)
    if samples:
        s = sorted(samples)
        print(f'skip ipc median={s[len(s)//2]:.0f} p95={s[int(len(s)*0.95)]:.0f} max={s[-1]:.0f}')
    print(f'fails={fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
