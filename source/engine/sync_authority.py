"""Syncplay-inspired playback authority helpers for Huge Watch Party.

Adapted from Syncplay (https://github.com/Syncplay/syncplay), Apache License 2.0.
Copyright (c) Syncplay contributors. Modifications for Huge / PotPlayer Mini64
by the Huge project.

Syncplay concepts reused here (not a wholesale port of the Syncplay app):

* ignoringOnTheFly — after a local intentional Play/Pause/Seek, ignore remote
  state that would overwrite that intent until the lobby acknowledges it.
* Dual-threshold seek detection — a sample is a seek only when it differs from
  BOTH the last local player position AND the authoritative global position
  (see Syncplay client._determinePlayerStateChange / SEEK_THRESHOLD).
* Pause-change detection — a pause toggle is only a user action when it differs
  from BOTH the tracked local player paused flag AND the global paused flag.
* Latency-adjusted global position when unpaused (messageAge compensation).

PotPlayer Mini64 does not expose Syncplay's player IPC; Huge maps absolute
play/pause onto status-aware Win32 10014 toggles elsewhere.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

# Adapted from syncplay/constants.py SEEK_THRESHOLD (1.0s). PotPlayer reports ms.
SEEK_THRESHOLD_MS = 1000
# Soft drift band: do not hard-seek if within this of the authoritative target.
DO_NOT_RESET_POSITION_MS = 1000  # Syncplay DO_NOT_RESET_POSITION_THRESHOLD ≈ 1s
# Rewind/fast-forward style desync thresholds (seconds → ms), Syncplay defaults.
DEFAULT_REWIND_THRESHOLD_MS = 4000
DEFAULT_FASTFORWARD_THRESHOLD_MS = 5000


@dataclass
class PlayStateSample:
    paused: bool
    position_ms: int
    wall_time: float = 0.0


class IgnoringOnTheFly:
    """Port of Syncplay protocols.clientIgnoringOnTheFly / serverIgnoringOnTheFly.

    Local intentional actions bump `local`. Remote messages may carry the peer's
    counter. While `local > 0`, remote playstate that is not an acknowledgement
    of our change must not undo it (Syncplay handleState skips updateGlobalState).
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.local = 0
        self.remote = 0
        self.expect_paused: Optional[bool] = None
        self.expect_position_ms: Optional[int] = None
        self.changed_at = 0.0

    def note_local_change(
        self,
        *,
        paused: Optional[bool] = None,
        position_ms: Optional[int] = None,
    ) -> int:
        with self._lock:
            self.local += 1
            if paused is not None:
                self.expect_paused = bool(paused)
            if position_ms is not None:
                self.expect_position_ms = int(max(0, position_ms))
            self.changed_at = time.time()
            return self.local

    def wire_fields(self) -> dict:
        with self._lock:
            out = {}
            if self.local:
                out['ignoring_on_the_fly'] = {'client': int(self.local)}
            if self.remote:
                out.setdefault('ignoring_on_the_fly', {})['server'] = int(self.remote)
            return out

    def observe_remote_ignore(self, payload: dict) -> None:
        """Mirror Syncplay handleState ignoringOnTheFly acknowledgement."""
        ignore = (payload or {}).get('ignoring_on_the_fly') or (payload or {}).get('ignoringOnTheFly') or {}
        if not isinstance(ignore, dict):
            return
        with self._lock:
            if 'server' in ignore:
                try:
                    self.remote = int(ignore['server'])
                except Exception:
                    pass
                self.local = 0
            elif 'client' in ignore:
                try:
                    peer = int(ignore['client'])
                except Exception:
                    return
                if peer == self.local:
                    self.local = 0

    def should_apply_remote_playstate(self, paused: Optional[bool] = None) -> bool:
        """Syncplay: apply global state only when clientIgnoringOnTheFly == 0."""
        with self._lock:
            if self.local <= 0:
                return True
            # Acknowledgement: remote matches what we intended.
            if paused is not None and self.expect_paused is not None:
                if bool(paused) == bool(self.expect_paused):
                    self.local = 0
                    return True
            return False

    def clear(self) -> None:
        with self._lock:
            self.local = 0
            self.remote = 0
            self.expect_paused = None
            self.expect_position_ms = None


def determine_player_state_change(
    *,
    tracked_player_paused: bool,
    tracked_player_position_ms: int,
    tracked_global_paused: bool,
    tracked_global_position_ms: int,
    sample_paused: bool,
    sample_position_ms: int,
    seek_threshold_ms: int = SEEK_THRESHOLD_MS,
) -> Tuple[bool, bool]:
    """Syncplay client._determinePlayerStateChange (positions in milliseconds).

    Returns (pause_change, seeked).
    """
    pause_change = (
        bool(tracked_player_paused) != bool(sample_paused)
        and bool(tracked_global_paused) != bool(sample_paused)
    )
    player_diff = abs(int(tracked_player_position_ms) - int(sample_position_ms))
    global_diff = abs(int(tracked_global_position_ms) - int(sample_position_ms))
    seeked = player_diff > seek_threshold_ms and global_diff > seek_threshold_ms
    return pause_change, seeked


def latency_adjusted_position_ms(
    position_ms: int,
    paused: bool,
    message_age_s: float,
) -> int:
    """Syncplay updateGlobalState: when not paused, add RTT/forward delay age."""
    pos = max(0, int(position_ms))
    if paused:
        return pos
    try:
        age_ms = max(0, int(float(message_age_s) * 1000.0))
    except Exception:
        age_ms = 0
    return pos + age_ms


def should_hard_seek(
    local_position_ms: int,
    target_position_ms: int,
    *,
    do_seek_flag: bool,
    threshold_ms: int = DO_NOT_RESET_POSITION_MS,
) -> bool:
    """Avoid unnecessary seeks when already close (Syncplay soft sync)."""
    if do_seek_flag:
        return True
    return abs(int(local_position_ms) - int(target_position_ms)) > int(threshold_ms)
