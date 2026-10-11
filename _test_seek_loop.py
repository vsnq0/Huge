"""Regression: multiplayer must not treat natural playback / settle as seeks."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.session import (  # noqa: E402
    PHASE_PLAYING,
    SEEK_DETECT_MIN_MS,
    STATUS_PLAYING,
    HugeEngine,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), label, detail)
    return bool(cond)


def test_natural_playback_not_seek():
    eng = HugeEngine()
    # 2.2s busy poll gap while playing ~2200ms forward — classic false seek.
    natural = eng._is_natural_playback_delta(2200, 2.2, True)
    intentional = eng._looks_like_intentional_seek(2200)
    return ok(
        '2.2s playback gap is natural, not intentional',
        natural and not intentional,
        f'natural={natural} intentional={intentional}',
    )


def test_real_5s_skip_is_intentional():
    eng = HugeEngine()
    # Short wall clock + 5s media jump = user skip.
    natural = eng._is_natural_playback_delta(5200, 0.2, True)
    intentional = eng._looks_like_intentional_seek(5200)
    return ok(
        '5s media jump is intentional seek',
        (not natural) and intentional,
        f'natural={natural} intentional={intentional}',
    )


def test_keyframe_undershoot_not_intentional():
    eng = HugeEngine()
    # ~2s backward settle after remote seek — the Live Log storm pattern.
    intentional = eng._looks_like_intentional_seek(-2000)
    below_floor = abs(-2000) < SEEK_DETECT_MIN_MS
    return ok(
        '2s backward settle is not an intentional seek',
        (not intentional) and below_floor,
        f'intentional={intentional} floor={SEEK_DETECT_MIN_MS}',
    )


def test_host_drops_echo_of_remote_seek():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng._last_pot_pos = 13000
    seeks = []

    def fake_set(pos):
        seeks.append(int(pos))
        return True

    import engine.session as session_mod
    original = session_mod.pot_set_current
    session_mod.pot_set_current = fake_set  # type: ignore
    try:
        eng._note_remote_seek_broadcast(13000)
        eng.apply_authoritative_action(
            {
                'action': 'set_position',
                'position': 11000,
                'delta': -2000,
                'requested_delta': -2000,
                'detected': True,
                'origin': 'potplayer',
                'playing': True,
                'sent_at': time.time(),
            },
            actor='fawaz',
            source_client={'id': 'guest1', 'name': 'fawaz'},
        )
        # Near the position we just pushed — echo guard (2500ms) should drop it.
        eng.apply_authoritative_action(
            {
                'action': 'set_position',
                'position': 12500,
                'delta': -500,
                'requested_delta': -500,
                'detected': True,
                'origin': 'potplayer',
                'playing': True,
                'sent_at': time.time(),
            },
            actor='fawaz',
            source_client={'id': 'guest1', 'name': 'fawaz'},
        )
    finally:
        session_mod.pot_set_current = original

    return ok(
        'host ignores guest settle/echo detected seeks',
        seeks == [],
        f'seeks={seeks}',
    )


def test_host_accepts_real_guest_skip():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng._last_pot_pos = 20000
    eng._cached_position = 20000
    seeks = []
    broadcasts = []

    def fake_set(pos):
        seeks.append(int(pos))
        return True

    eng.server.broadcast = lambda msg, exclude=None: broadcasts.append(msg)  # type: ignore
    import engine.session as session_mod
    original = session_mod.pot_set_current
    session_mod.pot_set_current = fake_set  # type: ignore
    try:
        eng.apply_authoritative_action(
            {
                'action': 'set_position',
                'position': 15000,
                'delta': -5000,
                'requested_delta': -5000,
                'detected': True,
                'origin': 'huge',
                'playing': True,
                'sent_at': time.time(),
            },
            actor='fawaz',
            source_client={'id': 'guest1', 'name': 'fawaz'},
        )
    finally:
        session_mod.pot_set_current = original

    return ok(
        'host still applies real guest -5s skip',
        seeks == [15000] and any(
            (m.get('action') or {}).get('action') == 'set_position' for m in broadcasts
        ),
        f'seeks={seeks} broadcasts={len(broadcasts)}',
    )


def test_room_state_marks_programmatic():
    eng = HugeEngine()
    eng.role = 'client'
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    applied = []

    import engine.session as session_mod
    original = session_mod.pot_set_current
    session_mod.pot_set_current = lambda pos: applied.append(int(pos)) or True  # type: ignore
    try:
        eng.handle_network_message({
            'type': 'room_state',
            'room_name': 'Lobby',
            'host_name': 'Host',
            'started': True,
            'users': [],
            'logs': [],
            'elapsed': 10,
            'position': 45000,
            'status': STATUS_PLAYING,
        })
    finally:
        session_mod.pot_set_current = original

    return ok(
        'join snapshot seek is programmatic (echo-guarded)',
        applied == [45000]
        and eng._expected_position == 45000
        and eng._is_echo_of_local_seek(45000),
        f'applied={applied} expected={eng._expected_position}',
    )


def main():
    results = [
        test_natural_playback_not_seek(),
        test_real_5s_skip_is_intentional(),
        test_keyframe_undershoot_not_intentional(),
        test_host_drops_echo_of_remote_seek(),
        test_host_accepts_real_guest_skip(),
        test_room_state_marks_programmatic(),
    ]
    failed = sum(1 for r in results if not r)
    print(f'\n{len(results) - failed}/{len(results)} passed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
