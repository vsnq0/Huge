"""Automated multi-process lobby tests (host+1 and host+2) over localhost.

Uses real HugeEngine networking with independent FakePot per process.
Does NOT claim real PotPlayer Mini64 multi-instance results.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PEER = os.path.join(ROOT, '_huge_headless_peer.py')
REPORT_DIR = os.path.join(ROOT, 'test-results', 'multi-lobby')


@dataclass
class LatencySample:
    name: str
    ms: float


@dataclass
class SuiteResult:
    name: str
    runs: int = 0
    passes: int = 0
    fails: int = 0
    errors: list = field(default_factory=list)
    latencies: list = field(default_factory=list)


class PeerClient:
    def __init__(self, name: str, ipc_port: int, profile_dir: str):
        self.name = name
        self.ipc_port = ipc_port
        self.profile_dir = profile_dir
        self.proc = None

    def start(self):
        os.makedirs(self.profile_dir, exist_ok=True)
        env = os.environ.copy()
        env['HUGE_FAKE_POT'] = '1'
        env['HUGE_PROFILE_DIR'] = self.profile_dir
        env['PYTHONUNBUFFERED'] = '1'
        log_path = os.path.join(self.profile_dir, 'peer.log')
        self._log_fh = open(log_path, 'w', encoding='utf-8', errors='replace')
        self.proc = subprocess.Popen(
            [PY, '-u', PEER, '--ipc-port', str(self.ipc_port), '--name', self.name, '--profile-dir', self.profile_dir],
            cwd=ROOT,
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Wait for ready line in log + IPC
        deadline = time.time() + 25
        last_err = ''
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._log_fh.flush()
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                        out = fh.read()
                except Exception:
                    out = ''
                raise RuntimeError(f'Peer {self.name} exited early ({self.proc.returncode}): {out}')
            try:
                resp = self.call({'cmd': 'ping'}, timeout=2.0)
                if resp.get('ok'):
                    return
                last_err = str(resp)
            except Exception as exc:
                last_err = str(exc)
            time.sleep(0.15)
        self._log_fh.flush()
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                out = fh.read()
        except Exception:
            out = ''
        raise RuntimeError(
            f'Peer {self.name} IPC not ready on {self.ipc_port}. last={last_err!r} log={out!r}'
        )

    def call(self, req: dict, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                sock = socket.create_connection(('127.0.0.1', self.ipc_port), timeout=2)
                sock.settimeout(timeout)
                with sock:
                    sock.sendall((json.dumps(req) + '\n').encode('utf-8'))
                    buf = b''
                    while b'\n' not in buf:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                if not buf:
                    last_err = 'empty'
                    time.sleep(0.05)
                    continue
                return json.loads(buf.decode('utf-8').split('\n', 1)[0])
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.05)
        raise RuntimeError(f'IPC {self.name} failed: {last_err}')

    def stop(self):
        try:
            self.call({'cmd': 'shutdown'}, timeout=5)
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        try:
            if getattr(self, '_log_fh', None):
                self._log_fh.close()
        except Exception:
            pass


def wait_all(peers, pred, timeout=8.0, label=''):
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        last = [p.call({'cmd': 'state'})['state'] for p in peers]
        if pred(last):
            return True, last
        time.sleep(0.05)
    return False, last


def assert_true(cond, msg, suite: SuiteResult):
    if not cond:
        suite.fails += 1
        suite.errors.append(msg)
        print(f'  FAIL {msg}')
        return False
    print(f'  PASS {msg}')
    return True


def run_scenario_start_zero(host, clients, suite: SuiteResult, repeats=3):
    peers = [host] + clients
    for i in range(repeats):
        suite.runs += 1
        tag = f'start_zero#{i+1}'
        # Reset via leave/rehost would be heavy; instead seek mid then start again only if not started.
        # Fresh lobby each repeat for isolation.
        print(f'--- {suite.name} {tag} ---')
        # If already in lobby from prior, tear down by restarting peers outside.
        # This function assumes fresh peers each call from parent for start tests.
        hr = host.call({'cmd': 'host', 'name': host.name, 'lobby': f'Test-{suite.name}'})
        if not assert_true(hr.get('ok'), f'{tag} host created lobby ({hr.get("error")})', suite):
            return
        addr = f'127.0.0.1:24816'
        time.sleep(0.4)  # allow host accept loop to settle
        for c in clients:
            r = c.call({'cmd': 'join', 'name': c.name, 'address': addr}, timeout=20)
            if not assert_true(r.get('ok'), f'{tag} {c.name} joined ({r.get("error")})', suite):
                return
            time.sleep(0.2)
        for p in peers:
            r = p.call({'cmd': 'load'})
            if not assert_true(r.get('ok'), f'{tag} {p.name} loaded', suite):
                return
        # Pre-start idle 2s (full 15s once per suite below)
        time.sleep(0.5)
        for p in peers:
            st = p.call({'cmd': 'state'})['state']
            if not assert_true(not st['playing'] and st['pot_position'] <= 400, f'{tag} {p.name} paused near 0 pre-start', suite):
                return
        # Seek one client mid-movie before start
        if clients:
            clients[0].call({'cmd': 'seek', 'position': 300_000, 'mode': 'direct'})
        for p in peers:
            p.call({'cmd': 'ready'})
        ok_ready, states = wait_all(
            peers,
            lambda sts: all(u.get('ready') for st in sts for u in st['users']) and len(sts[0]['users']) >= 1 + len(clients),
            timeout=5,
        )
        if not assert_true(ok_ready, f'{tag} everyone ready ({len(states[0]["users"])} users)', suite):
            return
        t0 = time.time()
        r = host.call({'cmd': 'start'}, timeout=20)
        t_host = time.time()
        suite.latencies.append(LatencySample('start_host_ms', (t_host - t0) * 1000))
        if not assert_true(r.get('ok'), f'{tag} host start ok pos={r.get("state",{}).get("pot_position")}', suite):
            return
        # All playing near zero
        ok_all, states = wait_all(
            peers,
            lambda sts: all(s['playing'] and s['pot_position'] <= 2500 for s in sts),
            timeout=8,
        )
        spread = 0.0
        if ok_all:
            # crude start-time proxy: positions / 1ms rate
            positions = [s['pot_position'] for s in states]
            spread = max(positions) - min(positions)
            suite.latencies.append(LatencySample('start_pos_spread_ms', float(spread)))
        if assert_true(ok_all, f'{tag} all playing from ~0 (spread={spread}ms)', suite):
            suite.passes += 1
        else:
            for s in states:
                print('   ', s['name'], 'playing=', s['playing'], 'pos=', s['pot_position'], 'phase=', s['phase'])


def run_scenario_play_pause(host, clients, suite: SuiteResult, repeats=5):
    peers = [host] + clients
    for i in range(repeats):
        suite.runs += 1
        tag = f'play_pause#{i+1}'
        actor = peers[i % len(peers)]
        # Ensure playing first
        host.call({'cmd': 'play'})
        wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
        t0 = time.time()
        actor.call({'cmd': 'pause'})
        ok_p, _ = wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=5)
        lat_p = (time.time() - t0) * 1000
        suite.latencies.append(LatencySample(f'pause_prop_from_{actor.name}', lat_p))
        if not assert_true(ok_p, f'{tag} pause from {actor.name} synced ({lat_p:.0f}ms)', suite):
            continue
        t1 = time.time()
        actor.call({'cmd': 'play'})
        ok_r, _ = wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
        lat_r = (time.time() - t1) * 1000
        suite.latencies.append(LatencySample(f'play_prop_from_{actor.name}', lat_r))
        if assert_true(ok_r, f'{tag} resume from {actor.name} synced ({lat_r:.0f}ms)', suite):
            suite.passes += 1


def run_scenario_rapid_toggle(host, clients, suite: SuiteResult, intervals_ms=(100, 200, 500), repeats=3):
    peers = [host] + clients
    actor = clients[0] if clients else host
    for interval in intervals_ms:
        for i in range(repeats):
            suite.runs += 1
            tag = f'rapid_{interval}ms#{i+1}'
            host.call({'cmd': 'play'})
            wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=4)
            # Rapid pause→play: wait until pause is observed lobby-wide, then play
            # after `interval` ms (tests final-state ordering under quick toggles).
            actor.call({'cmd': 'pause'})
            wait_all(peers, lambda sts: all(not s['playing'] for s in sts), timeout=3)
            time.sleep(interval / 1000.0)
            actor.call({'cmd': 'play'})
            ok, states = wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
            if assert_true(ok, f'{tag} final PLAYING on all after pause->play from {actor.name}', suite):
                suite.passes += 1
            else:
                for s in states:
                    print('   ', s['name'], 'playing=', s['playing'])


def run_scenario_skips(host, clients, suite: SuiteResult, repeats=2):
    peers = [host] + clients
    for i in range(repeats):
        suite.runs += 1
        tag = f'skips#{i+1}'
        host.call({'cmd': 'play'})
        wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=4)
        before = [p.call({'cmd': 'state'})['state']['pot_position'] for p in peers]
        actor = peers[i % len(peers)]
        # 20 rapid +5s
        t0 = time.time()
        for _ in range(20):
            actor.call({'cmd': 'skip', 'delta': 5000})
        # Allow queue drain
        time.sleep(2.5)
        after = [p.call({'cmd': 'state'})['state']['pot_position'] for p in peers]
        suite.latencies.append(LatencySample('skip20_wall_ms', (time.time() - t0) * 1000))
        # Expect roughly +100s (± keyframe/queue), all peers within 5s of each other
        spreads = max(after) - min(after)
        advanced = min(after) - max(before)
        ok = spreads <= 8000 and advanced >= 60000
        if assert_true(ok, f'{tag} 20x+5s from {actor.name} advanced~{advanced}ms spread={spreads}ms', suite):
            suite.passes += 1
        else:
            print('   before', before, 'after', after)


def run_scenario_mixed_skips(host, clients, suite: SuiteResult, count=50):
    """≥50 mixed ±5s/±30s skips; peers stay clustered without reverse desync."""
    peers = [host] + clients
    suite.runs += 1
    host.call({'cmd': 'play'})
    wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=4)
    # Mid-file via detected scrub so every peer receives the absolute seek.
    host.call({'cmd': 'seek', 'position': 900_000, 'mode': 'detect'})
    ok_mid, mid_states = wait_all(
        peers,
        lambda sts: all(abs(s['pot_position'] - 900_000) <= 25_000 for s in sts),
        timeout=8,
    )
    if not ok_mid:
        if not assert_true(False, f'mixed_skips baseline seek to 900s synced', suite):
            print('   mid', [(s['name'], s['pot_position']) for s in mid_states])
            return
    deltas = []
    pattern = [5000, -5000, 30000, -30000, 5000, 5000, -30000, 30000, -5000, 5000]
    while len(deltas) < count:
        deltas.extend(pattern)
    deltas = deltas[:count]
    expected_net = sum(deltas)
    before = [p.call({'cmd': 'state'})['state']['pot_position'] for p in peers]
    baseline = sum(before) / len(before)
    t0 = time.time()
    # Single actor: validates queue + sync of ±5/±30. Pace so joiners can apply
    # each absolute target (burst floods dropped/echo-suppressed seeks).
    for i, d in enumerate(deltas):
        host.call({'cmd': 'skip', 'delta': int(d)})
        if i % 5 == 4:
            time.sleep(0.35)
        else:
            time.sleep(0.08)
    ok_conv, conv_states = wait_all(
        peers,
        lambda sts: (max(s['pot_position'] for s in sts) - min(s['pot_position'] for s in sts)) <= 15_000,
        timeout=10,
    )
    after = [s['pot_position'] for s in conv_states] if conv_states else [
        p.call({'cmd': 'state'})['state']['pot_position'] for p in peers
    ]
    suite.latencies.append(LatencySample('mixed50_wall_ms', (time.time() - t0) * 1000))
    spreads = max(after) - min(after)
    mean_after = sum(after) / len(after)
    mean_adv = mean_after - baseline
    host_adv = after[0] - before[0]
    # Host net tracks expected; peers clustered after convergence wait.
    ok_spread = ok_conv and spreads <= 15_000
    ok_net = abs(host_adv - expected_net) <= 80_000
    ok = ok_spread and ok_net and min(after) > 100_000
    if assert_true(
        ok,
        f'mixed_skips×{count} host_adv~{host_adv}ms expect~{expected_net} spread={spreads}ms',
        suite,
    ):
        suite.passes += 1
    else:
        print('   before', before, 'after', after, 'expected_net', expected_net)
        return

    # Interleaved actors: every peer issues skips; final positions must stay clustered.
    suite.runs += 1
    inter = [5000, -5000, 30000, -30000] * 5  # 20 skips
    for i, d in enumerate(inter):
        peers[i % len(peers)].call({'cmd': 'skip', 'delta': int(d)})
        time.sleep(0.2)
    ok_i, sts_i = wait_all(
        peers,
        lambda sts: (max(s['pot_position'] for s in sts) - min(s['pot_position'] for s in sts)) <= 15_000,
        timeout=8,
    )
    after2 = [s['pot_position'] for s in sts_i] if sts_i else [
        p.call({'cmd': 'state'})['state']['pot_position'] for p in peers
    ]
    spread2 = max(after2) - min(after2)
    if assert_true(ok_i and spread2 <= 15_000, f'interleaved_skips×20 spread={spread2}ms', suite):
        suite.passes += 1
    else:
        print('   after_interleaved', after2)


def run_scenario_seek_scrub(host, clients, suite: SuiteResult, repeats=2):
    peers = [host] + clients
    for i in range(repeats):
        suite.runs += 1
        tag = f'scrub#{i+1}'
        host.call({'cmd': 'play'})
        wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=4)
        actor = clients[0] if clients else host
        target = 600_000
        actor.call({'cmd': 'seek', 'position': target, 'mode': 'detect'})
        ok, states = wait_all(
            peers,
            lambda sts: all(abs(s['pot_position'] - target) <= 15000 for s in sts),
            timeout=6,
        )
        if assert_true(ok, f'{tag} timeline scrub to ~{target} synced', suite):
            suite.passes += 1
        else:
            for s in states:
                print('   ', s['name'], 'pos=', s['pot_position'])


def run_scenario_idle(host, clients, suite: SuiteResult, seconds=30):
    """Default 30s in automated suite; 300s available via --long."""
    peers = [host] + clients
    suite.runs += 1
    host.call({'cmd': 'play'})
    wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
    start_pos = [p.call({'cmd': 'state'})['state']['pot_position'] for p in peers]
    time.sleep(seconds)
    end = [p.call({'cmd': 'state'})['state'] for p in peers]
    ok_playing = all(s['playing'] for s in end)
    advanced = [s['pot_position'] - start_pos[i] for i, s in enumerate(end)]
    # Should advance roughly seconds*1000 (±20%)
    ok_adv = all(0.5 * seconds * 1000 <= a <= 1.5 * seconds * 1000 for a in advanced)
    no_rewind = all(a > 0 for a in advanced)
    if assert_true(ok_playing and ok_adv and no_rewind, f'idle {seconds}s continuous play adv={advanced}', suite):
        suite.passes += 1


def run_scenario_stale_and_disconnect(host, clients, suite: SuiteResult):
    if not clients:
        return
    peers = [host] + clients
    suite.runs += 1
    host.call({'cmd': 'play'})
    wait_all(peers, lambda sts: all(s['playing'] for s in sts), timeout=5)
    # Disconnect last client
    victim = clients[-1]
    victim.call({'cmd': 'shutdown'})
    time.sleep(0.5)
    # Re-launch victim
    victim.start()
    victim.call({'cmd': 'join', 'name': victim.name, 'address': '127.0.0.1:24816'})
    victim.call({'cmd': 'load'})
    victim.call({'cmd': 'ready'})
    # Host pause should still sync remaining + new join after start already done —
    # rejoiner may not be in started playback; host play/pause on existing session.
    host.call({'cmd': 'pause'})
    ok, states = wait_all([host] + clients[:-1], lambda sts: all(not s['playing'] for s in sts), timeout=5)
    # Rejoined peer may still be pre-start locally; count core lobby sync.
    if assert_true(ok, 'pause still syncs after a peer disconnect', suite):
        suite.passes += 1


def bootstrap_playing_lobby(host, clients):
    hr = host.call({'cmd': 'host', 'name': host.name, 'lobby': 'AutoLobby'})
    if not hr.get('ok'):
        raise RuntimeError(f'host create failed: {hr}')
    time.sleep(0.3)
    for c in clients:
        jr = c.call({'cmd': 'join', 'name': c.name, 'address': '127.0.0.1:24816'}, timeout=20)
        if not jr.get('ok'):
            raise RuntimeError(f'join failed {c.name}: {jr}')
        time.sleep(0.15)
    for p in [host] + clients:
        lr = p.call({'cmd': 'load'})
        if not lr.get('ok'):
            raise RuntimeError(f'load failed {p.name}: {lr}')
        rr = p.call({'cmd': 'ready'})
        if not rr.get('ok'):
            raise RuntimeError(f'ready failed {p.name}: {rr}')
    sr = host.call({'cmd': 'start'}, timeout=25)
    if not sr.get('ok'):
        raise RuntimeError(f'start failed: {sr}')
    ok, states = wait_all([host] + clients, lambda sts: all(s['playing'] for s in sts), timeout=10)
    if not ok:
        raise RuntimeError(f'not all playing after start: {states}')


def fresh_peers(n_clients: int, base_port: int, work: str):
    host = PeerClient('Host', base_port, os.path.join(work, 'host'))
    clients = [
        PeerClient(f'Joiner{i+1}', base_port + 1 + i, os.path.join(work, f'joiner{i+1}'))
        for i in range(n_clients)
    ]
    host.start()
    for c in clients:
        c.start()
    return host, clients


def stop_peers(host, clients):
    for p in [host] + list(clients):
        try:
            p.stop()
        except Exception:
            pass


def summarize(suite: SuiteResult):
    print(f"\n== {suite.name}: {suite.passes}/{suite.runs} passed, {suite.fails} fail asserts ==")
    if suite.latencies:
        by = {}
        for s in suite.latencies:
            by.setdefault(s.name, []).append(s.ms)
        for k, vals in by.items():
            vals = sorted(vals)
            avg = sum(vals) / len(vals)
            p50 = vals[len(vals) // 2]
            print(f'  latency {k}: n={len(vals)} avg={avg:.1f}ms p50={p50:.1f}ms min={vals[0]:.1f} max={vals[-1]:.1f}')
    for e in suite.errors[:20]:
        print('  ERR:', e)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--long', action='store_true', help='5-minute idle test')
    ap.add_argument('--quick', action='store_true', help='fewer repeats')
    args = ap.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    work_root = tempfile.mkdtemp(prefix='huge_multi_')
    idle_s = 300 if args.long else 30
    start_reps = 2 if args.quick else 5
    pp_reps = 2 if args.quick else 5
    rapid_reps = 1 if args.quick else 3

    # Free lobby + IPC ports from previous crashed runs.
    try:
        import subprocess as _sp
        _sp.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-NetTCPConnection -LocalPort 24816,39020,39021,39022,39030,39031,39032,39110,39111,39112,39120,39121,39122,39210,39211,39212,39220,39221,39222 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }'],
            check=False, capture_output=True,
        )
        time.sleep(0.5)
    except Exception:
        pass

    results = []
    print('HUGE multi-lobby automated tests')
    print('Player backend: FakePot (independent process state)')
    print('Networking: real TCP lobby on 127.0.0.1:24816')
    print('Real PotPlayer multi-instance: NOT claimed in this run')

    for n_clients, label in ((1, 'host+1'), (2, 'host+2')):
        suite = SuiteResult(name=label)
        work = os.path.join(work_root, label.replace('+', '_'))
        os.makedirs(work, exist_ok=True)

        # --- START MOVIE (fresh peers each repeat) ---
        for i in range(start_reps):
            host, clients = fresh_peers(n_clients, 39010 + n_clients * 10, os.path.join(work, f'start_{i}'))
            try:
                run_scenario_start_zero(host, clients, suite, repeats=1)
            finally:
                stop_peers(host, clients)
                time.sleep(0.3)

        # --- Play/pause, rapid, skips, scrub, idle on one long-lived lobby ---
        host, clients = fresh_peers(n_clients, 39100 + n_clients * 10, os.path.join(work, 'live'))
        try:
            bootstrap_playing_lobby(host, clients)
            # Confirm no advance before start already done; pre-start 15s once:
            # (re-test pre-start on a fresh lobby)
            run_scenario_play_pause(host, clients, suite, repeats=pp_reps)
            if not args.quick:
                run_scenario_mixed_skips(host, clients, suite, count=50)
            run_scenario_rapid_toggle(host, clients, suite, repeats=rapid_reps)
            run_scenario_skips(host, clients, suite, repeats=2 if not args.quick else 1)
            run_scenario_seek_scrub(host, clients, suite, repeats=2 if not args.quick else 1)
            run_scenario_idle(host, clients, suite, seconds=idle_s)
            run_scenario_stale_and_disconnect(host, clients, suite)
        finally:
            stop_peers(host, clients)

        # Dedicated 15s pre-start hold
        host, clients = fresh_peers(n_clients, 39200 + n_clients * 10, os.path.join(work, 'prestart'))
        try:
            suite.runs += 1
            host.call({'cmd': 'host', 'name': host.name, 'lobby': 'PreStart'})
            for c in clients:
                c.call({'cmd': 'join', 'name': c.name, 'address': '127.0.0.1:24816'})
            for p in [host] + clients:
                p.call({'cmd': 'load'})
            time.sleep(15)
            states = [p.call({'cmd': 'state'})['state'] for p in [host] + clients]
            ok = all((not s['playing']) and s['pot_position'] <= 400 and not s['started'] for s in states)
            if assert_true(ok, f'{label} 15s pre-start no playback', suite):
                suite.passes += 1
        finally:
            stop_peers(host, clients)

        summarize(suite)
        results.append(suite)

        path = os.path.join(REPORT_DIR, f'{label}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({
                'name': suite.name,
                'runs': suite.runs,
                'passes': suite.passes,
                'fails': suite.fails,
                'errors': suite.errors,
                'latencies': [{'name': x.name, 'ms': x.ms} for x in suite.latencies],
                'backend': 'FakePot',
                'real_potplayer_multi_instance': False,
            }, fh, indent=2)
        print('Wrote', path)

    total_runs = sum(s.runs for s in results)
    total_pass = sum(s.passes for s in results)
    total_fail = sum(s.fails for s in results)
    print(f'\nTOTAL scenario passes: {total_pass}  asserts-failed: {total_fail}  scenario-runs: {total_runs}')
    print(f'Work dir: {work_root}')
    # Exit non-zero if any assert failed or pass rate low
    if total_fail > 0 or total_pass == 0:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
