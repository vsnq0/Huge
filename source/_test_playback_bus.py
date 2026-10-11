"""Unit tests for the v2.0.8 PlaybackCommandBus (local protocol only)."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.playback import (  # noqa: E402
    KIND_PAUSE,
    KIND_PLAY,
    KIND_SEEK_ABS,
    KIND_START_PREPARE,
    ORIGIN_HUGE,
    PHASE_STARTING,
    PlaybackCommand,
    PlaybackCommandBus,
    is_natural_playback_delta,
    looks_like_intentional_seek,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label, detail)
    return bool(cond)


def test_lobby_seq_monotonic():
    bus = PlaybackCommandBus()
    a, b, c = bus.next_lobby_seq(), bus.next_lobby_seq(), bus.next_lobby_seq()
    return ok('lobby_seq monotonic', a == 1 and b == 2 and c == 3, f'{a},{b},{c}')


def test_stale_play_rejected():
    bus = PlaybackCommandBus()
    c1 = PlaybackCommand(kind=KIND_PAUSE, lobby_seq=5, origin=ORIGIN_HUGE)
    bus.accept(c1)
    bus.mark_applied(c1)
    c2 = PlaybackCommand(kind=KIND_PLAY, lobby_seq=3, origin=ORIGIN_HUGE)
    accepted, reason = bus.accept(c2)
    return ok('stale play rejected', accepted is False and 'stale' in reason, reason)


def test_newer_play_accepted():
    bus = PlaybackCommandBus()
    c1 = PlaybackCommand(kind=KIND_PAUSE, lobby_seq=5, origin=ORIGIN_HUGE)
    bus.accept(c1)
    bus.mark_applied(c1)
    c2 = PlaybackCommand(kind=KIND_PLAY, lobby_seq=6, origin=ORIGIN_HUGE)
    accepted, reason = bus.accept(c2)
    return ok('newer play accepted', accepted is True, reason)


def test_duplicate_cmd_id():
    bus = PlaybackCommandBus()
    c1 = PlaybackCommand(kind=KIND_PLAY, lobby_seq=1, cmd_id='abc123', origin=ORIGIN_HUGE)
    bus.accept(c1)
    bus.mark_applied(c1)
    c2 = PlaybackCommand(kind=KIND_PLAY, lobby_seq=2, cmd_id='abc123', origin=ORIGIN_HUGE)
    accepted, reason = bus.accept(c2)
    return ok('duplicate cmd_id rejected', accepted is False and reason == 'duplicate_cmd_id', reason)


def test_blocked_during_start():
    bus = PlaybackCommandBus()
    bus.set_phase(PHASE_STARTING)
    c = PlaybackCommand(kind=KIND_PAUSE, lobby_seq=1, origin=ORIGIN_HUGE, programmatic=False)
    accepted, reason = bus.accept(c)
    return ok('user pause blocked during STARTING', accepted is False and reason == 'blocked_during_start', reason)


def test_start_prepare_allowed_during_start():
    bus = PlaybackCommandBus()
    bus.set_phase(PHASE_STARTING)
    c = PlaybackCommand(kind=KIND_START_PREPARE, lobby_seq=1, origin='start', programmatic=True)
    accepted, reason = bus.accept(c)
    return ok('start_prepare allowed during STARTING', accepted is True, reason)


def test_suppress_never_shortens():
    bus = PlaybackCommandBus()
    bus.bump_seek_protect(2.0)
    seek_until = bus.seek_protect_until
    bus.mark_play_echo(False, seconds=0.45)
    return ok(
        'play echo does not shorten seek protect',
        bus.suppress_until >= seek_until and bus.seek_protect_until >= seek_until,
        f'suppress={bus.suppress_until} seek={bus.seek_protect_until}',
    )


def test_echo_opposite_not_ignored():
    bus = PlaybackCommandBus()
    bus.mark_play_echo(False, seconds=1.0)
    same = bus.should_ignore_play_echo(False)
    opposite = bus.should_ignore_play_echo(True)
    return ok('echo ignores same state only', same and not opposite, f'same={same} opp={opposite}')


def test_natural_vs_intentional():
    natural = is_natural_playback_delta(2200, 2.2, True)
    intentional = looks_like_intentional_seek(5200)
    false_scrub = looks_like_intentional_seek(-2000)
    return ok(
        'natural vs intentional seek',
        natural and intentional and not false_scrub,
        f'n={natural} i={intentional} f={false_scrub}',
    )


def test_from_wire_legacy_set_playing():
    cmd = PlaybackCommand.from_wire({'action': 'set_playing', 'playing': True, 'play_seq': 4})
    return ok('legacy set_playing maps to play', cmd.kind == KIND_PLAY and cmd.lobby_seq == 4, str(cmd))


def test_from_wire_ignores_start_seq_as_lobby_seq():
    cmd = PlaybackCommand.from_wire({
        'action': 'set_playing',
        'playing': False,
        'detected': True,
        'seq': 7,  # start-generation counter — must not become lobby_seq
    })
    return ok('start seq is not lobby_seq', cmd.lobby_seq == 0, str(cmd))


def main():
    results = [
        test_lobby_seq_monotonic(),
        test_stale_play_rejected(),
        test_newer_play_accepted(),
        test_duplicate_cmd_id(),
        test_blocked_during_start(),
        test_start_prepare_allowed_during_start(),
        test_suppress_never_shortens(),
        test_echo_opposite_not_ignored(),
        test_natural_vs_intentional(),
        test_from_wire_legacy_set_playing(),
        test_from_wire_ignores_start_seq_as_lobby_seq(),
    ]
    failed = sum(1 for r in results if not r)
    print(f'\n{len(results) - failed}/{len(results)} passed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
