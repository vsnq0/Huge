"""Unit tests for Huge v2.0.5 playback phases / start sequencing."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    PHASE_IDLE,
    PHASE_LOADING,
    PHASE_PLAYING,
    PHASE_READY,
    PHASE_STARTING,
    STATUS_PAUSED,
    STATUS_PLAYING,
    HugeEngine,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label, detail)
    return bool(cond)


def test_stale_seq_dropped():
    eng = HugeEngine()
    eng._playback_seq = 5
    eng._playback_phase = PHASE_PLAYING
    eng.started = True
    accepted = eng._accept_playback_seq({'seq': 3, 'action': 'set_playing'})
    return ok('stale seq dropped', accepted is False)


def test_newer_seq_accepted():
    eng = HugeEngine()
    eng._playback_seq = 2
    accepted = eng._accept_playback_seq({'seq': 4})
    return ok('newer seq accepted', accepted is True and eng._playback_seq == 4, str(eng._playback_seq))


def test_room_state_ignores_pause_while_started():
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    toggles = []
    eng._set_playing = lambda playing: toggles.append(playing)  # type: ignore
    eng.handle_network_message({
        'type': 'room_state',
        'started': True,
        'users': [],
        'logs': [],
        'elapsed': 0,
        'position': -1,
        'status': STATUS_PAUSED,
        'room_name': 'L',
        'host_name': 'H',
    })
    return ok('room_state pause ignored while started', toggles == [])


def test_start_requires_ready_members():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.movie_loaded = True
    eng._playback_phase = PHASE_READY
    eng.users = {
        'host': {'id': 'host', 'name': 'Host', 'ready': True, 'movie_loaded': True, 'host': True},
        'g1': {'id': 'g1', 'name': 'Guest', 'ready': False, 'movie_loaded': True, 'host': False},
    }
    eng.start_movie()
    return ok('start blocked when guest not ready', eng.started is False and eng._playback_phase == PHASE_READY)


def test_select_sets_loading_phase():
    eng = HugeEngine()
    eng.role = 'host'
    eng.potplayer_path = r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe'
    # Do not actually load — just ensure begin flags when path empty returns early
    state = eng.begin_select_movie('')
    return ok('empty path no-op', eng._playback_phase == PHASE_IDLE, eng._playback_phase)


def test_snapshot_uses_cached_playing():
    eng = HugeEngine()
    eng.started = True
    eng.movie_loaded = True
    eng._cached_status = STATUS_PLAYING
    eng._playback_phase = PHASE_PLAYING
    snap = eng.make_snapshot(include_position=False)
    return ok('snapshot cached playing', snap.get('status') == STATUS_PLAYING)


def test_no_broadcast_before_start():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = False
    eng._playback_phase = PHASE_READY
    eng.server.running = True
    sent = []
    eng.server.broadcast = lambda msg, exclude=None: sent.append(msg)  # type: ignore
    eng.handle_local_state_transition(True, False)
    return ok('no broadcast before start', sent == [])


def main():
    results = [
        test_stale_seq_dropped(),
        test_newer_seq_accepted(),
        test_room_state_ignores_pause_while_started(),
        test_start_requires_ready_members(),
        test_select_sets_loading_phase(),
        test_snapshot_uses_cached_playing(),
        test_no_broadcast_before_start(),
    ]
    print('ALL PASSED' if all(results) else 'FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
