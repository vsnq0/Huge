"""Reproduce: pause must stay paused (no auto-resume) for host+1 / host+2.

Traces FakePot cmd_log + playback_trace around PotPlayer-origin and Huge pauses.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import _test_multi_lobby as ml  # noqa: E402


def pot_pause_direct(peer):
    """Simulate user pausing PotPlayer Mini64 without going through Huge buttons."""
    return peer.call({'cmd': 'pot_pause'})


def pot_play_direct(peer):
    return peer.call({'cmd': 'pot_play'})


def assert_held_paused(peers, seconds, tag, traces):
    t0 = time.time()
    while time.time() - t0 < seconds:
        states = [p.call({'cmd': 'state'})['state'] for p in peers]
        playing = {s['name']: s['playing'] for s in states}
        if any(playing.values()):
            traces.append({'tag': tag, 'fail_at_ms': (time.time() - t0) * 1000, 'playing': playing})
            return False, playing
        time.sleep(0.1)
    return True, {s['name']: False for s in [p.call({'cmd': 'state'})['state'] for p in peers]}


def _free_lobby_ports(extra_ports=None):
    """Kill anything still holding the shared lobby port or peer IPC ports."""
    ports = [24816]
    if extra_ports:
        ports.extend(int(p) for p in extra_ports)
    port_list = ','.join(str(p) for p in ports)
    try:
        import subprocess as _sp
        _sp.run(
            [
                'powershell', '-NoProfile', '-Command',
                f'Get-NetTCPConnection -LocalPort {port_list} -ErrorAction SilentlyContinue '
                '| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }',
            ],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass
    time.sleep(0.35)


def run_case(n_clients: int, repeats: int) -> dict:
    work = os.path.join(ROOT, 'test-results', 'pause-hold', f'n{n_clients}')
    os.makedirs(work, exist_ok=True)
    result = {'n_clients': n_clients, 'repeats': repeats, 'pass': 0, 'fail': 0, 'traces': []}
    for i in range(repeats):
        # Unique IPC base every iteration (avoid TimeWait / stale peer reuse).
        base_port = 40000 + n_clients * 200 + i * 5
        ipc_ports = [base_port + j for j in range(n_clients + 1)]
        _free_lobby_ports(ipc_ports)
        run_dir = os.path.join(work, f'r{i}_{int(time.time())}')
        host, clients = ml.fresh_peers(n_clients, base_port, run_dir)
        peers = [host] + clients
        try:
            ml.bootstrap_playing_lobby(host, clients)
            actor = peers[i % len(peers)]
            mode = 'huge' if (i % 2 == 0) else 'potplayer'
            # Ensure playing
            host.call({'cmd': 'play'})
            ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)

            t0 = time.time()
            if mode == 'huge':
                actor.call({'cmd': 'pause'})
            else:
                pot_pause_direct(actor)
            ok_sync, _ = ml.wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=5)
            lat = (time.time() - t0) * 1000
            if not ok_sync:
                result['fail'] += 1
                result['traces'].append({
                    'i': i, 'actor': actor.name, 'mode': mode, 'phase': 'sync',
                    'lat_ms': lat,
                    'states': [p.call({'cmd': 'state'})['state'] for p in peers],
                })
                print(f'FAIL sync {mode} from {actor.name} lat={lat:.0f}ms')
                continue

            ok_hold, playing = assert_held_paused(peers, 10.0, f'{mode}:{actor.name}', result['traces'])
            if not ok_hold:
                result['fail'] += 1
                result['traces'].append({
                    'i': i, 'actor': actor.name, 'mode': mode, 'phase': 'hold10',
                    'playing': playing,
                    'traces_tail': result['traces'][-1],
                })
                print(f'FAIL hold10 {mode} from {actor.name} playing={playing}')
                continue

            # Explicit resume
            if mode == 'huge':
                actor.call({'cmd': 'play'})
            else:
                pot_play_direct(actor)
            ok_r, _ = ml.wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
            if not ok_r:
                result['fail'] += 1
                print(f'FAIL resume {mode} from {actor.name}')
                continue

            result['pass'] += 1
            print(f'OK {i+1}/{repeats} {mode} {actor.name} pause_lat={lat:.0f}ms hold10')
        finally:
            ml.stop_peers(host, clients)
            _free_lobby_ports(ipc_ports)
            time.sleep(0.25)
    return result


def main():
    _free_lobby_ports()
    total_pass = total_fail = 0
    for n in (1, 2):
        # 15 each → 30 pause-hold scenarios
        r = run_case(n, 15)
        total_pass += r['pass']
        total_fail += r['fail']
        print(f"\n== host+{n}: {r['pass']}/{r['repeats']} pass, {r['fail']} fail ==")
    print(f'\nTOTAL pause-hold: {total_pass} pass, {total_fail} fail')
    return 0 if total_fail == 0 and total_pass > 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
