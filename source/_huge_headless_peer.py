"""Headless Huge peer process for multi-client lobby tests.

Usage:
  set HUGE_FAKE_POT=1
  set HUGE_PROFILE_DIR=...\\peer_host
  py -3 _huge_headless_peer.py --ipc-port 39001 --name Host
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ['HUGE_FAKE_POT'] = '1'

import engine.session as session  # noqa: E402
from engine.fake_pot import get_fake, install_fake_pot  # noqa: E402
from engine.session import (  # noqa: E402
    PHASE_PLAYING,
    PHASE_READY,
    PHASE_STARTING,
    STATUS_PAUSED,
    STATUS_PLAYING,
    HugeEngine,
)

install_fake_pot(session)


class PeerServer:
    def __init__(self, engine: HugeEngine, ipc_port: int, name: str):
        self.engine = engine
        self.ipc_port = ipc_port
        self.name = name
        self._sock = None
        self._closed = False

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', self.ipc_port))
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client_loop, args=(conn,), daemon=True).start()

    def _client_loop(self, conn):
        try:
            reader = conn.makefile('r', encoding='utf-8')
            writer = conn.makefile('w', encoding='utf-8')
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except Exception:
                    self._reply(writer, {'ok': False, 'error': 'bad_json'})
                    continue
                try:
                    resp = self.handle(req)
                except Exception as exc:
                    resp = {'ok': False, 'error': str(exc), 'trace': traceback.format_exc()}
                self._reply(writer, resp)
                if req.get('cmd') == 'shutdown':
                    self._closed = True
                    break
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _reply(self, writer, obj):
        writer.write(json.dumps(obj, separators=(',', ':')) + '\n')
        writer.flush()

    def snapshot(self):
        eng = self.engine
        fake = get_fake()
        return {
            'name': self.name,
            'role': eng.role,
            'user_id': eng.user_id,
            'started': bool(eng.started),
            'phase': eng._playback_phase,
            'movie_loaded': bool(eng.movie_loaded),
            'ready': bool((eng.users.get(eng.user_id) or {}).get('ready')),
            'users': [
                {
                    'id': u.get('id'),
                    'name': u.get('name'),
                    'ready': bool(u.get('ready')),
                    'movie_loaded': bool(u.get('movie_loaded')),
                }
                for u in eng.users.values()
            ],
            'pot_status': fake.status(),
            'pot_position': fake.current(),
            'pot_total': fake.total(),
            'playing': fake.status() == STATUS_PLAYING,
            'lobby_seq': eng._bus.lobby_seq,
            'playback_seq': eng._playback_seq,
            'avatars': {
                str(u.get('id')): {
                    'name': u.get('name'),
                    'len': len(str(u.get('avatar') or '')),
                    'ok': bool(eng._sanitize_remote_avatar(u.get('avatar'))),
                }
                for u in eng.users.values()
            },
            't': time.time(),
        }

    def handle(self, req):
        cmd = req.get('cmd')
        eng = self.engine
        t0 = time.time()

        if cmd == 'ping':
            return {'ok': True, 't': t0, 'state': self.snapshot()}

        if cmd == 'host':
            eng.username = str(req.get('name') or self.name)
            eng._name_draft = eng.username
            eng.potplayer_path = r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe'
            state = eng.create_room(str(req.get('lobby') or 'AutoTest'))
            # Wait until server.running
            deadline = time.time() + 5
            while time.time() < deadline and not eng.server.running:
                time.sleep(0.05)
            err = ''
            if isinstance(state, dict):
                err = str(state.get('last_error') or '')
            return {
                'ok': bool(eng.server.running and eng.role == 'host'),
                'address': f'127.0.0.1:{session.PORT}',
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
                'error': err,
            }

        if cmd == 'join':
            eng.username = str(req.get('name') or self.name)
            eng._name_draft = eng.username
            eng.potplayer_path = r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe'
            address = str(req.get('address') or f'127.0.0.1:{session.PORT}')
            eng.join_room(address)
            deadline = time.time() + 12
            while time.time() < deadline:
                # room_state sets role=client; assigned sets user_id.
                if eng.role == 'client' and eng.client.running:
                    break
                time.sleep(0.05)
            # Give membership a moment to arrive.
            deadline2 = time.time() + 5
            while time.time() < deadline2 and eng.role == 'client' and len(eng.users) < 1:
                time.sleep(0.05)
            ok = eng.role == 'client' and eng.client.running
            return {
                'ok': bool(ok),
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
                'error': '' if ok else f'role={eng.role} client_running={eng.client.running} users={len(eng.users)}',
            }

        if cmd == 'load':
            path = str(req.get('path') or os.path.join(ROOT, 'test_movie.mp4'))
            duration = int(req.get('duration_ms') or 3_600_000)
            # Touch a placeholder file so basename works.
            try:
                if not os.path.isfile(path):
                    with open(path, 'wb') as fh:
                        fh.write(b'FAKE')
            except Exception:
                path = 'test_movie.mp4'
            eng.test_inject_loaded_movie(path, duration_ms=duration)
            deadline = time.time() + 3
            while time.time() < deadline and not eng.movie_loaded:
                time.sleep(0.05)
            return {
                'ok': bool(eng.movie_loaded),
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'ready':
            # Ensure ready True
            me = eng.users.get(eng.user_id) or {}
            if not me.get('ready'):
                eng.toggle_ready()
            deadline = time.time() + 3
            while time.time() < deadline:
                me = eng.users.get(eng.user_id) or {}
                if me.get('ready'):
                    break
                time.sleep(0.05)
            return {
                'ok': bool((eng.users.get(eng.user_id) or {}).get('ready')),
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'start':
            if eng.role != 'host':
                return {'ok': False, 'error': 'not_host', 'state': self.snapshot()}
            eng.force_start()
            deadline = time.time() + 10
            while time.time() < deadline:
                if eng.started and eng._playback_phase == PHASE_PLAYING and get_fake().status() == STATUS_PLAYING:
                    break
                time.sleep(0.05)
            fake = get_fake()
            return {
                'ok': bool(eng.started and fake.status() == STATUS_PLAYING and fake.current() <= 2500),
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
                't_play': time.time(),
            }

        if cmd == 'play':
            # Explicit intent on UI queue (ordered with guest/monitor commands).
            if eng.started and eng._playback_phase != PHASE_STARTING:
                eng.ui_call(lambda: (
                    eng._apply_play_state(True, origin='huge'),
                    eng._publish_play_state(
                        True, eng.username, source_client=None, log=True, origin='huge',
                    ),
                ))
            deadline = time.time() + 3
            while time.time() < deadline and get_fake().status() != STATUS_PLAYING:
                time.sleep(0.02)
            return {
                'ok': get_fake().status() == STATUS_PLAYING,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'pause':
            if eng.started and eng._playback_phase != PHASE_STARTING:
                eng.ui_call(lambda: (
                    eng._apply_play_state(False, origin='huge'),
                    eng._publish_play_state(
                        False, eng.username, source_client=None, log=True, origin='huge',
                    ),
                ))
            deadline = time.time() + 3
            while time.time() < deadline and get_fake().status() == STATUS_PLAYING:
                time.sleep(0.02)
            return {
                'ok': get_fake().status() != STATUS_PLAYING,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'pot_pause':
            # Direct PotPlayer Mini64-style pause (no Huge button / publish).
            # Monitor must detect and sync — used to catch auto-resume bugs.
            get_fake().pause()
            deadline = time.time() + 4
            while time.time() < deadline and get_fake().status() == STATUS_PLAYING:
                time.sleep(0.05)
            # Allow one monitor tick to propose/relay.
            time.sleep(0.45)
            return {
                'ok': get_fake().status() != STATUS_PLAYING,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'pot_play':
            get_fake().play()
            deadline = time.time() + 4
            while time.time() < deadline and get_fake().status() != STATUS_PLAYING:
                time.sleep(0.05)
            time.sleep(0.45)
            return {
                'ok': get_fake().status() == STATUS_PLAYING,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'toggle':
            eng.local_action('toggle_pause')
            time.sleep(0.05)
            return {
                'ok': True,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'inject_command':
            action = req.get('action') or {}
            eng.ui_call(lambda: eng.apply_authoritative_action(dict(action), actor='inject'))
            time.sleep(0.05)
            return {
                'ok': True,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'set_avatar_b64':
            # Test helper: set a (possibly oversized) avatar and rebuild lobby thumb.
            eng.avatar_b64 = str(req.get('avatar_b64') or '')
            eng._avatar_lobby_b64 = ''
            wire = eng._avatar_for_wire()
            if eng.role == 'host' and eng.user_id in eng.users:
                eng.users[eng.user_id]['avatar'] = wire
                eng.broadcast_users()
            elif eng.role == 'client' and eng.client and eng.client.running:
                eng.client.send({'type': 'avatar', 'avatar': wire})
                time.sleep(0.2)
            return {
                'ok': bool(wire) or not eng.avatar_b64,
                'wire_len': len(wire or ''),
                'wire_ok': bool(eng._sanitize_remote_avatar(wire)),
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'skip':
            delta = int(req.get('delta') or 5000)
            eng.local_action(f'seek_{delta}')
            # Allow queue to apply
            time.sleep(0.15)
            return {
                'ok': True,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'seek':
            pos = int(req.get('position') or 0)
            # Simulate PotPlayer timeline scrub: set local pot then let monitor detect,
            # OR apply via network path. For direct scrub detection use fake set + wait.
            mode = str(req.get('mode') or 'detect')
            if mode == 'detect':
                get_fake().set_current(pos)
                # Force last_pot far so monitor sees intentional jump on next ticks.
                eng._last_pot_pos = max(0, pos - 15000) if pos > 0 else 15000
                eng._last_pos_sample_time = time.time() - 0.05
                eng._expected_position = -1
                eng._suppress_detection_until = 0
                eng._programmatic_position_until = 0
                time.sleep(0.35)
            else:
                # Authoritative remote-style apply locally
                session.pot_set_current(pos)
                eng._remember_programmatic_seek(pos)
            return {
                'ok': True,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'wait':
            want_playing = req.get('playing')
            max_pos = req.get('max_position')
            min_pos = req.get('min_position')
            timeout = float(req.get('timeout') or 5)
            deadline = time.time() + timeout
            ok = False
            while time.time() < deadline:
                st = self.snapshot()
                good = True
                if want_playing is not None and st['playing'] != bool(want_playing):
                    good = False
                if max_pos is not None and st['pot_position'] > int(max_pos):
                    good = False
                if min_pos is not None and st['pot_position'] < int(min_pos):
                    good = False
                if good:
                    ok = True
                    break
                time.sleep(0.05)
            return {
                'ok': ok,
                'elapsed_ms': (time.time() - t0) * 1000,
                'state': self.snapshot(),
            }

        if cmd == 'state':
            return {'ok': True, 'state': self.snapshot()}

        if cmd == 'shutdown':
            try:
                eng.leave_lobby()
                eng.close()
            except Exception:
                pass
            return {'ok': True}

        return {'ok': False, 'error': f'unknown_cmd:{cmd}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ipc-port', type=int, required=True)
    ap.add_argument('--name', default='Peer')
    ap.add_argument('--profile-dir', default='')
    args = ap.parse_args()
    if args.profile_dir:
        os.environ['HUGE_PROFILE_DIR'] = args.profile_dir
        os.makedirs(args.profile_dir, exist_ok=True)

    eng = HugeEngine()
    eng.username = args.name
    eng._name_draft = args.name
    peer = PeerServer(eng, args.ipc_port, args.name)
    peer.start()
    print(json.dumps({'ready': True, 'ipc_port': args.ipc_port, 'name': args.name}), flush=True)
    try:
        while not peer._closed:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    try:
        eng.close()
    except Exception:
        pass


if __name__ == '__main__':
    main()
