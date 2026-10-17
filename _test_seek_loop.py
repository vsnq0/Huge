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


def test_skip_publish_before_slow_local_apply():
    """Huge skips must broadcast before PotPlayer SET_CURRENT finishes (PP pattern)."""
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'host'
    eng._last_pot_pos = 10000
    eng._cached_position = 10000
    events = []

    import engine.session as session_mod

    def slow_sync(pos, timeout=2.0):
        events.append(('apply', int(pos), time.monotonic()))
        time.sleep(0.25)
        return True

    original_sync = session_mod.pot_set_current_sync
    original_status = session_mod.pot_status
    session_mod.pot_set_current_sync = slow_sync  # type: ignore
    session_mod.pot_status = lambda: STATUS_PLAYING  # type: ignore
    def _broadcast(msg, exclude=None):
        if msg.get('type') != 'command':
            return
        events.append(
            ('broadcast', (msg.get('action') or {}).get('position'), time.monotonic(), msg)
        )

    eng.server.broadcast = _broadcast  # type: ignore
    try:
        eng.local_action('seek_5000')
        # Wait for worker to finish apply.
        deadline = time.time() + 3.0
        while time.time() < deadline and not any(e[0] == 'apply' for e in events):
            time.sleep(0.02)
        time.sleep(0.35)
    finally:
        session_mod.pot_set_current_sync = original_sync
        session_mod.pot_status = original_status

    broadcasts = [e for e in events if e[0] == 'broadcast']
    applies = [e for e in events if e[0] == 'apply']
    passed = ok('one broadcast', len(broadcasts) == 1, broadcasts)
    passed = ok('one local apply', len(applies) == 1, applies) and passed
    if broadcasts and applies:
        passed = ok(
            'publish before apply',
            broadcasts[0][2] < applies[0][2],
            f'pub={broadcasts[0][2]} apply={applies[0][2]}',
        ) and passed
        action = broadcasts[0][3].get('action') or {}
        passed = ok('delta +5000', int(action.get('delta') or 0) == 5000, action) and passed
        passed = ok('kind skip_rel', action.get('kind') == 'skip_rel', action) and passed
        passed = ok('target 15000', int(action.get('position') or 0) == 15000, action) and passed
    return passed


def test_rapid_skips_preserve_order_one_each():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'host'
    eng._last_pot_pos = 20000
    eng._cached_position = 20000
    broadcasts = []
    applies = []

    import engine.session as session_mod

    original_sync = session_mod.pot_set_current_sync
    original_status = session_mod.pot_status
    session_mod.pot_set_current_sync = lambda pos, timeout=2.0: (  # type: ignore
        applies.append(int(pos)) or True
    )
    session_mod.pot_status = lambda: STATUS_PLAYING  # type: ignore
    def _broadcast(msg, exclude=None):
        if msg.get('type') != 'command':
            return
        broadcasts.append(int((msg.get('action') or {}).get('position') or -1))

    eng.server.broadcast = _broadcast  # type: ignore
    try:
        eng.local_action('seek_5000')
        eng.local_action('seek_5000')
        eng.local_action('seek_-5000')
        deadline = time.time() + 4.0
        while time.time() < deadline and len(broadcasts) < 3:
            time.sleep(0.02)
        time.sleep(0.2)
    finally:
        session_mod.pot_set_current_sync = original_sync
        session_mod.pot_status = original_status

    passed = ok('three broadcasts', broadcasts == [25000, 30000, 25000], broadcasts)
    passed = ok('three applies same order', applies == [25000, 30000, 25000], applies) and passed
    return passed


def test_hotkey_and_button_share_skip_queue():
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'host'
    eng._last_pot_pos = 5000
    eng._cached_position = 5000
    broadcasts = []

    import engine.session as session_mod

    original_sync = session_mod.pot_set_current_sync
    original_status = session_mod.pot_status
    session_mod.pot_set_current_sync = lambda pos, timeout=2.0: True  # type: ignore
    session_mod.pot_status = lambda: STATUS_PLAYING  # type: ignore
    def _broadcast(msg, exclude=None):
        if msg.get('type') != 'command':
            return
        action = msg.get('action') or {}
        broadcasts.append(
            (int(action.get('delta') or 0), int(action.get('position') or 0))
        )

    eng.server.broadcast = _broadcast  # type: ignore
    try:
        eng.local_action('seek_-5000')  # UI button path
        eng._on_hotkey_action('seek_forward', scope='potplayer')  # hotkey → +5000
        deadline = time.time() + 3.0
        while time.time() < deadline and len(broadcasts) < 2:
            time.sleep(0.02)
        time.sleep(0.15)
    finally:
        session_mod.pot_set_current_sync = original_sync
        session_mod.pot_status = original_status

    return ok(
        'button and hotkey each publish once',
        broadcasts == [(-5000, 0), (5000, 5000)],
        broadcasts,
    )


def test_programmatic_seek_not_republished_by_monitor():
    """Huge-owned skip must not mint a second PotPlayer lobby seek from settle."""
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'vsnq'
    eng._last_pot_pos = 20000
    eng._cached_position = 20000
    eng._last_pos_sample_time = time.time()
    eng._last_pot_state = STATUS_PLAYING
    eng._last_broadcast_playing = True
    eng._cached_status = STATUS_PLAYING
    broadcasts = []
    logs = []

    def _broadcast(msg, exclude=None):
        if msg.get('type') == 'command':
            broadcasts.append(msg.get('action') or {})
        elif msg.get('type') == 'log':
            entry = msg.get('entry') or {}
            logs.append(f"{entry.get('actor')} {entry.get('message')}".strip())

    eng.server.broadcast = _broadcast  # type: ignore
    # Simulate Huge intercept skip already applied + remembered.
    eng._remember_programmatic_seek(15000)
    eng._log_user_skip('vsnq', -5000, broadcast=True, input_source='potplayer')
    # Pretend suppress windows expired but echo guard still matches (settle).
    eng._programmatic_position_until = 0.0
    eng._expected_position = -1
    eng._suppress_detection_until = 0.0
    eng._last_pot_pos = 20000
    # Monitor would see a -5s jump to the same target Huge already owns.
    import engine.session as session_mod

    original_busy = session_mod._POT.busy
    original_snap = session_mod.pot_snapshot
    session_mod._POT.busy = lambda: False  # type: ignore
    session_mod.pot_snapshot = lambda want_total=False: {  # type: ignore
        'status': STATUS_PLAYING,
        'position': 15000,
        'total': 3_600_000,
    }
    try:
        eng._monitor_body()
    finally:
        session_mod._POT.busy = original_busy
        session_mod.pot_snapshot = original_snap

    cmd_seeks = [
        a for a in broadcasts
        if a.get('action') == 'set_position'
    ]
    return ok(
        'monitor does not republish Huge-owned seek',
        cmd_seeks == [] and logs.count('vsnq went back 5s (PotPlayer).') == 1,
        {'cmds': cmd_seeks, 'logs': logs},
    )


def test_activity_log_seek_source_attribution():
    """Live Log must keep (Huge) / (PotPlayer) from the original input."""
    eng = HugeEngine()
    eng.role = 'host'
    eng.server.running = True
    eng.started = True
    eng._playback_phase = PHASE_PLAYING
    eng.username = 'vsnq'
    eng._last_pot_pos = 10000
    eng._cached_position = 10000
    log_msgs = []
    cmd_meta = []

    import engine.session as session_mod

    original_sync = session_mod.pot_set_current_sync
    original_status = session_mod.pot_status
    session_mod.pot_set_current_sync = lambda pos, timeout=2.0: True  # type: ignore
    session_mod.pot_status = lambda: STATUS_PLAYING  # type: ignore

    def _broadcast(msg, exclude=None):
        if msg.get('type') == 'log':
            entry = msg.get('entry') or {}
            actor = entry.get('actor') or ''
            message = entry.get('message') or ''
            log_msgs.append(f'{actor} {message}'.strip())
            return
        if msg.get('type') == 'command':
            action = msg.get('action') or {}
            cmd_meta.append(
                (
                    action.get('input_source'),
                    action.get('origin'),
                    int(action.get('delta') or 0),
                )
            )

    eng.server.broadcast = _broadcast  # type: ignore
    try:
        eng.local_action('seek_5000')  # UI → (Huge)
        eng._on_hotkey_action('seek_back', scope='huge')  # Huge-focused → (Huge)
        eng.username = 'Abdullah'
        eng._on_hotkey_action('seek_forward', scope='potplayer')  # PP intercept → (PotPlayer)
        # Genuine PotPlayer-originated skip on the host apply path.
        eng._log_detected_seek_action('Abdullah', 5000, 25000, broadcast=True, input_source='potplayer')
        deadline = time.time() + 3.0
        while time.time() < deadline and len(log_msgs) < 4:
            time.sleep(0.02)
        time.sleep(0.15)
    finally:
        session_mod.pot_set_current_sync = original_sync
        session_mod.pot_status = original_status

    passed = ok(
        'UI skip logs (Huge)',
        any(m == 'vsnq skipped forward 5s (Huge).' for m in log_msgs),
        log_msgs,
    )
    passed = ok(
        'Huge-focused arrow logs (Huge)',
        any(m == 'vsnq went back 5s (Huge).' for m in log_msgs),
        log_msgs,
    ) and passed
    passed = ok(
        'PotPlayer-focused intercept logs (PotPlayer)',
        any(m == 'Abdullah skipped forward 5s (PotPlayer).' for m in log_msgs),
        log_msgs,
    ) and passed
    passed = ok(
        'detected PotPlayer skip logs (PotPlayer)',
        log_msgs.count('Abdullah skipped forward 5s (PotPlayer).') >= 1,
        log_msgs,
    ) and passed
    passed = ok(
        'payload carries input_source',
        ('huge', 'huge', 5000) in cmd_meta and ('potplayer', 'potplayer', 5000) in cmd_meta,
        cmd_meta,
    ) and passed
    # Resolver covers guest/host relay payloads.
    passed = ok(
        'resolve huge payload',
        eng._resolve_seek_input_source({'kind': 'skip_rel', 'origin': 'huge'}) == 'huge',
    ) and passed
    passed = ok(
        'resolve potplayer payload',
        eng._resolve_seek_input_source({'origin': 'potplayer', 'detected': True}) == 'potplayer',
    ) and passed
    return passed


def main():
    results = [
        test_natural_playback_not_seek(),
        test_real_5s_skip_is_intentional(),
        test_keyframe_undershoot_not_intentional(),
        test_host_drops_echo_of_remote_seek(),
        test_host_accepts_real_guest_skip(),
        test_room_state_marks_programmatic(),
        test_skip_publish_before_slow_local_apply(),
        test_rapid_skips_preserve_order_one_each(),
        test_hotkey_and_button_share_skip_queue(),
        test_programmatic_seek_not_republished_by_monitor(),
        test_activity_log_seek_source_attribution(),
    ]
    failed = sum(1 for r in results if not r)
    print(f'\n{len(results) - failed}/{len(results)} passed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
