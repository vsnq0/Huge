"""Unit tests for Huge v2.0.7 play/pause ordering and START MOVIE zero-reset."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
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


def test_stale_play_seq_dropped():
    eng = HugeEngine()
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng._playback_seq = 1
    eng._last_applied_play_seq = 5
    accepted = eng._accept_play_cmd({'play_seq': 3, 'seq': 1, 'playing': False})
    return ok('stale play_seq dropped', accepted is False)


def test_newer_play_seq_wins():
    eng = HugeEngine()
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng._playback_seq = 1
    eng._last_applied_play_seq = 5
    accepted = eng._accept_play_cmd({'play_seq': 6, 'seq': 1, 'playing': True, 'sent_at': time.time()})
    return ok('newer play_seq accepted', accepted is True and eng._last_applied_play_seq == 6)


def test_echo_expect_blocks_same_not_opposite():
    eng = HugeEngine()
    eng._mark_remote_play_echo(False)  # just applied pause
    # Same state = echo
    same = (
        time.time() < eng._echo_play_until
        and eng._echo_play_expect is False
    )
    # Opposite (play) must not be treated as echo by callers
    opposite_is_echo = eng._echo_play_expect is True
    return ok('echo guard is state-specific', same and not opposite_is_echo)


def test_rapid_resume_not_swallowed():
    """Simulate pause then play during suppress — play must still publish."""
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'Host'
    published = []

    eng._publish_play_state = (  # type: ignore
        lambda playing, actor, source_client=None, log=True, origin='local':
            published.append(bool(playing))
    )
    eng._last_pot_state = STATUS_PLAYING
    # Pause publishes and sets echo expect False
    eng.handle_local_state_transition(False, True)
    eng._mark_remote_play_echo(False)
    # Rapid resume while suppress active — opposite of echo expect
    eng._last_pot_state = STATUS_PAUSED
    eng.handle_local_state_transition(True, False)
    return ok(
        'rapid resume publishes after pause',
        published == [False, True],
        str(published),
    )


def test_host_excludes_guest_on_relay():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'Host'
    sent = []

    def fake_broadcast(msg, exclude=None):
        sent.append({'msg': msg, 'exclude': exclude})

    eng.server.broadcast = fake_broadcast  # type: ignore
    eng.broadcast_state = lambda include_position=True: None  # type: ignore
    guest = {'id': 'g1', 'name': 'fawaz'}
    eng._publish_play_state(True, 'fawaz', source_client=guest, log=False, origin='guest')
    return ok(
        'relay excludes originating guest',
        bool(sent) and sent[0]['exclude'] is guest,
        str(sent),
    )


def test_verify_at_zero_helper_exists():
    eng = HugeEngine()
    return ok('verify_at_zero exists', callable(getattr(eng, '_verify_at_zero', None)))


def test_start_blocks_when_not_ready():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.movie_loaded = True
    eng._playback_phase = PHASE_READY
    eng.users = {
        'host': {'id': 'host', 'name': 'H', 'ready': True, 'movie_loaded': True, 'host': True},
        'g1': {'id': 'g1', 'name': 'G', 'ready': False, 'movie_loaded': True, 'host': False},
    }
    eng.start_movie()
    return ok('start blocked until ready', eng.started is False)


def test_start_ack_recorded():
    eng = HugeEngine()
    eng.role = 'host'
    client = {'id': 'g1', 'name': 'fawaz'}
    eng.on_start_ack(client, {
        'seq': 2,
        'ok': True,
        'position': 0,
        'status': STATUS_PLAYING,
    })
    ack = eng._start_acks.get('g1') or {}
    return ok('start_ack stored', ack.get('seq') == 2 and ack.get('ok') is True, str(ack))


def test_wait_start_acks_empty_ok():
    eng = HugeEngine()
    eng.role = 'host'
    eng.users = {'host': {'id': 'host', 'name': 'H', 'host': True, 'ready': True, 'movie_loaded': True}}
    eng._playback_seq = 1
    ok_barrier, missing = eng._wait_start_acks(1, timeout=0.2)
    return ok('solo host barrier passes', ok_barrier and missing == [])


def test_skip_blocked_during_starting():
    eng = HugeEngine()
    eng.started = True
    eng._playback_phase = PHASE_STARTING
    eng._enqueue_user_skip(5000)
    return ok('skips blocked during STARTING', eng._skip_queue == [])


def test_room_state_position_ignored_while_starting():
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = True
    eng._playback_phase = PHASE_STARTING
    applied = []
    import engine.session as session_mod
    original = session_mod.pot_set_current
    session_mod.pot_set_current = lambda pos: applied.append(int(pos)) or True  # type: ignore
    try:
        eng.handle_network_message({
            'type': 'room_state',
            'started': True,
            'users': [],
            'logs': [],
            'elapsed': 0,
            'position': 99999,
            'status': STATUS_PAUSED,
            'room_name': 'L',
            'host_name': 'H',
        })
    finally:
        session_mod.pot_set_current = original
    return ok('room_state position ignored during STARTING', applied == [], str(applied))


def main():
    results = [
        test_stale_play_seq_dropped(),
        test_newer_play_seq_wins(),
        test_echo_expect_blocks_same_not_opposite(),
        test_rapid_resume_not_swallowed(),
        test_host_excludes_guest_on_relay(),
        test_verify_at_zero_helper_exists(),
        test_start_blocks_when_not_ready(),
        test_start_ack_recorded(),
        test_wait_start_acks_empty_ok(),
        test_skip_blocked_during_starting(),
        test_room_state_position_ignored_while_starting(),
    ]
    failed = sum(1 for r in results if not r)
    print(f'\n{len(results) - failed}/{len(results)} passed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
