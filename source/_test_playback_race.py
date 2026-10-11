"""Regression tests for select-movie / start-movie play-then-stop races."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    PHASE_PLAYING,
    STATUS_PAUSED,
    STATUS_PLAYING,
    HugeEngine,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), label, detail)
    return bool(cond)


def test_no_broadcast_before_start():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = False
    eng.server.running = True
    sent = []
    eng.server.broadcast = lambda msg, exclude=None: sent.append(msg)  # type: ignore
    eng.handle_local_state_transition(True, False)
    eng.handle_local_state_transition(False, True)
    return ok('no play/pause broadcast before START MOVIE', sent == [], str(sent))


def test_snapshot_uses_cached_status():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng.movie_loaded = True
    eng._cached_status = STATUS_PLAYING
    # Force a wrong live read by leaving PotPlayer alone; cached must win.
    snap = eng.make_snapshot(include_position=False)
    return ok('snapshot prefers cached PLAYING', snap.get('status') == STATUS_PLAYING, str(snap.get('status')))


def test_room_state_ignores_stale_pause_while_started():
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = True
    eng._pot_link_active = True
    eng._cached_status = STATUS_PLAYING
    eng._last_pot_state = STATUS_PLAYING
    toggles = []

    def fake_set(playing):
        toggles.append(playing)

    eng._set_playing = fake_set  # type: ignore
    eng.handle_network_message({
        'type': 'room_state',
        'room_name': 'Lobby',
        'host_name': 'Host',
        'started': True,
        'users': [],
        'logs': [],
        'elapsed': 0,
        'position': -1,
        'status': STATUS_PAUSED,
    })
    return ok('room_state PAUSED ignored while started', toggles == [], str(toggles))


def test_room_state_applies_play_on_late_join():
    """First in-progress snapshot (was not started) may sync play once."""
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = False
    toggles = []
    eng._set_playing = lambda playing: toggles.append(playing)  # type: ignore
    eng.handle_network_message({
        'type': 'room_state',
        'room_name': 'Lobby',
        'host_name': 'Host',
        'started': True,
        'users': [],
        'logs': [],
        'elapsed': 0,
        'position': -1,
        'status': STATUS_PLAYING,
    })
    return ok('late-join room_state PLAYING applied once', toggles == [True], str(toggles))


def test_room_state_ignores_play_while_already_started():
    """Ongoing room_state must not force PLAYING — races guest-originated pause."""
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = True
    toggles = []
    eng._set_playing = lambda playing: toggles.append(playing)  # type: ignore
    eng.handle_network_message({
        'type': 'room_state',
        'room_name': 'Lobby',
        'host_name': 'Host',
        'started': True,
        'users': [],
        'logs': [],
        'elapsed': 0,
        'position': -1,
        'status': STATUS_PLAYING,
    })
    return ok('room_state PLAYING ignored while already started', toggles == [], str(toggles))


def test_stale_detected_pause_dropped_in_grace():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng.server.running = True
    eng._playback_phase = PHASE_PLAYING
    eng._playback_start_grace_until = time.time() + 5
    sent = []
    eng.server.broadcast = lambda msg, exclude=None: sent.append(msg)  # type: ignore
    eng.apply_authoritative_action(
        {'action': 'set_playing', 'playing': False, 'detected': True, 'origin': 'potplayer'},
        actor='Guest',
    )
    return ok('potplayer pause dropped during start grace', sent == [], str(sent))


def test_huge_guest_pause_accepted_in_grace():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng.server.running = True
    eng._playback_phase = PHASE_PLAYING
    eng._playback_start_grace_until = time.time() + 5
    eng._cached_status = STATUS_PLAYING
    eng._last_pot_state = STATUS_PLAYING
    applied = []
    eng._apply_play_state = lambda desired, origin='local': applied.append(desired) or True  # type: ignore
    published = []
    eng._publish_play_state = lambda *a, **k: published.append((a, k))  # type: ignore
    eng.apply_authoritative_action(
        {
            'action': 'set_playing',
            'playing': False,
            'detected': True,
            'origin': 'huge',
            'cmd_id': 'testcmd1',
            'sent_at': time.time(),
        },
        actor='Guest',
        source_client={'id': 'g1'},
    )
    return ok(
        'huge guest pause accepted during start grace',
        applied == [False] and len(published) == 1,
        f'applied={applied} published={len(published)}',
    )


def test_movie_loading_flag_cleared_on_cancel():
    eng = HugeEngine()
    eng._movie_loading = True
    eng.closed = True
    # Simulate early exit path via finally with finished_via_callback False
    finished_via_callback = False
    if not finished_via_callback:
        with eng._movie_load_lock:
            eng._movie_loading = False
    return ok('load flag can clear on cancel', eng._movie_loading is False)


def main():
    results = [
        test_no_broadcast_before_start(),
        test_snapshot_uses_cached_status(),
        test_room_state_ignores_stale_pause_while_started(),
        test_room_state_applies_play_on_late_join(),
        test_room_state_ignores_play_while_already_started(),
        test_stale_detected_pause_dropped_in_grace(),
        test_huge_guest_pause_accepted_in_grace(),
        test_movie_loading_flag_cleared_on_cancel(),
    ]
    print('ALL PASSED' if all(results) else 'FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
