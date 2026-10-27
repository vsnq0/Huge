"""Host play/pause must hit the socket before live-log and member UI updates.

The v2.0.33 two-PC host trace spent ~100 ms between local_pp_intent and
publish_play. That gap was append_log/_push and mark_paused_user, which ran
before server.broadcast of set_playing. The client propose path does neither.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'

from engine.fake_pot import install_fake_pot, get_fake  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine, STATUS_PLAYING, STATUS_PAUSED  # noqa: E402


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


class _FakeServer:
    def __init__(self):
        self.running = True
        self.sent = []

    def broadcast(self, msg, exclude=None):
        self.sent.append(msg)

    def find(self, *_a, **_k):
        return None


def _host():
    eng = HugeEngine()
    eng.closed = True  # stop monitor/clock threads from racing this test
    eng.role = 'host'
    eng.started = True
    eng._playback_phase = session.PHASE_PLAYING
    eng._playback_seq = 1
    eng.server = _FakeServer()
    eng.username = 'vsnq'
    eng.users = {
        'host': {'id': 'host', 'name': 'vsnq', 'host': True, 'ready': True, 'paused': False},
    }
    fake = get_fake()
    fake.load(duration_ms=60_000)
    fake.set_current(5000)
    fake.play()
    eng._commit_play_intent(True)
    eng._last_pot_state = STATUS_PLAYING
    eng._cached_status = STATUS_PLAYING
    eng._echo_play_expect = None
    eng._echo_play_until = 0.0
    eng._play_apply_pending = False
    return eng


def _kinds(eng):
    out = []
    for msg in eng.server.sent:
        if msg.get('type') == 'command':
            out.append((msg.get('action') or {}).get('action'))
        else:
            out.append(msg.get('type'))
    return out


def test_command_precedes_announcements_even_if_ui_is_slow():
    eng = _host()
    pushes = []

    def slow_push():
        pushes.append(time.monotonic())
        time.sleep(0.06)

    eng._push = slow_push
    eng._local_play_pause(origin='huge')
    kinds = _kinds(eng)
    command_at = None
    for index, msg in enumerate(eng.server.sent):
        action = (msg.get('action') or {}) if msg.get('type') == 'command' else {}
        if action.get('action') == 'set_playing':
            command_at = index
            break
    plays = [m for m in eng.server.sent if (m.get('action') or {}).get('action') == 'set_playing']
    later = [k for k in kinds[command_at + 1:] if k in ('log', 'users', 'room_state')]
    return ok(
        'set_playing is sent once, before log/users/room_state',
        command_at == 0 and len(plays) == 1 and plays[0]['action']['playing'] is False
        and later == ['log', 'users', 'room_state'] and len(pushes) >= 1,
        str(kinds),
    )


def test_slow_ui_is_not_inside_publish():
    eng = _host()

    def slow_push():
        time.sleep(0.08)

    eng._push = slow_push
    t0 = time.monotonic()
    eng._local_play_pause(origin='huge')
    total_ms = (time.monotonic() - t0) * 1000
    done = [r for r in eng._playback_trace if r.get('reason') == 'local_pp_done'][-1]
    published = [r for r in eng._playback_trace if r.get('reason') == 'publish_play'][-1]
    return ok(
        'dt_publish stays small while the UI push is slow',
        done['dt_publish_ms'] < 30 and published['dt_send_ms'] < 30 and total_ms >= 80,
        f"dt_publish_ms={done['dt_publish_ms']} dt_send_ms={published['dt_send_ms']} total_ms={total_ms:.1f}",
    )


def test_rapid_toggles_keep_order():
    eng = _host()
    eng._local_play_pause(origin='huge')
    eng._local_play_pause(origin='huge')
    plays = [
        (m.get('action') or {})
        for m in eng.server.sent
        if (m.get('action') or {}).get('action') == 'set_playing'
    ]
    seqs = [p['play_seq'] for p in plays]
    states = [p['playing'] for p in plays]
    return ok(
        'rapid pause then play keep increasing play_seq',
        seqs == [1, 2] and states == [False, True],
        str(list(zip(seqs, states))),
    )


def test_pending_apply_does_not_resync():
    eng = _host()
    eng._echo_play_expect = False
    eng._echo_play_until = time.time() + 2
    eng._echo_play_confirmed = False
    eng._last_pot_state = STATUS_PLAYING
    eng._last_broadcast_playing = False
    eng._playback_start_grace_until = 0
    applied = []
    eng._apply_play_state = lambda *a, **k: applied.append(a) or True  # type: ignore
    eng._play_apply_pending = True
    eng.handle_local_state_transition(True, False)
    pending_applied = len(applied)
    eng._play_apply_pending = False
    eng._echo_play_confirmed = False
    eng._last_pot_state = STATUS_PLAYING
    eng.handle_local_state_transition(True, False)
    return ok(
        'stale sample during local apply does not resync; a later one can',
        pending_applied == 0 and len(applied) == 1,
        f'pending={pending_applied} later={len(applied)}',
    )


def main():
    results = [
        test_command_precedes_announcements_even_if_ui_is_slow(),
        test_slow_ui_is_not_inside_publish(),
        test_rapid_toggles_keep_order(),
        test_pending_apply_does_not_resync(),
    ]
    print('FAILS', results.count(False))
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
