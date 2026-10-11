"""Independent fake PotPlayer for multi-process lobby tests.

Each process that imports this (with HUGE_FAKE_POT=1) gets its own FakePot
instance. Position advances in real time while PLAYING so monitor/natural-
playback logic is exercised without Win32.
"""
from __future__ import annotations

import threading
import time

STATUS_STOPPED = -1
STATUS_PAUSED = 1
STATUS_PLAYING = 2


class FakePot:
    def __init__(self, duration_ms: int = 3_600_000):
        self._lock = threading.RLock()
        self._status = STATUS_PAUSED
        self._pos = 0
        self._total = int(duration_ms)
        self._hwnd = True
        self._anchor_wall = 0.0
        self._anchor_pos = 0
        self.cmd_log = []

    def _flush(self):
        if self._status == STATUS_PLAYING and self._anchor_wall:
            elapsed = (time.time() - self._anchor_wall) * 1000.0
            self._pos = int(max(0, min(self._total, self._anchor_pos + elapsed)))

    def load(self, duration_ms: int = 3_600_000):
        with self._lock:
            self._total = int(duration_ms)
            self._pos = 0
            self._status = STATUS_PAUSED
            self._anchor_wall = 0.0
            self._anchor_pos = 0
            self._hwnd = True

    def snapshot(self, want_total=False):
        with self._lock:
            self._flush()
            return {
                'status': self._status,
                'position': self._pos,
                'total': self._total if want_total else None,
                'hwnd': self._hwnd,
            }

    def cmd(self, command_id):
        with self._lock:
            self.cmd_log.append(('cmd', int(command_id), time.time()))
            # 10014 Play/Pause toggle
            if int(command_id) == 10014:
                self._flush()
                if self._status == STATUS_PLAYING:
                    self._status = STATUS_PAUSED
                    self._anchor_wall = 0.0
                    self._anchor_pos = self._pos
                else:
                    self._status = STATUS_PLAYING
                    self._anchor_wall = time.time()
                    self._anchor_pos = self._pos
                return True
            return True

    def status(self):
        with self._lock:
            self._flush()
            return self._status

    def current(self):
        with self._lock:
            self._flush()
            return int(self._pos)

    def total(self):
        with self._lock:
            return int(self._total)

    def set_current(self, ms):
        with self._lock:
            self._flush()
            self._pos = int(max(0, min(self._total, ms)))
            self._anchor_pos = self._pos
            if self._status == STATUS_PLAYING:
                self._anchor_wall = time.time()
            self.cmd_log.append(('set_current', self._pos, time.time()))
            return True

    def set_current_sync(self, ms, timeout=2.0):
        return self.set_current(ms)

    def has_window(self):
        return bool(self._hwnd)

    def play(self):
        with self._lock:
            self._flush()
            if self._status == STATUS_PLAYING:
                return True
            return self.cmd(10014)

    def pause(self):
        with self._lock:
            self._flush()
            if self._status != STATUS_PLAYING:
                return True
            return self.cmd(10014)


_FAKE: FakePot | None = None


def get_fake() -> FakePot:
    global _FAKE
    if _FAKE is None:
        _FAKE = FakePot()
    return _FAKE


def install_fake_pot(session_module):
    """Replace pot_* helpers on engine.session with this process's FakePot."""
    fake = get_fake()

    def pot_window():
        return 1 if fake.has_window() else None

    def pot_cmd(cmd):
        return fake.cmd(cmd)

    def pot_play():
        status = fake.status()
        if status == STATUS_PLAYING:
            return True
        return fake.cmd(10014)

    def pot_pause():
        status = fake.status()
        if status != STATUS_PLAYING:
            return True
        return fake.cmd(10014)

    def pot_status():
        return fake.status()

    def pot_current():
        return fake.current()

    def pot_total():
        return fake.total()

    def pot_set_current(ms):
        return fake.set_current(ms)

    def pot_set_current_sync(ms, timeout=2.0):
        return fake.set_current_sync(ms, timeout=timeout)

    def pot_snapshot(want_total=False):
        return fake.snapshot(want_total=want_total)

    session_module.pot_window = pot_window
    session_module.pot_cmd = pot_cmd
    session_module.pot_play = pot_play
    session_module.pot_pause = pot_pause
    session_module.pot_status = pot_status
    session_module.pot_current = pot_current
    session_module.pot_total = pot_total
    session_module.pot_set_current = pot_set_current
    session_module.pot_set_current_sync = pot_set_current_sync
    session_module.pot_snapshot = pot_snapshot
    # Bypass path checks for lobby create in tests.
    session_module.HugeEngine._valid_potplayer_path = lambda self, path: True
    return fake
