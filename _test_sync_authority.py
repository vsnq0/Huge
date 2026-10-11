"""Unit tests for Syncplay-adapted sync_authority helpers."""
import sys

sys.path.insert(0, '.')
from engine.sync_authority import (
    IgnoringOnTheFly,
    determine_player_state_change,
    latency_adjusted_position_ms,
    should_hard_seek,
)


def main():
    # Dual-threshold: natural progress near global is NOT a seek
    pause_c, seeked = determine_player_state_change(
        tracked_player_paused=False,
        tracked_player_position_ms=10_000,
        tracked_global_paused=False,
        tracked_global_position_ms=10_050,
        sample_paused=False,
        sample_position_ms=10_200,
        seek_threshold_ms=1000,
    )
    assert not pause_c and not seeked, (pause_c, seeked)
    print('OK natural progress not seek')

    # Genuine seek: far from both player and global
    pause_c, seeked = determine_player_state_change(
        tracked_player_paused=False,
        tracked_player_position_ms=10_000,
        tracked_global_paused=False,
        tracked_global_position_ms=10_100,
        sample_paused=False,
        sample_position_ms=60_000,
        seek_threshold_ms=1000,
    )
    assert not pause_c and seeked, (pause_c, seeked)
    print('OK scrub is seek')

    # User pause: differs from player AND global
    pause_c, seeked = determine_player_state_change(
        tracked_player_paused=False,
        tracked_player_position_ms=10_000,
        tracked_global_paused=False,
        tracked_global_position_ms=10_000,
        sample_paused=True,
        sample_position_ms=10_050,
        seek_threshold_ms=1000,
    )
    assert pause_c and not seeked, (pause_c, seeked)
    print('OK user pause detected')

    # Lagging PLAYING after we already set global paused: NOT a pause_change
    # (player still playing, global already paused — Syncplay requires BOTH differ)
    pause_c, seeked = determine_player_state_change(
        tracked_player_paused=False,
        tracked_player_position_ms=10_000,
        tracked_global_paused=True,
        tracked_global_position_ms=10_000,
        sample_paused=False,
        sample_position_ms=10_100,
        seek_threshold_ms=1000,
    )
    assert not pause_c, pause_c
    print('OK lagging play after global pause is not pause_change')

    fly = IgnoringOnTheFly()
    tok = fly.note_local_change(paused=True)
    assert tok == 1 and fly.local == 1
    assert not fly.should_apply_remote_playstate(paused=False)  # stale play
    assert fly.should_apply_remote_playstate(paused=True)  # ack pause clears
    assert fly.local == 0
    print('OK ignoringOnTheFly blocks stale play')

    fly.note_local_change(paused=True)
    fly.observe_remote_ignore({'ignoring_on_the_fly': {'server': 3}})
    assert fly.local == 0
    print('OK server ignore clears client')

    assert latency_adjusted_position_ms(1000, False, 0.25) == 1250
    assert latency_adjusted_position_ms(1000, True, 0.25) == 1000
    assert not should_hard_seek(5000, 5500, do_seek_flag=False, threshold_ms=1000)
    assert should_hard_seek(5000, 8000, do_seek_flag=False, threshold_ms=1000)
    assert should_hard_seek(5000, 5100, do_seek_flag=True, threshold_ms=1000)
    print('OK latency + hard-seek helpers')
    print('PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
