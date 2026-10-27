"""Play/Pause stage timings for host + 3 FakePot peers on localhost.

Uses each peer's playback trace (same-PC wall clock). This is not a
separate-physical-PC test. Optional HUGE_SIM_ONE_WAY_MS delays lobby sends.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from _test_multi_lobby import bootstrap_playing_lobby, fresh_peers, stop_peers  # noqa: E402


def _rows(peer, reason):
    trace = peer.call({'cmd': 'trace', 'n': 80}).get('trace') or []
    return [r for r in trace if r.get('reason') == reason]


def _latest(rows, cmd_id=None):
    if cmd_id:
        rows = [r for r in rows if str(r.get('cmd_id') or '') == cmd_id]
    return rows[-1] if rows else None


def _wait_row(peer, reason, pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = [r for r in _rows(peer, reason) if pred(r)]
        if rows:
            return rows[-1]
        time.sleep(0.02)
    return None


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def main():
    rounds = int(os.environ.get('HUGE_STAGE_ROUNDS', '20'))
    work = tempfile.mkdtemp(prefix='huge_stages_')
    host, clients = fresh_peers(3, 39440, work)
    peers = [host] + clients
    by_origin = {p.name: [] for p in peers}
    fails = []
    try:
        bootstrap_playing_lobby(host, clients)
        delay = os.environ.get('HUGE_SIM_ONE_WAY_MS') or '0'
        print(f'PEERS={[p.name for p in peers]} sim_one_way_ms={delay} rounds_per_origin={rounds}')
        for origin in peers:
            for i in range(rounds):
                cmd = 'pause' if i % 2 == 0 else 'play'
                mark = time.time()
                t_ipc = time.time()
                resp = origin.call({'cmd': cmd})
                if not resp.get('ok'):
                    fails.append(f'{origin.name} {cmd} local not applied')
                    continue
                if origin is host:
                    pub = _wait_row(origin, 'publish_play', lambda r: float(r.get('wall_s') or 0) >= mark - 0.05)
                    if not pub:
                        fails.append(f'{origin.name} {cmd} no publish_play')
                        continue
                    propose_wall = float(pub['wall_s'])
                    publish_wall = propose_wall
                    cmd_id = str(pub.get('cmd_id') or '')
                else:
                    prop = _wait_row(origin, 'propose_play', lambda r: float(r.get('wall_s') or 0) >= mark - 0.05)
                    if not prop:
                        fails.append(f'{origin.name} {cmd} no propose_play')
                        continue
                    propose_wall = float(prop['wall_s'])
                    pub = _wait_row(host, 'publish_play', lambda r: float(r.get('wall_s') or 0) >= propose_wall - 0.01)
                    if not pub:
                        fails.append(f'{origin.name} {cmd} host did not publish')
                        continue
                    publish_wall = float(pub['wall_s'])
                    cmd_id = str(pub.get('cmd_id') or '')
                input_to_broadcast = (propose_wall - t_ipc) * 1000
                to_host_publish = (publish_wall - propose_wall) * 1000
                applies = [
                    r for r in _rows(origin, 'apply_play_state')
                    if float(r.get('wall_s') or 0) >= propose_wall - 0.001
                ]
                apply_ms = float(applies[-1].get('dt_ms') or 0) if applies else None
                hops = []
                for other in peers:
                    if other is origin:
                        continue
                    remote = _wait_row(
                        other, 'remote_play_applied',
                        lambda r, cid=cmd_id: str(r.get('cmd_id') or '') == cid,
                    )
                    if not remote:
                        # Host applies a guest proposal locally; it does not log remote_play_applied.
                        if other is host:
                            host_apply = [
                                r for r in _rows(host, 'apply_play_state')
                                if float(r.get('wall_s') or 0) >= publish_wall - 0.001
                            ]
                            if host_apply:
                                hops.append((float(host_apply[-1]['wall_s']) - publish_wall) * 1000)
                                continue
                        fails.append(f'{origin.name}->{other.name} missed {cmd_id}')
                        continue
                    hops.append((float(remote['wall_s']) - publish_wall) * 1000)
                if not hops:
                    continue
                sample = {
                    'cmd': cmd,
                    'input_to_broadcast_ms': input_to_broadcast,
                    'to_host_publish_ms': to_host_publish,
                    'apply_ms': apply_ms,
                    'broadcast_to_peer_apply_ms': hops,
                    'worst_peer_ms': max(hops),
                    'ipc_elapsed_ms': resp.get('elapsed_ms'),
                }
                by_origin[origin.name].append(sample)
        print('--- by origin (trace clocks, not the IPC poll) ---')
        for name, samples in by_origin.items():
            if not samples:
                print(name, 'NO SAMPLES')
                continue
            ib = [s['input_to_broadcast_ms'] for s in samples]
            host_pub = [s['to_host_publish_ms'] for s in samples]
            wp = [s['worst_peer_ms'] for s in samples]
            ap = [s['apply_ms'] for s in samples if s['apply_ms'] is not None]
            ipc = [s['ipc_elapsed_ms'] for s in samples]
            print(
                f'{name} n={len(samples)} '
                f'input->send avg={statistics.mean(ib):.2f} p95={percentile(ib, 95):.2f} max={max(ib):.2f} | '
                f'send->host_publish avg={statistics.mean(host_pub):.2f} p95={percentile(host_pub, 95):.2f} max={max(host_pub):.2f} | '
                f'host_publish->peer_apply avg={statistics.mean(wp):.2f} p95={percentile(wp, 95):.2f} max={max(wp):.2f} | '
                f'local_apply avg={statistics.mean(ap):.2f} max={max(ap):.2f} | '
                f'ipc_poll avg={statistics.mean(ipc):.1f} max={max(ipc):.1f}'
            )
            worst = max(samples, key=lambda s: s['input_to_broadcast_ms'] + s['to_host_publish_ms'] + s['worst_peer_ms'])
            print(
                f'  worst sum cmd={worst["cmd"]} '
                f'input->send={worst["input_to_broadcast_ms"]:.2f} '
                f'send->host_publish={worst["to_host_publish_ms"]:.2f} '
                f'peer_apply={worst["worst_peer_ms"]:.2f} '
                f'local_apply={worst["apply_ms"]} '
                f'hops={["%.2f" % h for h in worst["broadcast_to_peer_apply_ms"]]}'
            )
        # Simultaneous opposite intents: last writer should win on every peer.
        host.call({'cmd': 'play'})
        time.sleep(0.1)
        import threading
        errs = []

        def fire(peer, cmd):
            try:
                peer.call({'cmd': cmd})
            except Exception as exc:
                errs.append(str(exc))

        threads = [
            threading.Thread(target=fire, args=(host, 'pause')),
            threading.Thread(target=fire, args=(clients[0], 'play')),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.4)
        flags = []
        for p in peers:
            flags.append((p.name, bool(p.call({'cmd': 'state'})['state']['playing'])))
        same = len({f for _, f in flags}) == 1
        print('SIMULTANEOUS host pause + joiner1 play ->', flags, 'converged' if same else 'DIVERGED')
        if not same:
            fails.append(f'simultaneous diverged {flags}')
        print('--- seek spread (FakePot position after each origin skip) ---')
        for origin in peers:
            for delta in (5000, -5000):
                before = [int(p.call({'cmd': 'state'})['state']['pot_position']) for p in peers]
                origin.call({'cmd': 'skip', 'delta': delta})
                deadline = time.time() + 4
                spread = None
                positions = before
                while time.time() < deadline:
                    positions = [int(p.call({'cmd': 'state'})['state']['pot_position']) for p in peers]
                    spread = max(positions) - min(positions)
                    if spread <= 80:
                        break
                    time.sleep(0.05)
                print(f'{origin.name} skip {delta:+d} spread_ms={spread} positions={positions}')
                if spread is None or spread > 500:
                    fails.append(f'{origin.name} skip {delta} spread {spread}')
        # Drop joiner 3 with the real leave path, then confirm the others still pause.
        clients[2].call({'cmd': 'disconnect'})
        time.sleep(0.3)
        host.call({'cmd': 'pause'})
        time.sleep(0.4)
        alive = [
            (p.name, bool(p.call({'cmd': 'state'})['state']['playing']))
            for p in (host, clients[0], clients[1])
        ]
        print('AFTER joiner3 leave, host pause ->', alive)
        if any(flag for _, flag in alive):
            fails.append(f'remaining peers not paused {alive}')
        clients[2].call({'cmd': 'join', 'address': '127.0.0.1:24816', 'name': clients[2].name})
        time.sleep(0.6)
        host.call({'cmd': 'pause'})
        time.sleep(0.4)
        after_rejoin = [
            (p.name, bool(p.call({'cmd': 'state'})['state']['playing']),
             int(p.call({'cmd': 'state'})['state']['pot_position']))
            for p in peers
        ]
        print('HOST pause after joiner3 rejoin ->', after_rejoin)
        if any(flag for _, flag, _pos in after_rejoin):
            fails.append(f'rejoin did not receive host pause {after_rejoin}')
        positions = [pos for _, _flag, pos in after_rejoin]
        rejoin_spread = max(positions) - min(positions)
        print(f'REJOIN position spread_ms={rejoin_spread}')
        if rejoin_spread > 1500:
            fails.append(f'rejoin position spread {rejoin_spread} {after_rejoin}')
        print('FAILS', len(fails))
        for line in fails[:15]:
            print(' ', line)
        return 1 if fails else 0
    finally:
        stop_peers(host, clients)


if __name__ == '__main__':
    raise SystemExit(main())
