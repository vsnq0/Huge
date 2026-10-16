"""Regression tests for Play/Pause sync fix (host log Huge-SyncLog-vsnq-20261016).

Reproduces the failure mode from the real v2.0.20 host trace:
  huge apply/publish playing=X play_seq=N
  potplayer publish playing=X play_seq=N+1   ← must be suppressed

Does not require two physical PCs. Seeking paths are exercised separately.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'

from engine.fake_pot import install_fake_pot, get_fake  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine, STATUS_PLAYING, STATUS_PAUSED  # noqa: E402
from engine.playback import PlaybackCommandBus, KIND_PLAY, KIND_PAUSE, PlaybackCommand  # noqa: E402


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


class _FakeServer:
    def __init__(self):
        self.running = True
        self.sent = []

    def broadcast(self, msg, exclude=None):
        self.sent.append(('broadcast', msg, exclude))

    def find(self, *_a, **_k):
        return None


class _FakeClient:
    def __init__(self):
        self.running = True
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def stop(self):
        self.running = False


def _set_pot(playing: bool, position_ms: int = 5000):
    fake = get_fake()
    fake.load(duration_ms=3_600_000)
    fake.set_current(position_ms)
    if playing:
        fake.play()
    else:
        fake.pause()
    return fake


def _host_playing():
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng._playback_phase = session.PHASE_PLAYING
    eng._playback_seq = 1
    eng.server = _FakeServer()
    eng.client = _FakeClient()
    eng.username = 'vsnq'
    _set_pot(True, 5000)
    eng._commit_play_intent(True)
    eng._last_pot_state = STATUS_PLAYING
    eng._echo_play_expect = None
    eng._echo_play_until = 0.0
    return eng


def test_huge_pause_does_not_echo_potplayer_publish():
    eng = _host_playing()
    before_seq = eng._play_cmd_seq
    # Simulate Huge UI pause (desired explicit False — never toggle blindly).
    eng._apply_play_state(False, origin='huge')
    seq1 = eng._publish_play_state(False, 'vsnq', origin='huge')
    # PotPlayer monitor observes the same paused state.
    eng.handle_local_state_transition(False, True)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed = ok('exactly one set_playing after Huge pause', len(publishes) == 1, f'n={len(publishes)}')
    passed &= ok('play_seq advanced once', seq1 == before_seq + 1, f'seq1={seq1} before={before_seq}')
    passed &= ok('play_cmd_seq not doubled', eng._play_cmd_seq == before_seq + 1, eng._play_cmd_seq)
    # After echo window, matching sample must still not republish.
    eng._echo_play_until = 0.0
    eng.handle_local_state_transition(False, True)
    publishes2 = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed &= ok('no republish after echo expiry', len(publishes2) == 1, f'n={len(publishes2)}')
    return passed


def test_huge_play_does_not_echo_potplayer_publish():
    eng = _host_playing()
    _set_pot(False, 5000)
    eng._commit_play_intent(False)
    eng._last_pot_state = STATUS_PAUSED
    eng._apply_play_state(True, origin='huge')
    eng._publish_play_state(True, 'vsnq', origin='huge')
    eng.handle_local_state_transition(True, False)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    return ok('exactly one set_playing after Huge play', len(publishes) == 1, f'n={len(publishes)}')


def test_genuine_potplayer_toggle_still_publishes():
    eng = _host_playing()
    # Authoritative state is playing; user hits pause in PotPlayer.
    eng.handle_local_state_transition(False, True)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed = ok('genuine PotPlayer pause publishes once', len(publishes) == 1, f'n={len(publishes)}')
    if publishes:
        act = publishes[0]['action']
        passed &= ok('payload desired playing=False', act.get('playing') is False, act)
        passed &= ok('origin potplayer', act.get('origin') == 'potplayer', act.get('origin'))
    return passed


def test_remote_apply_does_not_echo():
    eng = _host_playing()
    eng.apply_authoritative_action(
        {
            'action': 'set_playing',
            'playing': False,
            'remote': True,
            'origin': 'huge',
            'play_seq': 5,
            'lobby_seq': 5,
            'cmd_id': 'remote-pause-1',
            'sent_at': time.time(),
            'seq': 1,
        },
        actor='Abdullah',
    )
    # Local PotPlayer settles to paused — must not create a new host broadcast.
    eng.handle_local_state_transition(False, True)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    return ok('remote pause does not echo potplayer publish', len(publishes) == 0, f'n={len(publishes)}')


def test_stale_play_seq_rejected():
    eng = _host_playing()
    eng._last_applied_play_seq = 10
    ok_accept = eng._accept_play_cmd({
        'action': 'set_playing',
        'playing': False,
        'remote': True,
        'play_seq': 8,
        'lobby_seq': 8,
        'cmd_id': 'stale-1',
        'sent_at': time.time(),
        'seq': 1,
    })
    return ok('stale play_seq rejected', ok_accept is False)


def test_out_of_order_does_not_reverse_latest():
    eng = _host_playing()
    eng._last_applied_play_seq = 0
    # Apply newer pause first.
    eng.apply_authoritative_action(
        {
            'action': 'set_playing', 'playing': False, 'remote': True,
            'play_seq': 2, 'lobby_seq': 2, 'cmd_id': 'new-pause',
            'sent_at': time.time(), 'seq': 1, 'origin': 'huge',
        },
        actor='Abdullah',
    )
    paused_after_new = get_fake().status() == STATUS_PAUSED
    # Older play arrives late — must not resume.
    eng.apply_authoritative_action(
        {
            'action': 'set_playing', 'playing': True, 'remote': True,
            'play_seq': 1, 'lobby_seq': 1, 'cmd_id': 'old-play',
            'sent_at': time.time() - 1, 'seq': 1, 'origin': 'huge',
        },
        actor='Abdullah',
    )
    still_paused = get_fake().status() == STATUS_PAUSED
    return ok(
        'late older play does not reverse pause',
        paused_after_new and still_paused,
        f'after_new={paused_after_new} still={still_paused}',
    )


def test_guest_proposal_one_host_relay():
    eng = _host_playing()
    eng.apply_authoritative_action(
        {
            'action': 'set_playing',
            'playing': False,
            'detected': True,
            'origin': 'huge',
            'cmd_id': 'guest-1',
            'sent_at': time.time(),
            'seq': 1,
        },
        actor='Abdullah',
    )
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed = ok('guest proposal relays once', len(publishes) == 1, f'n={len(publishes)}')
    # Feedback after relay must not add another.
    eng.handle_local_state_transition(False, True)
    publishes2 = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed &= ok('no feedback after guest relay', len(publishes2) == 1, f'n={len(publishes2)}')
    return passed


def test_start_go_prefers_relative_delay():
    eng = _host_playing()
    eng.started = False
    eng._playback_phase = session.PHASE_STARTING
    _set_pot(False, 0)
    t0 = time.monotonic()
    ok_go, err = eng._run_start_go(1, origin='host', go_at=0.0, go_delay_ms=50)
    elapsed = time.monotonic() - t0
    passed = ok('start_go ok with relative delay', ok_go, err)
    passed &= ok('waited roughly go_delay_ms', 0.04 <= elapsed <= 0.35, f'elapsed={elapsed:.3f}')
    # Absolute go_at far in the future must not be ignored when delay_ms absent —
    # but when delay > 1s skew, relative fallback is used. Simulate skew via go_at only.
    eng2 = _host_playing()
    eng2.started = False
    eng2._playback_phase = session.PHASE_STARTING
    _set_pot(False, 0)
    t1 = time.monotonic()
    # Old bug: delay>0.5 caused *zero* wait. Now should still wait a short relative amount.
    ok_go2, err2 = eng2._run_start_go(2, origin='client', go_at=time.time() + 2.0, go_delay_ms=None)
    elapsed2 = time.monotonic() - t1
    passed &= ok('skewed go_at still waits briefly', ok_go2 and elapsed2 >= 0.05, f'elapsed={elapsed2:.3f} err={err2}')
    return passed


def test_rapid_alternating_desired_state():
    eng = _host_playing()
    states = []
    for desired in (False, True, False, True, False):
        eng._apply_play_state(desired, origin='huge')
        eng._publish_play_state(desired, 'vsnq', origin='huge')
        # Matching potplayer sample after each — must not add extras.
        eng.handle_local_state_transition(desired, not desired)
        states.append(get_fake().status() == STATUS_PLAYING)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    expected = [False, True, False, True, False]
    passed = ok('rapid toggles final pot states', states == expected, states)
    passed &= ok('rapid toggles one publish each', len(publishes) == 5, f'n={len(publishes)}')
    return passed


def test_rapid_potplayer_resume_after_pause():
    """Pause then Play in PotPlayer within the echo window must both publish."""
    eng = _host_playing()
    eng.handle_local_state_transition(False, True)
    eng._last_pot_state = STATUS_PAUSED  # PotPlayer confirmed pause
    eng.handle_local_state_transition(True, False)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    states = [bool(m['action'].get('playing')) for m in publishes]
    return ok('rapid potplayer pause+play both publish', states == [False, True], states)


def test_seek_helpers_unchanged():
    """Sanity: classify 5s/30s seeks still recognized (seeking must stay intact)."""
    from engine.playback import classify_seek_delta, looks_like_intentional_seek
    k5, d5 = classify_seek_delta(5000)
    k30, d30 = classify_seek_delta(30000)
    kn, dn = classify_seek_delta(-5000)
    passed = ok('5s skip classified', k5 == 'skip' and d5 == 5000)
    passed &= ok('30s skip classified', k30 == 'skip' and d30 == 30000)
    passed &= ok('-5s skip classified', kn == 'skip' and dn == -5000)
    passed &= ok('looks_like 5s', looks_like_intentional_seek(5000))
    passed &= ok('looks_like 30s', looks_like_intentional_seek(30000))
    return passed


def main():
    tests = [
        test_huge_pause_does_not_echo_potplayer_publish,
        test_huge_play_does_not_echo_potplayer_publish,
        test_genuine_potplayer_toggle_still_publishes,
        test_remote_apply_does_not_echo,
        test_stale_play_seq_rejected,
        test_out_of_order_does_not_reverse_latest,
        test_guest_proposal_one_host_relay,
        test_start_go_prefers_relative_delay,
        test_rapid_alternating_desired_state,
        test_rapid_potplayer_resume_after_pause,
        test_seek_helpers_unchanged,
    ]
    failed = 0
    for fn in tests:
        print(f'\n== {fn.__name__} ==')
        try:
            if not fn():
                failed += 1
        except Exception as exc:
            print('FAIL - exception', exc)
            failed += 1
    print(f'\nRESULT failed={failed}')
    return failed


if __name__ == '__main__':
    raise SystemExit(main())
