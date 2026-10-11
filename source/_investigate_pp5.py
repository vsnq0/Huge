"""Reproduce host+2 play_pause#5 and trace which peer lags."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def run_once(attempt: int) -> dict:
    work = os.path.join(ROOT, 'test-results', 'multi-lobby', f'pp5-debug-{attempt}')
    os.makedirs(work, exist_ok=True)
    host, clients = ml.fresh_peers(2, 39300, work)
    trace = {'attempt': attempt, 'cycles': [], 'fail': None}
    try:
        ml.bootstrap_playing_lobby(host, clients)
        peers = [host] + clients
        for i in range(5):
            tag = f'play_pause#{i+1}'
            actor = peers[i % len(peers)]
            host.call({'cmd': 'play'})
            ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
            t0 = time.time()
            pres = [p.call({'cmd': 'state'})['state'] for p in peers]
            pr = actor.call({'cmd': 'pause'})
            posts = [p.call({'cmd': 'state'})['state'] for p in peers]
            samples = []
            ok_p = False
            while time.time() - t0 < 6.0:
                states = [p.call({'cmd': 'state'})['state'] for p in peers]
                playing = {s['name']: s['playing'] for s in states}
                samples.append({'t_ms': (time.time() - t0) * 1000, 'playing': playing})
                if all(not s['playing'] for s in states):
                    ok_p = True
                    break
                time.sleep(0.05)
            lat = (time.time() - t0) * 1000
            cycle = {
                'tag': tag,
                'actor': actor.name,
                'ok': ok_p,
                'lat_ms': lat,
                'pause_ipc': pr,
                'pre': [{k: s.get(k) for k in ('name', 'playing', 'started', 'phase')} for s in pres],
                'post_ipc': [{k: s.get(k) for k in ('name', 'playing', 'started', 'phase')} for s in posts],
                'samples': samples[-8:],
            }
            trace['cycles'].append(cycle)
            if not ok_p:
                trace['fail'] = cycle
                break
            actor.call({'cmd': 'play'})
            ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
        return trace
    finally:
        ml.stop_peers(host, clients)
        time.sleep(0.2)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    fails = 0
    for i in range(n):
        tr = run_once(i)
        fail = tr.get('fail')
        if fail:
            fails += 1
            print(f'FAIL attempt {i}: {fail["tag"]} from {fail["actor"]} lat={fail["lat_ms"]:.0f}ms')
            print('  pause_ipc:', fail.get('pause_ipc'))
            print('  pre:', fail.get('pre'))
            print('  post_ipc:', fail.get('post_ipc'))
            last = fail['samples'][-1]['playing'] if fail.get('samples') else {}
            print('  still playing:', last)
            for s in fail.get('samples', [])[::2]:
                print(f'    {s["t_ms"]:.0f}ms', s['playing'])
        else:
            print(f'OK attempt {i}')
    print(f'\n{fails}/{n} failed')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
