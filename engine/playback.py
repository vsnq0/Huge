"""Unified playback command bus for Huge Watch Party.

All genuine play/pause/seek/start actions flow through PlaybackCommandBus so
ordering, dedupe, and echo suppression stay consistent. Background monitoring
may only *propose* user commands; programmatic applies never re-broadcast.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


KIND_PLAY = 'play'
KIND_PAUSE = 'pause'
KIND_SEEK_ABS = 'seek_abs'
KIND_SKIP_REL = 'skip_rel'
KIND_START_PREPARE = 'start_prepare'
KIND_START_GO = 'start_go'

ORIGIN_HUGE = 'huge'
ORIGIN_POTPLAYER = 'potplayer'
ORIGIN_PROGRAMMATIC = 'programmatic'
ORIGIN_START = 'start'
ORIGIN_NET = 'net'

PHASE_IDLE = 'idle'
PHASE_LOADING = 'loading'
PHASE_READY = 'ready'
PHASE_STARTING = 'starting'
PHASE_PLAYING = 'playing'


def new_cmd_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class PlaybackCommand:
    kind: str
    cmd_id: str = field(default_factory=new_cmd_id)
    lobby_seq: int = 0
    actor: str = ''
    origin: str = ORIGIN_HUGE
    playing: Optional[bool] = None
    position_ms: Optional[int] = None
    delta_ms: Optional[int] = None
    sent_at: float = field(default_factory=time.time)
    programmatic: bool = False
    source_user_id: str = ''

    def to_wire(self) -> dict:
        d = {
            'action': 'playback_cmd',
            'kind': self.kind,
            'cmd_id': self.cmd_id,
            'lobby_seq': int(self.lobby_seq),
            'actor': self.actor,
            'origin': self.origin,
            'sent_at': float(self.sent_at),
            'programmatic': bool(self.programmatic),
        }
        if self.playing is not None:
            d['playing'] = bool(self.playing)
        if self.position_ms is not None:
            d['position_ms'] = int(self.position_ms)
        if self.delta_ms is not None:
            d['delta_ms'] = int(self.delta_ms)
        if self.source_user_id:
            d['source_user_id'] = self.source_user_id
        # Legacy aliases so older peers / handlers stay readable in traces.
        if self.kind in (KIND_PLAY, KIND_PAUSE):
            d['action_legacy'] = 'set_playing'
            d['playing'] = self.kind == KIND_PLAY if self.playing is None else bool(self.playing)
        elif self.kind == KIND_SEEK_ABS:
            d['action_legacy'] = 'set_position'
            d['position'] = int(self.position_ms or 0)
        elif self.kind == KIND_SKIP_REL:
            d['action_legacy'] = 'seek_' + str(int(self.delta_ms or 0))
        elif self.kind in (KIND_START_PREPARE, KIND_START_GO):
            d['action_legacy'] = 'start_movie'
            d['seek_ms'] = 0
        return d

    @classmethod
    def from_wire(cls, data: dict) -> 'PlaybackCommand':
        data = dict(data or {})
        kind = str(data.get('kind') or '')
        if not kind:
            # Adapt legacy network actions into bus kinds.
            name = str(data.get('action') or data.get('name') or '')
            if name == 'set_playing':
                kind = KIND_PLAY if data.get('playing') else KIND_PAUSE
            elif name == 'set_position':
                kind = KIND_SEEK_ABS
                if 'position_ms' not in data and 'position' in data:
                    data['position_ms'] = data.get('position')
            elif name.startswith('seek_'):
                kind = KIND_SKIP_REL
                try:
                    data['delta_ms'] = int(name.split('_', 1)[1])
                except Exception:
                    data['delta_ms'] = int(data.get('delta') or 0)
            elif name == 'start_movie':
                # Old single-shot start maps to prepare; go is separate in v2.0.8+.
                phase = str(data.get('phase') or '')
                kind = KIND_START_GO if phase == 'go' or data.get('go') else KIND_START_PREPARE
            elif name == 'playback_cmd':
                kind = str(data.get('kind') or '')
        playing = data.get('playing')
        if playing is None and kind in (KIND_PLAY, KIND_PAUSE):
            playing = kind == KIND_PLAY
        pos = data.get('position_ms', data.get('position'))
        try:
            pos_i = int(pos) if pos is not None else None
        except Exception:
            pos_i = None
        try:
            delta_i = int(data.get('delta_ms', data.get('delta'))) if data.get('delta_ms', data.get('delta')) is not None else None
        except Exception:
            delta_i = None
        try:
            # lobby_seq / play_seq only — never fall back to start-generation `seq`
            # (that field is _playback_seq and would falsely mark guest proposals stale).
            lobby_seq = int(data.get('lobby_seq') or data.get('play_seq') or 0)
        except Exception:
            lobby_seq = 0
        return cls(
            kind=kind,
            cmd_id=str(data.get('cmd_id') or new_cmd_id()),
            lobby_seq=lobby_seq,
            actor=str(data.get('actor') or ''),
            origin=str(data.get('origin') or ORIGIN_NET),
            playing=bool(playing) if playing is not None else None,
            position_ms=pos_i,
            delta_ms=delta_i,
            sent_at=float(data.get('sent_at') or time.time()),
            programmatic=bool(data.get('programmatic') or data.get('remote') or data.get('origin') == ORIGIN_START),
            source_user_id=str(data.get('source_user_id') or ''),
        )


class PlaybackCommandBus:
    """Host-authoritative lobby sequence + local suppress / echo state."""

    def __init__(self, trace_fn: Optional[Callable[..., None]] = None):
        self._lock = threading.RLock()
        self.lobby_seq = 0
        self.last_applied_seq = 0
        self.last_play_seq = 0
        self.last_seek_seq = 0
        self.seen_cmd_ids: dict[str, float] = {}
        self.phase = PHASE_IDLE
        self.suppress_until = 0.0
        self.seek_protect_until = 0.0
        self.play_echo_until = 0.0
        self.play_echo_expect: Optional[bool] = None
        self.start_grace_until = 0.0
        self.expected_position = -1
        self.expected_until = 0.0
        self.programmatic_until = 0.0
        self.last_broadcast_playing: Optional[bool] = None
        self.recent_remote_seeks: list[tuple[int, float]] = []
        self.local_seek_echo: list[tuple[int, float]] = []
        self._trace = trace_fn or (lambda **kwargs: None)

    # ---- sequencing ----

    def next_lobby_seq(self) -> int:
        with self._lock:
            self.lobby_seq += 1
            return self.lobby_seq

    def observe_remote_seq(self, seq: int) -> None:
        with self._lock:
            if seq > self.lobby_seq:
                self.lobby_seq = seq

    def accept(self, cmd: PlaybackCommand) -> tuple[bool, str]:
        """Return (ok, reason). Reject stale / duplicate / blocked-during-start."""
        with self._lock:
            now = time.time()
            self._prune_seen(now)
            if cmd.cmd_id and cmd.cmd_id in self.seen_cmd_ids:
                return False, 'duplicate_cmd_id'
            if self.phase == PHASE_STARTING and cmd.kind not in (
                KIND_START_PREPARE, KIND_START_GO,
            ) and not cmd.programmatic:
                # Only start pipeline may mutate timeline during STARTING.
                if cmd.kind in (KIND_PLAY, KIND_PAUSE, KIND_SEEK_ABS, KIND_SKIP_REL):
                    if cmd.origin != ORIGIN_START:
                        return False, 'blocked_during_start'

            seq = int(cmd.lobby_seq or 0)
            if seq > 0:
                # Play/pause family vs seek family — both use lobby_seq total order.
                if seq < self.last_applied_seq:
                    # Allow equal-family catch-up only if same seq already applied (dup).
                    return False, 'stale_lobby_seq'
                if cmd.kind in (KIND_PLAY, KIND_PAUSE):
                    if seq < self.last_play_seq:
                        return False, 'stale_play_seq'
                if cmd.kind in (KIND_SEEK_ABS, KIND_SKIP_REL):
                    if seq < self.last_seek_seq:
                        return False, 'stale_seek_seq'

            if cmd.cmd_id:
                self.seen_cmd_ids[cmd.cmd_id] = now + 30.0
            return True, 'ok'

    def mark_applied(self, cmd: PlaybackCommand) -> None:
        with self._lock:
            seq = int(cmd.lobby_seq or 0)
            if seq > self.last_applied_seq:
                self.last_applied_seq = seq
            if seq > self.lobby_seq:
                self.lobby_seq = seq
            if cmd.kind in (KIND_PLAY, KIND_PAUSE) and seq > self.last_play_seq:
                self.last_play_seq = seq
            if cmd.kind in (KIND_SEEK_ABS, KIND_SKIP_REL) and seq > self.last_seek_seq:
                self.last_seek_seq = seq
            if seq > 0:
                self.observe_remote_seq(seq)

    def _prune_seen(self, now: float) -> None:
        dead = [k for k, exp in self.seen_cmd_ids.items() if exp < now]
        for k in dead:
            self.seen_cmd_ids.pop(k, None)

    # ---- suppress (never shorten with assign) ----

    def bump_suppress(self, until: float) -> None:
        with self._lock:
            self.suppress_until = max(self.suppress_until, float(until))

    def bump_seek_protect(self, seconds: float) -> None:
        until = time.time() + float(seconds)
        with self._lock:
            self.seek_protect_until = max(self.seek_protect_until, until)
            self.suppress_until = max(self.suppress_until, until)

    def mark_play_echo(self, playing: bool, seconds: float = 0.45) -> None:
        until = time.time() + float(seconds)
        with self._lock:
            self.play_echo_expect = bool(playing)
            self.play_echo_until = until
            self.last_broadcast_playing = bool(playing)
            # Extend suppress — never shorten seek protect.
            self.suppress_until = max(self.suppress_until, until)

    def mark_start_grace(self, seconds: float) -> None:
        until = time.time() + float(seconds)
        with self._lock:
            self.start_grace_until = max(self.start_grace_until, until)
            self.suppress_until = max(self.suppress_until, until)
            self.play_echo_expect = True
            self.play_echo_until = max(self.play_echo_until, until)

    def remember_programmatic_seek(self, position_ms: int, seconds: float = 2.2) -> None:
        now = time.time()
        until = now + float(seconds)
        try:
            pos = int(max(0, position_ms))
        except Exception:
            return
        with self._lock:
            self.expected_position = pos
            self.expected_until = until
            self.programmatic_until = until
            self.seek_protect_until = max(self.seek_protect_until, until)
            self.suppress_until = max(self.suppress_until, until)
            self.local_seek_echo = [(p, u) for (p, u) in self.local_seek_echo if u > now][-12:]
            self.local_seek_echo.append((pos, until))

    def note_remote_seek(self, position_ms: int, seconds: float = 3.1) -> None:
        now = time.time()
        until = now + float(seconds)
        try:
            pos = int(max(0, position_ms))
        except Exception:
            return
        with self._lock:
            self.recent_remote_seeks = [(p, u) for (p, u) in self.recent_remote_seeks if u > now][-16:]
            self.recent_remote_seeks.append((pos, until))

    def is_echo_of_local_seek(self, position_ms: int, tol: int = 1500) -> bool:
        try:
            pos = int(position_ms)
        except Exception:
            return False
        now = time.time()
        with self._lock:
            for target, until in self.local_seek_echo:
                if now <= until and abs(pos - int(target)) <= tol:
                    return True
        return False

    def is_echo_of_remote_seek(self, position_ms: int, tol: int = 2500) -> bool:
        try:
            pos = int(position_ms)
        except Exception:
            return False
        now = time.time()
        with self._lock:
            for target, until in self.recent_remote_seeks:
                if now <= until and abs(pos - int(target)) <= tol:
                    return True
        return False

    def should_ignore_play_echo(self, is_playing: bool) -> bool:
        now = time.time()
        with self._lock:
            if now < self.play_echo_until and self.play_echo_expect is not None:
                return bool(is_playing) == bool(self.play_echo_expect)
        return False

    def in_start_grace(self) -> bool:
        return time.time() < self.start_grace_until

    def detection_suppressed(self) -> bool:
        return time.time() < self.suppress_until

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase
            self._trace(reason='phase', phase=phase)

    def reset_lobby(self) -> None:
        with self._lock:
            self.lobby_seq = 0
            self.last_applied_seq = 0
            self.last_play_seq = 0
            self.last_seek_seq = 0
            self.seen_cmd_ids.clear()
            self.phase = PHASE_IDLE
            self.suppress_until = 0.0
            self.seek_protect_until = 0.0
            self.play_echo_until = 0.0
            self.play_echo_expect = None
            self.start_grace_until = 0.0
            self.expected_position = -1
            self.expected_until = 0.0
            self.programmatic_until = 0.0
            self.last_broadcast_playing = None
            self.recent_remote_seeks.clear()
            self.local_seek_echo.clear()


def is_natural_playback_delta(delta_ms: int, wall_elapsed_s: float, playing: bool) -> bool:
    try:
        delta_ms = int(delta_ms)
        wall_elapsed_s = float(wall_elapsed_s)
    except Exception:
        return False
    if wall_elapsed_s <= 0:
        return False
    if not playing:
        return abs(delta_ms) < 400
    if delta_ms < 0:
        return False
    expected = wall_elapsed_s * 1000.0
    lo = max(0.0, expected * 0.2 - 500.0)
    hi = expected * 2.8 + 1000.0
    return lo <= float(delta_ms) <= hi


def classify_seek_delta(delta_ms: int, seek_detect_min_ms: int = 3000) -> tuple[Optional[str], Optional[int]]:
    try:
        delta_ms = int(delta_ms)
    except Exception:
        return None, None
    if not delta_ms:
        return None, None
    amount = abs(delta_ms)
    sign = 1 if delta_ms > 0 else -1
    if amount in (5000, 30000):
        return 'skip', sign * amount
    if 3500 <= amount <= 15000:
        return 'skip', sign * 5000
    if 16000 <= amount <= 45000:
        return 'skip', sign * 30000
    if amount >= seek_detect_min_ms:
        return 'scrub', None
    return None, None


def looks_like_intentional_seek(delta_ms: int, seek_detect_min_ms: int = 3000) -> bool:
    kind, _ = classify_seek_delta(delta_ms, seek_detect_min_ms)
    return kind in ('skip', 'scrub')


def export_sync_logs(
    profile_dir: str,
    username: str = '',
    role: str = '',
    app_version: str = '',
    extra_files: Optional[list[str]] = None,
) -> tuple[bool, str]:
    """Zip playback_trace.log (+ extras) to the Desktop for easy sharing."""
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        if not os.path.isdir(desktop):
            desktop = os.path.expanduser('~')
        stamp = time.strftime('%Y%m%d-%H%M%S')
        safe_user = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (username or 'pc'))[:32]
        out_name = f'Huge-SyncLog-{safe_user}-{stamp}.zip'
        out_path = os.path.join(desktop, out_name)
        trace = os.path.join(profile_dir, 'playback_trace.log')
        summary = os.path.join(profile_dir, f'sync_summary_{stamp}.txt')
        with open(summary, 'w', encoding='utf-8') as fh:
            fh.write(f'Huge Sync Log Export\n')
            fh.write(f'version={app_version}\n')
            fh.write(f'user={username}\n')
            fh.write(f'role={role}\n')
            fh.write(f'exported_at={time.strftime("%Y-%m-%d %H:%M:%S")}\n')
            fh.write(f'trace={trace}\n')
            fh.write('Attach this zip when reporting multiplayer test results.\n')
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(summary, arcname=os.path.basename(summary))
            if os.path.isfile(trace):
                zf.write(trace, arcname='playback_trace.log')
            for path in extra_files or []:
                if path and os.path.isfile(path):
                    zf.write(path, arcname=os.path.basename(path))
        try:
            os.remove(summary)
        except Exception:
            pass
        return True, out_path
    except Exception as exc:
        return False, str(exc)
