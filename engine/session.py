"""Huge engine: PotPlayer control + lobby protocol.

Sync and networking logic is preserved from the working Huge.py app.
The Tkinter UI is replaced by a JSON snapshot + method API for the Figma UI.
"""
import base64
import ctypes
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

from engine.playback import (
    KIND_PAUSE,
    KIND_PLAY,
    KIND_SEEK_ABS,
    KIND_SKIP_REL,
    KIND_START_GO,
    KIND_START_PREPARE,
    ORIGIN_HUGE,
    ORIGIN_POTPLAYER,
    ORIGIN_PROGRAMMATIC,
    ORIGIN_START,
    PlaybackCommand,
    PlaybackCommandBus,
    classify_seek_delta,
    export_sync_logs,
    is_natural_playback_delta,
    looks_like_intentional_seek,
    new_cmd_id,
)
from engine.sync_authority import (
    SEEK_THRESHOLD_MS,
    IgnoringOnTheFly,
    determine_player_state_change,
    latency_adjusted_position_ms,
    should_hard_seek,
)
from engine import gif_image

APP_NAME = 'Huge'
APP_CREDIT = 'by vsnq'
PORT = 24816


def _read_bundled_version():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(here, '..', 'VERSION.txt'),
        os.path.join(getattr(sys, '_MEIPASS', here), 'VERSION.txt'),
        os.path.join(os.path.dirname(getattr(sys, 'executable', here)), 'VERSION.txt'),
        os.path.join(os.path.dirname(getattr(sys, 'executable', here)), '_internal', 'VERSION.txt'),
    )
    for path in candidates:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                text = fh.read().strip().lstrip('vV')
                if text:
                    return text
        except Exception:
            pass
    return '0.0.0'


APP_VERSION = _read_bundled_version()
# 30ms polling was flooding PotPlayer with Win32 messages and could freeze it,
# especially on 4K/HEVC. Position sync no longer depends on hard periodic seeks.
MONITOR_MS = 200
STATE_SUPPRESS_MS = 800
# 4K/HEVC keyframe seeks commonly take ~0.5s to report the new position.
SEEK_COMMAND_SUPPRESS_MS = 2200
SEEK_BATCH_IDLE_MS = 280
# Manual-seek detection floor. Values below this are never treated as seeks.
# Natural playback across a busy poll gap must not clear this alone — see
# _is_natural_playback_delta().
SEEK_DETECT_MIN_MS = 3000
SEEK_DETECT_MAX_MS = 900000
# Extra ignore window after a remote/programmatic seek settles.
SEEK_SETTLE_GRACE_MS = 900
# Echo-guard after applying a remote/local play/pause. Must stay short so a rapid
# opposite local toggle (pause→play within ~200ms) is still detected and synced.
# Long enough to cover one monitor poll (MONITOR_MS) plus PotPlayer settle.
PLAY_PAUSE_SUPPRESS_MS = 550
# After START MOVIE, ignore stale PotPlayer PAUSED reads / room_state echoes.
# Short grace after START MOVIE so a real user pause still syncs quickly.
START_PLAYBACK_GRACE_MS = 1500
# Position at or below this is treated as "at zero" after seek / start.
START_ZERO_TOLERANCE_MS = 400
# How long the host waits for client start acknowledgements.
START_ACK_TIMEOUT_S = 4.0
# After start, position may advance a few hundred ms while we confirm PLAYING.
START_PLAY_POS_MAX_MS = 2500
# Shared relative delay for coordinated START MOVIE go (clock-skew safe).
START_GO_DELAY_MS = 200

WM_USER = 0x0400
WM_COMMAND = 0x0111

# PotPlayer x64 Win32 commands.
CMD_PLAY = 10000
CMD_PAUSE = 10001
CMD_PLAY_PAUSE = 10014
CMD_FULLSCREEN = 10013
CMD_BACK_5 = 10059  # PotPlayer's time-navigation back command; exact seek is enforced locally below.
CMD_FORWARD_5 = 10060
CMD_BACK_30 = 10061
CMD_FORWARD_30 = 10062
CMD_GET_STATUS = 0x5006
CMD_GET_CURRENT = 0x5004
CMD_GET_TOTAL = 0x5002
CMD_SET_CURRENT = 0x5005

STATUS_STOPPED = -1
STATUS_PAUSED = 1
STATUS_PLAYING = 2

# Playback coordination phases (local + networked start).
PHASE_IDLE = 'idle'
PHASE_LOADING = 'loading'
PHASE_READY = 'ready'
PHASE_STARTING = 'starting'
PHASE_PLAYING = 'playing'

# Visual palette inspired by the supplied purple/black reference.
BG = '#08070b'
BG_2 = '#0c0912'
RAIL = '#0f0b17'
CARD = '#111019'
CARD_2 = '#15121f'
CARD_3 = '#1b1628'
CARD_HOVER = '#241a36'
BORDER = '#2b2238'
BORDER_2 = '#392c4d'
TEXT = '#f7f2ff'
MUTED = '#958ba5'
PURPLE = '#965dff'
PURPLE_2 = '#b88cff'
PURPLE_3 = '#6f36e7'
GREEN = '#4de1a0'
RED = '#ff6077'
YELLOW = '#ffd35c'
WHITE_2 = '#ddd6e8'


def find_font(families, preferred):
    for name in preferred:
        if name in families:
            return name
    return preferred[-1]


def human_time(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f'{h:02d}:{m:02d}:{s:02d}'
    return f'{m:02d}:{s:02d}'


def elapsed_time(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


def now_clock():
    return time.strftime('%H:%M:%S')


def safe_name(value):
    """Sanitize a display name. Strip is applied by callers that need a final name."""
    value = ''.join(ch for ch in str(value or '') if ch.isprintable())
    return value[:32]


def _saved_potplayer_path():
    if os.name != 'nt':
        return ''
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    profile = os.path.join(base, 'HugeWatchParty', 'profile.json')
    try:
        with open(profile, 'r', encoding='utf-8') as fh:
            value = str(json.load(fh).get('potplayer_path') or '').strip()
        if value and os.path.isfile(value):
            name = os.path.basename(value).lower()
            if name in ('potplayermini64.exe', 'potplayer64.exe'):
                return os.path.abspath(value)
    except Exception:
        pass
    return ''


def find_potplayer_exe():
    """Find the PotPlayer 64-bit executable, preferring Huge's saved path."""
    if os.name != 'nt':
        return None
    saved = _saved_potplayer_path()
    if saved:
        return saved
    candidates = []
    for exe in ('PotPlayerMini64.exe', 'PotPlayer64.exe'):
        found = shutil.which(exe)
        if found:
            candidates.append(found)
    roots = [
        os.environ.get('ProgramFiles', r'C:\Program Files'),
        os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
        os.environ.get('LOCALAPPDATA', ''),
    ]
    for root in roots:
        if not root:
            continue
        candidates.extend([
            os.path.join(root, 'DAUM', 'PotPlayer', 'PotPlayerMini64.exe'),
            os.path.join(root, 'DAUM', 'PotPlayer', 'PotPlayer64.exe'),
            os.path.join(root, 'PotPlayer', 'PotPlayerMini64.exe'),
            os.path.join(root, 'PotPlayer', 'PotPlayer64.exe'),
        ])
    try:
        import winreg
        keys = (r'SOFTWARE\DAUM\PotPlayer64', r'SOFTWARE\WOW6432Node\DAUM\PotPlayer64')
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for keypath in keys:
                try:
                    with winreg.OpenKey(hive, keypath) as key:
                        value, _ = winreg.QueryValueEx(key, 'InstallPath')
                        candidates.extend([
                            os.path.join(value, 'PotPlayerMini64.exe'),
                            os.path.join(value, 'PotPlayer64.exe'),
                        ])
                except OSError:
                    pass
    except Exception:
        pass
    for path in dict.fromkeys(candidates):
        if path and os.path.isfile(path):
            return path
    return None


def launch_potplayer():
    """Launch PotPlayer 64-bit if no PotPlayer window is currently open."""
    if os.name != 'nt':
        return False
    if pot_window():
        return True
    path = find_potplayer_exe()
    if path:
        try:
            subprocess.Popen([path], close_fds=True)
            return True
        except OSError:
            pass
    return False


if os.name == 'nt':
    user32 = ctypes.windll.user32
else:
    class _User32Stub:
        def FindWindowW(self, *args): return 0
        def SendMessageW(self, *args): return -1
        def PostMessageW(self, *args): return 0
    user32 = _User32Stub()


def _find_pot_hwnd():
    """Locate the PotPlayer window. Prefer the cheap class lookup."""
    if os.name != 'nt':
        return None
    hwnd = user32.FindWindowW('PotPlayer64', None)
    if hwnd:
        return hwnd
    found = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if 'PotPlayer' in buf.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


# PotPlayer can temporarily stop responding while opening/indexing heavy HEVC/DV files.
# All Win32 traffic goes through one serialized worker so polling and commands never race.
# Do NOT use SMTO_ABORTIFHUNG: during 4K/HEVC seeks PotPlayer often looks "hung" and
# AbortIfHung drops/abandons the message, which freezes control and loses seeks.
POT_MSG_TIMEOUT_MS = 250
POT_SEEK_TIMEOUT_MS = 800
POT_CMD_TIMEOUT_MS = 500
SMTO_NORMAL = 0x0000


class PotController:
    """Single background worker for every PotPlayer Win32 call.

    Polling and user/network commands share this queue so PotPlayer only ever
    handles one message at a time. Pending seeks coalesce to the latest target.
    """

    def __init__(self):
        self._q = queue.Queue()
        self._closed = False
        self._hwnd = 0
        self._hwnd_until = 0.0
        self._busy = False
        self._seek_lock = threading.Lock()
        self._pending_seek_ms = None
        self._seek_queued = False
        self._thread = threading.Thread(target=self._loop, name='HugePotWorker', daemon=True)
        self._thread.start()

    def close(self):
        self._closed = True
        try:
            self._q.put_nowait(None)
        except Exception:
            pass

    def busy(self):
        return bool(self._busy) or (self._pending_seek_ms is not None) or bool(self._seek_queued)

    def _resolve_hwnd(self):
        now = time.time()
        if self._hwnd and now < self._hwnd_until:
            try:
                if user32.IsWindow(self._hwnd):
                    return self._hwnd
            except Exception:
                pass
            self._hwnd = 0
        hwnd = _find_pot_hwnd() or 0
        self._hwnd = int(hwnd) if hwnd else 0
        self._hwnd_until = now + (0.75 if self._hwnd else 0.25)
        return self._hwnd

    def _raw_send(self, msg, wparam=0, lparam=0, timeout_ms=None):
        hwnd = self._resolve_hwnd()
        if not hwnd:
            return False, 0
        if timeout_ms is None:
            timeout_ms = POT_MSG_TIMEOUT_MS
        try:
            result = ctypes.c_ulong(0)
            try:
                ctypes.set_last_error(0)
            except Exception:
                pass
            ok = user32.SendMessageTimeoutW(
                hwnd, int(msg), int(wparam), int(lparam),
                SMTO_NORMAL, int(timeout_ms), ctypes.byref(result),
            )
            if not ok:
                # Timeout while PotPlayer is busy is normal on heavy seeks.
                # Only drop the cached hwnd when the window is actually gone.
                try:
                    if not user32.IsWindow(hwnd):
                        self._hwnd = 0
                        self._hwnd_until = 0.0
                except Exception:
                    self._hwnd = 0
                    self._hwnd_until = 0.0
                return False, 0
            # SendMessageTimeout writes an unsigned LRESULT; PotPlayer status
            # uses -1 for STOPPED, so re-interpret as signed 32-bit.
            value = int(result.value)
            if value >= 0x80000000:
                value -= 0x100000000
            return True, value
        except Exception:
            self._hwnd = 0
            self._hwnd_until = 0.0
            return False, 0

    def _exec(self, kind, args):
        if kind == 'snapshot':
            ok_s, status = self._raw_send(WM_USER, CMD_GET_STATUS, 0)
            ok_p, pos = self._raw_send(WM_USER, CMD_GET_CURRENT, 0)
            total = None
            if args and args[0]:
                ok_t, total_v = self._raw_send(WM_USER, CMD_GET_TOTAL, 0)
                total = int(total_v) if ok_t else -1
            return {
                'status': int(status) if ok_s else STATUS_STOPPED,
                'position': int(pos) if ok_p else -1,
                'total': total,
                'hwnd': bool(self._hwnd),
            }
        if kind == 'cmd':
            ok, _ = self._raw_send(WM_COMMAND, int(args[0]), 0, timeout_ms=POT_CMD_TIMEOUT_MS)
            return ok
        if kind == 'status':
            ok, value = self._raw_send(WM_USER, CMD_GET_STATUS, 0)
            return int(value) if ok else STATUS_STOPPED
        if kind == 'current':
            ok, value = self._raw_send(WM_USER, CMD_GET_CURRENT, 0)
            return int(value) if ok else -1
        if kind == 'total':
            ok, value = self._raw_send(WM_USER, CMD_GET_TOTAL, 0)
            return int(value) if ok else -1
        if kind == 'set_current':
            ok, _ = self._raw_send(
                WM_USER, CMD_SET_CURRENT, int(max(0, args[0])),
                timeout_ms=POT_SEEK_TIMEOUT_MS,
            )
            return ok
        if kind == 'hwnd':
            return bool(self._resolve_hwnd())
        return None

    def _take_seek_target(self):
        with self._seek_lock:
            target = self._pending_seek_ms
            self._pending_seek_ms = None
            self._seek_queued = False
            return target

    def _requeue_seek_if_needed(self):
        with self._seek_lock:
            if self._pending_seek_ms is None or self._seek_queued:
                return
            self._seek_queued = True
        self._q.put(('seek',))

    def _loop(self):
        while not self._closed:
            try:
                item = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                break
            kind = item[0]
            self._busy = True
            try:
                if kind == 'req':
                    _, op, args, evt, box = item
                    try:
                        box['result'] = self._exec(op, args)
                    except Exception as exc:
                        box['error'] = str(exc)
                        box['result'] = None
                    evt.set()
                elif kind == 'seek':
                    # One absolute seek at a time. Newer targets replace older ones.
                    target = self._take_seek_target()
                    if target is not None:
                        self._exec('set_current', (target,))
                    self._requeue_seek_if_needed()
            finally:
                self._busy = False

    def _request(self, op, args=(), timeout=0.35):
        if self._closed:
            return None
        # Never deadlock the worker thread on itself.
        if threading.current_thread() is self._thread:
            return self._exec(op, args)
        evt = threading.Event()
        box = {}
        self._q.put(('req', op, args, evt, box))
        if not evt.wait(timeout):
            return None
        return box.get('result')

    def snapshot(self, want_total=False, timeout=0.45):
        result = self._request('snapshot', (want_total,), timeout=timeout)
        if not isinstance(result, dict):
            return {'status': STATUS_STOPPED, 'position': -1, 'total': -1 if want_total else None, 'hwnd': False}
        return result

    def cmd(self, command_id):
        return bool(self._request('cmd', (command_id,), timeout=0.6))

    def status(self):
        value = self._request('status', (), timeout=0.4)
        return int(value) if isinstance(value, int) else STATUS_STOPPED

    def current(self):
        value = self._request('current', (), timeout=0.4)
        return int(value) if isinstance(value, int) else -1

    def total(self):
        value = self._request('total', (), timeout=0.4)
        return int(value) if isinstance(value, int) else -1

    def set_current(self, ms):
        """Queue an absolute seek. Later seeks replace earlier ones (one in flight)."""
        if self._closed:
            return False
        try:
            target = int(max(0, ms))
        except Exception:
            return False
        with self._seek_lock:
            self._pending_seek_ms = target
            if self._seek_queued:
                return True
            self._seek_queued = True
        self._q.put(('seek',))
        return True

    def set_current_sync(self, ms, timeout=2.0):
        """Send SET_CURRENT through the worker and wait until it has been delivered.

        Returns True on SendMessage success, False on failure, None on wait timeout.
        Used by the Huge skip-button queue so each skip is applied exactly once.
        """
        if self._closed:
            return False
        try:
            target = int(max(0, ms))
        except Exception:
            return False
        result = self._request('set_current', (target,), timeout=timeout)
        if result is None:
            return None
        return bool(result)

    def has_window(self):
        value = self._request('hwnd', (), timeout=0.3)
        return bool(value)


_POT = PotController()


def pot_window():
    return _find_pot_hwnd()


def pot_cmd(cmd):
    return _POT.cmd(cmd)


def pot_play():
    """Start playback. PotPlayer Mini64 only honors Play/Pause toggle (10014),
    not separate Play/Pause IDs — so we toggle only when not already playing.
    """
    status = pot_status()
    if status == STATUS_PLAYING:
        return True
    return pot_cmd(CMD_PLAY_PAUSE)


def pot_pause():
    """Pause playback using the working Play/Pause toggle when currently playing."""
    status = pot_status()
    if status != STATUS_PLAYING:
        return True
    return pot_cmd(CMD_PLAY_PAUSE)


def pot_status():
    return _POT.status()


def pot_current():
    return _POT.current()


def pot_total():
    return _POT.total()


def pot_set_current(ms):
    return _POT.set_current(ms)


def pot_set_current_sync(ms, timeout=2.0):
    return _POT.set_current_sync(ms, timeout=timeout)


def pot_snapshot(want_total=False):
    return _POT.snapshot(want_total=want_total)


def local_addresses():
    ips = []
    try:
        for _, _, _, _, addr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = addr[0]
            if not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Prefer a Tailscale address if one is detected.
    for ip in list(ips):
        if ip.startswith('100.'):
            return [ip] + [x for x in ips if x != ip]
    return ips


def enable_windows_dpi_awareness():
    """Make Tkinter report physical monitor pixels instead of DPI-scaled logical pixels."""
    if os.name != 'nt':
        return
    try:
        # Per-monitor DPI aware (Windows 8.1+). Must be called before Tk creates windows.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_windows_dpi_awareness()


class NetServer:
    def __init__(self, app):
        self.app = app
        self.sock = None
        self.clients = []
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        if self.running:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(('0.0.0.0', PORT))
            self.sock.listen(20)
            self.sock.settimeout(1.0)
            self.running = True
            threading.Thread(target=self.accept_loop, daemon=True).start()
            return True
        except Exception as exc:
            self.app.log_local(f'Host error: {exc}')
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            return False

    def accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
                conn.settimeout(None)
                client = {
                    'conn': conn,
                    'addr': addr,
                    'name': 'Guest',
                    'id': str(uuid.uuid4()),
                    'ready': False,
                    'avatar': '',
                    'lock': threading.Lock(),
                }
                with self.lock:
                    self.clients.append(client)
                threading.Thread(target=self.client_loop, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.app.log_local(f'Accept error: {exc}')

    def client_loop(self, client):
        try:
            reader = client['conn'].makefile('r', encoding='utf-8')
            for line in reader:
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                typ = msg.get('type')
                if typ == 'hello':
                    client['name'] = safe_name(msg.get('name', 'Guest')) or 'Guest'
                    client['avatar'] = self.app._sanitize_remote_avatar(msg.get('avatar', ''))
                    self.app.on_guest_join(client)
                elif typ == 'avatar':
                    client['avatar'] = self.app._sanitize_remote_avatar(msg.get('avatar', ''))
                    user = self.app.users.get(client['id'])
                    if user is not None:
                        user['avatar'] = client['avatar']
                        self.app.broadcast_users()
                elif typ == 'ready':
                    self.app.on_guest_ready(client, bool(msg.get('ready')), bool(msg.get('movie_loaded')))
                elif typ == 'command':
                    self.app.on_guest_command(client, msg.get('action', {}))
                elif typ == 'start_ack':
                    self.app.on_start_ack(client, msg)
                elif typ == 'heartbeat':
                    self.send(client, {'type': 'heartbeat_ack', 't': msg.get('t', time.time())})
        except Exception:
            pass
        finally:
            try:
                reader.close()
            except Exception:
                pass
            self.remove_client(client)
            self.app.on_guest_left(client)

    def send(self, client, obj):
        if not client:
            return False
        try:
            packet = (json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8')
            with client['lock']:
                client['conn'].sendall(packet)
            return True
        except Exception:
            return False

    def broadcast(self, obj, exclude=None):
        with self.lock:
            clients = list(self.clients)
        dead = []
        for client in clients:
            if client is exclude:
                continue
            if not self.send(client, obj):
                dead.append(client)
        for client in dead:
            self.remove_client(client)

    def find(self, user_id):
        with self.lock:
            for client in self.clients:
                if client.get('id') == user_id:
                    return client
        return None

    def remove_client(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
        try:
            client['conn'].shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            client['conn'].close()
        except Exception:
            pass

    def kick(self, client):
        if not client:
            return
        self.send(client, {'type': 'kicked', 'reason': 'The host removed you from the lobby.'})
        self.remove_client(client)

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
        for client in clients:
            try:
                client['conn'].close()
            except Exception:
                pass


class NetClient:
    def __init__(self, app):
        self.app = app
        self.sock = None
        self.running = False
        self.lock = threading.Lock()

    def connect(self, host, name, avatar, port=PORT):
        try:
            self.sock = socket.create_connection((host, port), timeout=6)
            self.sock.settimeout(None)
            self.running = True
            self.send({'type': 'hello', 'name': name, 'avatar': avatar})
            threading.Thread(target=self.read_loop, daemon=True).start()
            return True
        except Exception as exc:
            self.app.log_local(f'Join error: {exc}')
            self.stop()
            return False

    def send(self, obj):
        with self.lock:
            if not self.sock:
                return False
            try:
                self.sock.sendall((json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8'))
                return True
            except Exception:
                return False

    def read_loop(self):
        reader = None
        try:
            reader = self.sock.makefile('r', encoding='utf-8')
            for line in reader:
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                self.app.ui(lambda m=msg: self.app.handle_network_message(m))
        except Exception:
            pass
        finally:
            try:
                if reader:
                    reader.close()
            except Exception:
                pass
            was_running = self.running
            self.running = False
            if was_running:
                self.app.ui(self.app.connection_lost)

    def stop(self):
        self.running = False
        with self.lock:
            sock, self.sock = self.sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass



class HugeEngine:
    def __init__(self):
        self.server = NetServer(self)
        self.client = NetClient(self)
        self.role = None
        self.user_id = 'host'
        self.username = ''
        self.avatar_b64 = ''
        self.avatar_path = ''
        self.avatar_mime = ''
        self._avatar_lobby_b64 = ''
        self.banner_b64 = ''
        self.banner_path = ''
        self.banner_mime = ''
        self.title_font = 'Outfit'
        self.title_font_size = 46
        self.potplayer_path = ''
        self.movie_path = ''
        self.movie_loaded = False
        self.users = {}
        self.logs = []
        self.lobby_name_value = ''
        self.host_name_value = ''
        self.lobby_started_at = 0.0
        self.started = False
        self.closed = False
        self._suppress_detection_until = 0.0
        self._programmatic_position_until = 0.0
        self._expected_position = -1
        self._expected_position_until = 0.0
        self._local_seek_echo_guard = []
        self._last_pot_state = None
        self._last_pot_pos = -1
        self._last_pos_sample_time = 0.0
        self._last_drift_send = 0.0
        # Recent absolute positions we broadcast as remote seeks (host), to ignore echoes.
        self._recent_remote_seek_guard = []
        self._first_play_seen = False
        self._playback_start_grace_until = 0.0
        self._playback_phase = PHASE_IDLE
        self._playback_seq = 0
        # Monotonic play/pause command order (independent of START MOVIE seq).
        self._play_cmd_seq = 0
        self._last_applied_play_seq = 0
        self._last_play_sent_at = 0.0
        # Guest proposal ordering only — must NOT share the host-relay wall clock
        # in _last_play_sent_at (that falsely drops the next guest Play).
        self._last_guest_play_sent_at = 0.0
        # Last play/pause state we broadcast or applied from the network.
        self._last_broadcast_playing = None
        # Syncplay-style authoritative room playstate (global) vs last observed player.
        self._global_paused = True
        self._global_position_ms = 0
        self._player_paused = True
        self._player_position_ms = 0
        # Syncplay ignoringOnTheFly — intentional local changes must not be undone
        # by stale remote / monitor samples until acknowledged.
        self._ignore_fly = IgnoringOnTheFly()
        # After remote set_playing: ignore only the matching echo, not the opposite toggle.
        self._echo_play_expect = None
        self._echo_play_until = 0.0
        # True once PotPlayer has been observed (or confirmed) at _echo_play_expect.
        # Until then, opposite samples are lag — never a new user Play/Pause.
        self._echo_play_confirmed = True
        self._start_acks = {}
        self._start_barrier_seq = 0
        self._start_cmd_id = ''
        self._playback_trace = []
        # Unified command bus — single suppress/order policy (v2.0.8).
        self._bus = PlaybackCommandBus(trace_fn=self._bus_trace)
        self._seek_batch_delta = 0
        self._seek_batch_direction = 0
        self._seek_batch_actor = ''
        self._seek_batch_last = 0.0
        self._seek_batch_job = None
        self._seek_log_suppress_until = 0.0
        self._pot_link_active = False
        self._pot_ever_seen = False
        self._pot_launch_grace_until = 0.0
        self._movie_loading = False
        self._movie_load_lock = threading.Lock()
        self._skip_lock = threading.Lock()
        self._skip_queue = []
        self._skip_worker_alive = False
        self._user_skip_active = False
        self._closing_from_pot = False
        self._joined_target = ''
        self._event_queue = queue.Queue()
        self._ui_queue = queue.Queue()
        self._ui_worker_started = False
        self._profile_save_job = None
        self._lock = threading.RLock()
        self._listeners = []
        self._after_jobs = {}
        self._after_seq = 0
        self.connection_text = 'Choose a movie file to link PotPlayer with Huge.'
        self.connection_kind = 'offline'
        self.pot_badge = 'WAITING FOR FILE'
        self.pot_badge_kind = 'warn'
        self.ready_label = 'NOT READY'
        self.last_error = ''
        self.last_info = ''
        self._pending_close = False
        self._name_draft = ''
        self._lobby_name_draft = ''
        self._join_address_draft = ''
        self.display_address = ''
        self._cached_position = -1
        self._cached_duration = -1
        self._cached_status = STATUS_STOPPED
        self._cached_ips = []
        self._cached_tailscale = False
        self._last_duration_read = 0.0
        self._load_profile()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._clock_loop, daemon=True).start()
        threading.Thread(target=self._net_loop, daemon=True).start()

    def subscribe(self, fn):
        self._listeners.append(fn)

    def _push(self):
        if not self._listeners:
            return
        state = self.ui_state()
        for fn in list(self._listeners):
            try:
                fn(state)
            except Exception:
                pass

    def ui(self, fn):
        """Run UI/network callbacks on one serialized queue (never concurrent).

        Spawning a new thread per message let pause/play apply out of order on
        localhost and Tailscale, so a later Play could lose to an earlier Pause.
        """
        if self.closed:
            return
        if not hasattr(self, '_ui_queue'):
            self._ui_queue = queue.Queue()
            self._ui_worker_started = False
            self._ui_worker_ident = None
        self._ui_queue.put(fn)
        if not self._ui_worker_started:
            self._ui_worker_started = True
            threading.Thread(target=self._ui_worker_loop, daemon=True, name='HugeUIQueue').start()

    def ui_call(self, fn, timeout=8.0):
        """Run fn on the UI queue and wait (no-op wait if already on that thread)."""
        if self.closed:
            return None
        if threading.get_ident() == getattr(self, '_ui_worker_ident', None):
            return fn()
        done = threading.Event()
        box = {}

        def wrapper():
            try:
                box['v'] = fn()
            except Exception as exc:
                box['e'] = exc
            finally:
                done.set()

        self.ui(wrapper)
        if not done.wait(timeout):
            self._log_playback('ui_call_timeout', origin='local', timeout=timeout)
            return None
        if 'e' in box:
            raise box['e']
        return box.get('v')

    def _ui_worker_loop(self):
        self._ui_worker_ident = threading.get_ident()
        while not self.closed:
            try:
                fn = self._ui_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if fn is None:
                break
            try:
                fn()
            except Exception:
                pass
            try:
                self._push()
            except Exception:
                pass

    def _after(self, ms, fn):
        if self.closed:
            return None
        self._after_seq += 1
        job_id = self._after_seq
        def run():
            time.sleep(max(0, ms) / 1000.0)
            if self.closed or self._after_jobs.get(job_id) is None:
                return
            self._after_jobs.pop(job_id, None)
            try:
                fn()
            except Exception:
                pass
            self._push()
        self._after_jobs[job_id] = True
        threading.Thread(target=run, daemon=True).start()
        return job_id

    def after(self, ms, fn):
        return self._after(ms, fn)

    def after_cancel(self, job_id):
        self._after_jobs.pop(job_id, None)

    def log_local(self, msg):
        self.connection_text = str(msg)
        self._push()

    def set_connection(self, text, good=False, warn=False):
        self.connection_text = text
        self.connection_kind = 'online' if good else ('waiting' if warn else 'offline')
        self._push()

    def _info(self, title, msg):
        self.last_info = str(msg)
        self.connection_text = str(msg)

    def _error(self, title, msg):
        self.last_error = str(msg)
        self.connection_text = str(msg)

    def get_name(self):
        return safe_name(self._name_draft or self.username).strip()

    def set_name_draft(self, value):
        # Keep keystrokes responsive: no UI push on every character. Persistence is
        # debounced; the React input owns the live text while typing.
        cleaned = safe_name(value)
        self._name_draft = cleaned
        if not self.role:
            self.username = cleaned.strip()
            self._schedule_profile_save()
        return {'ok': True}

    def set_lobby_name_draft(self, value):
        self._lobby_name_draft = str(value or '')[:48]
        self._push()
        return self.ui_state()

    def set_join_address_draft(self, value):
        self._join_address_draft = str(value or '').strip()
        self._push()
        return self.ui_state()

    def refresh_action_state(self):
        self._push()

    def update_ready_button(self):
        ready = self.users.get(self.user_id, {}).get('ready', False)
        self.ready_label = 'READY' if ready else 'NOT READY'

    def update_potplayer_path_ui(self):
        if self._valid_potplayer_path(self.potplayer_path):
            self.pot_badge = 'READY' if self._pot_link_active else 'PATH SAVED'
            self.pot_badge_kind = 'good'
        else:
            self.pot_badge = 'WAITING FOR FILE'
            self.pot_badge_kind = 'warn'
        self.refresh_action_state()

    def update_profile_preview(self):
        self._push()

    def render_members(self):
        self._push()

    def render_logs(self):
        self._push()

    def append_log(self, message, actor='', broadcast=False, source_client=None, parts=None):
        elapsed = int(time.time() - self.lobby_started_at) if self.lobby_started_at else 0
        actor_name = safe_name(actor)
        # Actor is stored separately; the UI prefixes it once. Call sites often
        # still pass "Name did X" — strip a leading actor so it never appears twice.
        text = self._log_message_without_actor(str(message), actor_name)
        now = time.time()
        fp = (actor_name, text)
        if (
            self.logs
            and getattr(self, '_last_log_fp', None) == fp
            and (now - float(getattr(self, '_last_log_at', 0) or 0)) < 0.75
        ):
            return
        self._last_log_fp = fp
        self._last_log_at = now
        self._log_seq = int(getattr(self, '_log_seq', 0) or 0) + 1
        rich = parts if isinstance(parts, list) and parts else self._log_parts_for_message(text)
        entry = {
            'id': self._log_seq,
            'time': elapsed_time(elapsed),
            'actor': actor_name,
            'message': text,
            'parts': rich,
        }
        self.logs.append(entry)
        self.logs = self.logs[-60:]
        self.render_logs()
        if broadcast and self.role == 'host' and self.server.running:
            self.server.broadcast({'type': 'log', 'entry': entry}, exclude=source_client)

    @staticmethod
    def _log_parts_for_message(message: str):
        """Split a log message into plain/emphasis spans for the Live Log UI."""
        import re
        text = str(message or '')
        if not text:
            return []
        patterns = [
            # skipped forward/back 5s|30s
            (re.compile(r'^(skipped forward|went back)\s+(5s|30s)(\s*\([^)]+\))?(\.?)$', re.I),
             lambda m: [
                 {'t': 'em', 'v': m.group(1)},
                 {'t': 'text', 'v': ' '},
                 {'t': 'em', 'v': m.group(2)},
                 {'t': 'text', 'v': (m.group(3) or '') + (m.group(4) or '')},
             ]),
            (re.compile(r'^(paused the movie|resumed the movie|started the movie|paused playback|resumed playback)(\s*\([^)]+\))?(\.?)$', re.I),
             lambda m: [
                 {'t': 'em', 'v': m.group(1)},
                 {'t': 'text', 'v': (m.group(2) or '') + (m.group(3) or '')},
             ]),
            (re.compile(r'^(started the movie from 00:00:00)(\.?)$', re.I),
             lambda m: [
                 {'t': 'em', 'v': m.group(1)},
                 {'t': 'text', 'v': m.group(2) or ''},
             ]),
            (re.compile(r'^(seeked to)\s+(\d{2}:\d{2}:\d{2})(\.?)$', re.I),
             lambda m: [
                 {'t': 'em', 'v': m.group(1)},
                 {'t': 'text', 'v': ' '},
                 {'t': 'em', 'v': m.group(2)},
                 {'t': 'text', 'v': m.group(3) or ''},
             ]),
        ]
        for rx, builder in patterns:
            m = rx.match(text.strip())
            if m:
                return [p for p in builder(m) if p.get('v')]
        return [{'t': 'text', 'v': text}]

    @staticmethod
    def _log_message_without_actor(message, actor):
        text = str(message or '').strip()
        name = str(actor or '').strip()
        if not name or not text:
            return text
        prefix = f'{name} '
        while text.startswith(prefix):
            text = text[len(prefix):].lstrip()
        return text

    def mark_paused_user(self, actor, paused):
        target = None
        for uid, user in self.users.items():
            if user.get('name') == actor:
                target = uid
                break
        if paused:
            for user in self.users.values():
                user['paused'] = (user is self.users.get(target))
        else:
            for user in self.users.values():
                user['paused'] = False
        self.render_members()
        if self.role == 'host':
            self.broadcast_users()

    def _load_profile(self):
        try:
            path = self._profile_file()
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                name = safe_name(str(data.get('name') or ''))
                avatar = str(data.get('avatar_b64') or '')
                if name:
                    self._name_draft = name
                    self.username = name
                if avatar:
                    self.avatar_b64 = avatar
                    self.avatar_path = str(data.get('avatar_path') or '')
                    self.avatar_mime = str(data.get('avatar_mime') or '')
                saved_pp = str(data.get('potplayer_path') or '').strip()
                if saved_pp and os.path.isfile(saved_pp) and os.path.basename(saved_pp).lower() in ('potplayermini64.exe', 'potplayer64.exe'):
                    self.potplayer_path = os.path.abspath(saved_pp)
                self.banner_path = str(data.get('banner_path') or '')
                self.banner_mime = str(data.get('banner_mime') or '')
                font = str(data.get('title_font') or '').strip()
                if font:
                    self.title_font = font
                try:
                    size = int(data.get('title_font_size') or 0)
                    if 22 <= size <= 72:
                        self.title_font_size = size
                except Exception:
                    pass
        except Exception:
            pass
        self.update_potplayer_path_ui()
        threading.Thread(target=self._finish_profile_load, daemon=True).start()

    def _finish_profile_load(self):
        try:
            if not self._valid_potplayer_path(self.potplayer_path):
                detected = find_potplayer_exe()
                if detected:
                    self.potplayer_path = os.path.abspath(detected)
                    self._save_profile()
            # Restore saved images as-is. Never re-crop on load — that would
            # destroy the user's Discord-style framing.
            if not self.avatar_b64:
                gif_disk = os.path.join(self._avatar_temp_dir(), 'avatar_final.gif')
                png_disk = os.path.join(self._avatar_temp_dir(), 'avatar_final.png')
                if os.path.isfile(gif_disk):
                    self.avatar_b64 = self._file_to_display_b64(gif_disk)
                    self.avatar_mime = 'image/gif'
                elif os.path.isfile(png_disk):
                    self.avatar_b64 = self._file_to_display_b64(png_disk)
                    self.avatar_mime = 'image/png'
            # Refresh lobby thumb used on hello / member list (safe across PCs).
            self._avatar_lobby_b64 = ''
            if self.avatar_b64:
                self._avatar_for_wire()
            self._load_saved_banner()
            self.update_potplayer_path_ui()
            self._push()
        except Exception:
            pass

    def prepare_image_edit(self, path, mode):
        """Load a chosen picture for the Discord-style crop editor (preview only)."""
        if not path or not os.path.isfile(path):
            return {'ok': False, 'error': 'Could not open that picture.'}
        mode = 'avatar' if mode == 'avatar' else 'banner'
        abs_path = os.path.abspath(path)
        # Animated GIFs: keep a real GIF preview so the crop editor plays frames.
        if gif_image.is_gif_file(abs_path):
            ok, err, meta = gif_image.prepare_gif_preview(abs_path)
            if not ok:
                return {'ok': False, 'error': err or 'Could not open that GIF.'}
            return {
                'ok': True,
                'mode': mode,
                'path': abs_path,
                'preview_b64': meta['preview_b64'],
                'preview_width': meta['preview_width'],
                'preview_height': meta['preview_height'],
                'source_width': meta['source_width'],
                'source_height': meta['source_height'],
                'mime': 'image/gif',
                'animated': bool(meta.get('animated')),
            }
        ok, err, meta = self._native_edit_preview(abs_path, max_edge=1920)
        if not ok:
            return {'ok': False, 'error': err or 'Could not open that picture.'}
        return {
            'ok': True,
            'mode': mode,
            'path': abs_path,
            'preview_b64': meta['preview_b64'],
            'preview_width': meta['preview_width'],
            'preview_height': meta['preview_height'],
            'source_width': meta['source_width'],
            'source_height': meta['source_height'],
            'mime': 'image/jpeg',
            'animated': False,
        }

    def save_image_crop(self, payload):
        """Persist the user's crop. Overlay/veil from the editor is never baked in."""
        try:
            mode = 'avatar' if str(payload.get('mode')) == 'avatar' else 'banner'
            path = str(payload.get('path') or '')
            x = float(payload.get('x', 0))
            y = float(payload.get('y', 0))
            w = float(payload.get('width', 0))
            h = float(payload.get('height', 0))
        except Exception:
            return {'ok': False, 'error': 'Invalid crop.', **self.ui_state()}
        if not path or not os.path.isfile(path) or w <= 1 or h <= 1:
            return {'ok': False, 'error': 'Could not save that picture.', **self.ui_state()}
        # Preserve GIF (including animation) whenever the source is a GIF file.
        use_gif = gif_image.is_gif_file(path)
        if use_gif:
            ok_gif, err_gif, _meta = gif_image.validate_gif_file(path)
            if not ok_gif:
                return {'ok': False, 'error': err_gif or 'Could not open that GIF.', **self.ui_state()}
        if mode == 'avatar':
            if use_gif:
                out = os.path.join(self._avatar_temp_dir(), 'avatar_final.gif')
                ok, err = gif_image.crop_animated_gif(
                    path, out, x, y, w, h, square=True, out_size=512,
                )
                # Remove stale PNG so load prefers GIF.
                try:
                    stale = os.path.join(self._avatar_temp_dir(), 'avatar_final.png')
                    if os.path.isfile(stale):
                        os.remove(stale)
                except Exception:
                    pass
            else:
                out = os.path.join(self._avatar_temp_dir(), 'avatar_final.png')
                ok, err = self._native_crop_avatar(path, out, x, y, w, h, 512)
                try:
                    stale = os.path.join(self._avatar_temp_dir(), 'avatar_final.gif')
                    if os.path.isfile(stale):
                        os.remove(stale)
                except Exception:
                    pass
            if not ok:
                self.last_error = err or 'Could not save that picture.'
                return {'ok': False, 'error': self.last_error, **self.ui_state()}
            data = self._file_to_display_b64(out)
            if not data:
                return {'ok': False, 'error': 'Could not save that picture.', **self.ui_state()}
            self.avatar_path = path
            self.avatar_b64 = data
            self.avatar_mime = 'image/gif' if use_gif else 'image/png'
            self._avatar_lobby_b64 = self._make_lobby_avatar_b64(out)
            self._save_profile()
            if self.role == 'host' and self.user_id in self.users:
                self.users[self.user_id]['avatar'] = self._avatar_for_wire()
                self.broadcast_users()
            elif self.role == 'client' and self.client and self.client.running:
                self.client.send({
                    'type': 'avatar',
                    'avatar': self._avatar_for_wire(),
                })
            self.set_connection('Profile picture updated.', good=True)
            return {'ok': True, **self.ui_state()}

        if use_gif:
            out = self._banner_gif_file()
            ok, err = gif_image.crop_animated_gif(
                path, out, x, y, w, h, square=False, max_width=1600,
            )
            try:
                stale = self._banner_file()
                if os.path.isfile(stale):
                    os.remove(stale)
            except Exception:
                pass
        else:
            out = self._banner_file()
            ok, err = self._native_crop_banner(path, out, x, y, w, h, 1600)
            try:
                stale = self._banner_gif_file()
                if os.path.isfile(stale):
                    os.remove(stale)
            except Exception:
                pass
        if not ok:
            self.last_error = err or 'Could not save that picture.'
            return {'ok': False, 'error': self.last_error, **self.ui_state()}
        self.banner_path = path
        self.banner_b64 = self._file_to_display_b64(out)
        self.banner_mime = 'image/gif' if use_gif else 'image/jpeg'
        self._save_profile()
        self.set_connection('Banner updated.', good=True)
        return {'ok': True, **self.ui_state()}

    def apply_avatar_file(self, path):
        """Legacy entry: open the crop editor via prepare_image_edit instead."""
        return self.prepare_image_edit(path, 'avatar')

    def apply_banner_file(self, path):
        """Legacy entry: open the crop editor via prepare_image_edit instead."""
        return self.prepare_image_edit(path, 'banner')

    def _banner_file(self):
        return os.path.join(os.path.dirname(self._profile_file()), 'banner.jpg')

    def _banner_gif_file(self):
        return os.path.join(os.path.dirname(self._profile_file()), 'banner.gif')

    def _file_to_display_b64(self, path):
        """Return a data-URL (preferred) or raw base64 for UI <img>/CSS backgrounds."""
        try:
            with open(path, 'rb') as fh:
                blob = fh.read()
            if not blob:
                return ''
            mime = gif_image.sniff_image_mime(blob) or 'application/octet-stream'
            return gif_image.data_url(mime, base64.b64encode(blob).decode('ascii'))
        except Exception:
            return ''

    def _load_saved_banner(self):
        gif_path = self._banner_gif_file()
        jpg_path = self._banner_file()
        if os.path.isfile(gif_path):
            self.banner_b64 = self._file_to_display_b64(gif_path)
            self.banner_mime = 'image/gif'
        elif os.path.isfile(jpg_path):
            self.banner_b64 = self._file_to_display_b64(jpg_path)
            self.banner_mime = 'image/jpeg'

    def apply_potplayer_path(self, path):
        if not path:
            return self.ui_state()
        base = os.path.basename(path).lower()
        if base not in ('potplayermini64.exe', 'potplayer64.exe'):
            self.last_error = 'Please select PotPlayerMini64.exe or PotPlayer64.exe.'
            return self.ui_state()
        self.potplayer_path = os.path.abspath(path)
        self._save_profile()
        self.update_potplayer_path_ui()
        self.set_connection('PotPlayer 64-bit path saved.', good=True)
        return self.ui_state()

    def create_room(self, lobby_name=None):
        if self.role:
            return self.ui_state()
        self.username = self.get_name()
        if not self.username:
            self.last_error = 'Please enter your display name first.'
            return self.ui_state()
        if not self._valid_potplayer_path(self.potplayer_path):
            self.last_error = 'Select PotPlayer Path first.'
            return self.ui_state()
        room_name = (lobby_name if lobby_name is not None else self._lobby_name_draft).strip()
        if not room_name:
            self.last_error = 'The host must name the lobby.'
            return self.ui_state()
        if not self.server.start():
            self.last_error = f'Could not open port {PORT}. Another app may be using it, or Windows Firewall may be blocking it.'
            return self.ui_state()
        self.role = 'host'
        self.user_id = 'host'
        self.movie_path = ''
        self.movie_loaded = False
        self._pot_link_active = False
        self.lobby_name_value = room_name[:48]
        self.lobby_started_at = time.time()
        self.users = {
            'host': {
                'id': 'host',
                'name': self.username,
                'ready': False,
                'movie_loaded': False,
                'host': True,
                'avatar': self._avatar_for_wire(),
            }
        }
        address = self.host_address()
        self.display_address = address
        self.set_connection('Hosting lobby • waiting for friends.', good=True)
        self.append_log('Lobby created.', actor=self.username, broadcast=False)
        self._set_movie_status(False, '')
        self.refresh_action_state()
        return self.ui_state()

    def join_room(self, target=None):
        if self.client.running or self.role:
            return self.ui_state()
        self.username = self.get_name()
        if not self.username:
            self.last_error = 'Please enter your display name first.'
            return self.ui_state()
        target = (target if target is not None else self._join_address_draft).strip()
        if not target:
            self.last_error = 'Enter your friend’s host address first.'
            return self.ui_state()
        host, port = self.parse_host(target)
        self._joined_target = f'{host}:{port}'
        self.movie_path = ''
        self.movie_loaded = False
        self._pot_link_active = False
        self.set_connection('Connecting…', warn=True)
        threading.Thread(target=self._join_thread, args=(host, port), daemon=True).start()
        return self.ui_state()

    def copy_address(self):
        value = self.display_address or self._joined_target or ''
        if ':' in value and 'appears' not in value and value not in ('—', 'Connected'):
            self.last_info = 'copied:' + value
            self.set_connection('Lobby IP copied.', good=True)
            return {'ok': True, 'address': value, **self.ui_state()}
        self.set_connection('No lobby IP is available yet.', warn=True)
        return {'ok': False, 'address': '', **self.ui_state()}

    def leave_lobby(self):
        if not self.role:
            return self.ui_state()
        try:
            self.server.stop()
            self.client.stop()
        except Exception:
            pass
        self.role = None
        self.user_id = str(uuid.uuid4())
        self.users = {}
        self.logs = []
        self.started = False
        self._playback_phase = PHASE_IDLE
        self._playback_seq = 0
        self._play_cmd_seq = 0
        self._last_applied_play_seq = 0
        self._last_play_sent_at = 0.0
        # Guest proposal ordering only — must NOT share the host-relay wall clock
        # in _last_play_sent_at (that falsely drops the next guest Play).
        self._last_guest_play_sent_at = 0.0
        self._last_broadcast_playing = None
        self._global_paused = True
        self._global_position_ms = 0
        self._player_paused = True
        self._player_position_ms = 0
        if getattr(self, '_ignore_fly', None):
            self._ignore_fly.clear()
        else:
            self._ignore_fly = IgnoringOnTheFly()
        self._echo_play_expect = None
        self._echo_play_until = 0.0
        self._echo_play_confirmed = True
        self._start_acks = {}
        self._start_barrier_seq = 0
        self._start_cmd_id = ''
        if getattr(self, '_bus', None):
            self._bus.reset_lobby()
        self.lobby_name_value = ''
        self.lobby_started_at = 0.0
        self.display_address = ''
        self.set_connection('Left the lobby.', warn=True)
        self.refresh_action_state()
        return self.ui_state()

    def begin_select_movie(self, path):
        if not self.role:
            self.set_connection('Create or join a lobby first.', warn=True)
            return self.ui_state()
        if not path:
            return self.ui_state()
        with self._movie_load_lock:
            if self._movie_loading:
                self.set_connection('Already loading a movie into PotPlayer…', warn=True)
                return self.ui_state()
            self._movie_loading = True
        self._playback_phase = PHASE_LOADING
        self.started = False
        self._log_playback('select_begin', origin='ui', path=os.path.basename(path))
        self.set_connection('Loading movie into PotPlayer…', warn=True)
        # Hold detection/monitor Win32 traffic while PotPlayer opens/indexes the file.
        self._suppress_detection_until = time.time() + 30.0
        threading.Thread(target=self._load_movie_thread, args=(path,), daemon=True).start()
        return self.ui_state()

    def _monitor_loop(self):
        while not self.closed:
            try:
                self._monitor_body()
            except Exception:
                pass
            time.sleep(MONITOR_MS / 1000.0)

    def _clock_loop(self):
        while not self.closed:
            time.sleep(0.25)

    def _net_loop(self):
        while not self.closed:
            try:
                ips = local_addresses()
                self._cached_ips = ips
                self._cached_tailscale = any(ip.startswith('100.') for ip in ips)
                if self.role == 'host' and ips:
                    self.display_address = f'{ips[0]}:{PORT}'
            except Exception:
                pass
            for _ in range(50):
                if self.closed:
                    return
                time.sleep(0.1)

    def close(self):
        if self.closed:
            return
        try:
            self._save_profile()
        except Exception:
            pass
        self.closed = True
        try:
            self.server.stop()
            self.client.stop()
        except Exception:
            pass
        try:
            if self._pot_link_active:
                self.close_potplayer()
        except Exception:
            pass

    def request_close(self):
        self._pending_close = True
        self.close()

    def ui_state(self):
        name = self.get_name()
        active = self.role is not None
        pot_ready = self._valid_potplayer_path(self.potplayer_path)
        everyone_ready = bool(self.users) and self.role == 'host' and self.all_ready()
        me = self.users.get(self.user_id, {})
        elapsed = max(0, int(time.time() - self.lobby_started_at)) if self.lobby_started_at else 0
        pos = self._cached_position
        total = self._cached_duration
        status = self._cached_status
        playing = status == STATUS_PLAYING
        members = []
        for user in self.users.values():
            item = dict(user)
            item['avatar'] = item.get('avatar') or ''
            members.append(item)
        tailscale = bool(self._cached_tailscale)
        all_ready = bool(self.users) and all(u.get('ready') and u.get('movie_loaded') for u in self.users.values())
        return {
            'app': APP_NAME,
            'credit': APP_CREDIT,
            'app_version': APP_VERSION,
            'role': self.role,
            'user_id': self.user_id,
            'username': name,
            'avatar_b64': self.avatar_b64,
            'banner_b64': self.banner_b64,
            'potplayer_path': self.potplayer_path,
            'pot_ready': pot_ready,
            'pot_badge': self.pot_badge,
            'pot_badge_kind': self.pot_badge_kind,
            'movie_path': self.movie_path,
            'movie_name': os.path.basename(self.movie_path) if self.movie_path else '',
            'movie_loaded': self.movie_loaded,
            'lobby_name': self.lobby_name_value,
            'host_name': self.username if self.role == 'host' else getattr(self, 'host_name_value', ''),
            'address': self.display_address or self._joined_target,
            'users': members,
            'logs': self.logs[-60:],
            'started': self.started,
            'ready': bool(me.get('ready')),
            'elapsed': elapsed,
            'elapsed_text': elapsed_time(elapsed),
            'connection_text': self.connection_text,
            'connection_kind': self.connection_kind,
            'position': pos if isinstance(pos, int) else -1,
            'duration': total if isinstance(total, int) else -1,
            'playing': playing,
            'paused': (not playing) and self.started,
            'all_ready': all_ready,
            'member_count': len(members),
            'ready_count': sum(1 for u in members if u.get('ready')),
            'can_create': bool(name and pot_ready and not active),
            'can_join': bool(name and not active),
            'can_select_movie': bool(active and pot_ready),
            'can_ready': bool(active and self.movie_loaded),
            'can_ready_all': bool(self.role == 'host'),
            'can_start': bool(self.role == 'host' and all_ready and not self.started),
            'can_playback': bool(active and self.started),
            'can_skip': bool(active and self.movie_loaded),
            'host': self.role == 'host',
            'in_lobby': active,
            'last_error': self.last_error,
            'last_info': self.last_info,
            'lobby_name_draft': self._lobby_name_draft,
            'join_address_draft': self._join_address_draft,
            'tailscale': tailscale,
            'closing': self._pending_close,
            'playback_phase': self._playback_phase,
            'playback_seq': self._playback_seq,
            'play_cmd_seq': self._play_cmd_seq,
            'quote': 'Why be ashamed of what you want?',
            'title_font': self.title_font or 'Outfit',
            'title_font_size': int(self.title_font_size or 46),
            'avatar_mime': self.avatar_mime or '',
            'banner_mime': self.banner_mime or '',
        }

    TITLE_FONTS = (
        'Outfit',
        'Segoe UI',
        'Arial',
        'Calibri',
        'Verdana',
        'Trebuchet MS',
        'Georgia',
        'Times New Roman',
        'Consolas',
        'Cascadia Mono',
        'Comic Sans MS',
        'Impact',
    )

    def list_title_fonts(self):
        return list(self.TITLE_FONTS)

    def set_title_font(self, value):
        name = str(value or '').strip() or 'Outfit'
        if name not in self.TITLE_FONTS:
            # Allow any installed family the UI offers; still sanitize length.
            name = name[:64] or 'Outfit'
        self.title_font = name
        self._save_profile()
        self._push()
        return {'ok': True, **self.ui_state()}

    def set_title_font_size(self, value):
        try:
            size = int(value)
        except Exception:
            size = 46
        size = max(22, min(72, size))
        self.title_font_size = size
        self._save_profile()
        self._push()
        return {'ok': True, **self.ui_state()}

    def _bus_trace(self, **kwargs):
        """Forward PlaybackCommandBus diagnostics into playback_trace.log."""
        reason = str(kwargs.pop('reason', 'bus'))
        self._log_playback(reason, origin=str(kwargs.pop('origin', 'bus')), **kwargs)


    def _log_playback(self, reason, origin='local', **extra):
        """Trace playback commands for diagnosing unexpected pause/play.

        Display clock is HH:MM:SS only — always include wall_s + mono_s so two-PC
        correlation can use high-resolution local clocks (and clock skew separately).
        """
        wall_s = time.time()
        mono_s = time.monotonic()
        entry = {
            'time': now_clock(),
            'wall_s': wall_s,
            'mono_s': mono_s,
            'phase': self._playback_phase,
            'seq': self._playback_seq,
            'lobby_seq': getattr(self, '_bus', None).lobby_seq if getattr(self, '_bus', None) else 0,
            'play_seq': int(getattr(self, '_last_applied_play_seq', 0) or 0),
            'origin': origin,
            'reason': str(reason),
        }
        for key, value in extra.items():
            try:
                entry[key] = value
            except Exception:
                pass
        self._playback_trace.append(entry)
        self._playback_trace = self._playback_trace[-120:]
        try:
            folder = os.path.dirname(self._profile_file())
            path = os.path.join(folder, 'playback_trace.log')
            line = (
                f"{entry['time']} wall={wall_s:.6f} mono={mono_s:.6f} "
                f"phase={entry['phase']} seq={entry['seq']} lobby_seq={entry['lobby_seq']} "
                f"origin={entry['origin']} reason={entry['reason']}"
            )
            for key, value in extra.items():
                line += f' {key}={value}'
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')
        except Exception:
            pass

    def _commit_play_intent(self, playing):
        """Atomically align dual-threshold tracking with the authoritative intent.

        Updating only PotPlayer (or only _global_paused) lets the monitor briefly see
        player_paused != global_paused and republish the same Play/Pause as a new
        potplayer event — the v2.0.20 host log failure mode.
        """
        playing = bool(playing)
        self._global_paused = not playing
        self._player_paused = not playing
        self._last_broadcast_playing = playing
        self._cached_status = STATUS_PLAYING if playing else STATUS_PAUSED

    def _accept_playback_seq(self, action):
        """Return False if the command is stale (older than the current start seq)."""
        try:
            seq = int((action or {}).get('seq') or 0)
        except Exception:
            seq = 0
        if seq <= 0:
            return True
        if seq < self._playback_seq:
            self._log_playback('drop_stale', origin='net', cmd_seq=seq, cmd=(action or {}).get('action'))
            return False
        if seq > self._playback_seq:
            self._playback_seq = seq
        return True


    def _next_play_cmd_seq(self):
        self._play_cmd_seq += 1
        return self._play_cmd_seq


    def _accept_play_cmd(self, action):
        """Reject older play/pause commands so rapid pause/resume keep final order."""
        if not self._accept_playback_seq(action):
            return False
        action = action or {}
        # Guest→host proposals carry no host lobby_seq/play_seq. Accept by cmd_id /
        # sent_at only; host assigns authority when relaying.
        if action.get('detected') and self.role == 'host' and not action.get('lobby_seq') and not action.get('play_seq'):
            # Guest Pause→Play: proposals carry the joiner's sent_at. Host relay of
            # Pause stamps _last_play_sent_at with *host* wall time, which is almost
            # always newer than the joiner's already-queued Play sent_at — so comparing
            # guest sent_at to _last_play_sent_at dropped the Play (host stuck paused).
            try:
                sent_at = float(action.get('sent_at') or 0.0)
            except Exception:
                sent_at = 0.0
            last_guest = float(getattr(self, '_last_guest_play_sent_at', 0.0) or 0.0)
            if sent_at > 0.0 and last_guest > 0.0 and sent_at + 0.001 < last_guest:
                self._log_playback(
                    'drop_stale_guest_play_ts',
                    origin='net',
                    sent_at=sent_at,
                    last_guest=last_guest,
                )
                return False
            cmd = PlaybackCommand.from_wire(action)
            if not cmd.kind:
                cmd.kind = KIND_PLAY if action.get('playing') else KIND_PAUSE
            # Zero lobby_seq so bus does not treat this as a stale host seq.
            cmd.lobby_seq = 0
            ok, reason = self._bus.accept(cmd)
            if not ok and reason == 'duplicate_cmd_id':
                self._log_playback('drop_play_bus', origin='net', bus_reason=reason, cmd_id=cmd.cmd_id)
                return False
            if sent_at > last_guest:
                self._last_guest_play_sent_at = sent_at
            return True

        cmd = PlaybackCommand.from_wire(action)
        if not cmd.kind:
            cmd.kind = KIND_PLAY if action.get('playing') else KIND_PAUSE
        ok, reason = self._bus.accept(cmd)
        if not ok:
            self._log_playback('drop_play_bus', origin='net', bus_reason=reason, cmd_id=cmd.cmd_id)
            return False
        try:
            play_seq = int(action.get('play_seq') or cmd.lobby_seq or 0)
        except Exception:
            play_seq = 0
        try:
            sent_at = float(action.get('sent_at') or 0.0)
        except Exception:
            sent_at = 0.0
        if play_seq > 0:
            if play_seq < self._last_applied_play_seq:
                self._log_playback(
                    'drop_stale_play',
                    origin='net',
                    play_seq=play_seq,
                    last=self._last_applied_play_seq,
                )
                return False
            self._last_applied_play_seq = max(self._last_applied_play_seq, play_seq)
            self._play_cmd_seq = max(self._play_cmd_seq, play_seq)
            if sent_at > self._last_play_sent_at:
                self._last_play_sent_at = sent_at
            self._bus.mark_applied(cmd)
            return True
        if sent_at > 0.0:
            if sent_at + 0.001 < self._last_play_sent_at:
                self._log_playback('drop_stale_play_ts', origin='net', sent_at=sent_at)
                return False
            self._last_play_sent_at = sent_at
        self._bus.mark_applied(cmd)
        return True


    def _mark_remote_play_echo(self, playing):
        """After applying a remote play/pause, ignore only that same state as an echo.

        Never shortens an active seek-protect window (v2.0.8 bus policy: max only).
        Align dual-threshold tracking immediately; keep _last_pot_state as the last
        real PotPlayer sample so lagging status can still be detected for resync.
        """
        seconds = PLAY_PAUSE_SUPPRESS_MS / 1000
        self._bus.mark_play_echo(bool(playing), seconds=seconds)
        self._echo_play_expect = bool(playing)
        self._echo_play_until = self._bus.play_echo_until
        expect_status = STATUS_PLAYING if playing else STATUS_PAUSED
        self._echo_play_confirmed = (self._last_pot_state == expect_status)
        # CRITICAL: extend suppress with max() — do not assign over seek protect.
        self._suppress_detection_until = max(
            self._suppress_detection_until,
            self._bus.suppress_until,
            self._bus.seek_protect_until,
        )
        self._commit_play_intent(playing)


    def _apply_play_state(self, desired, *, origin='local'):
        """Drive PotPlayer to the intended play/pause using status-aware 10014 toggles."""
        desired = bool(desired)
        if not hasattr(self, '_pot_cmd_lock'):
            self._pot_cmd_lock = threading.RLock()
        with self._pot_cmd_lock:
            # Commit intent before Win32 I/O so the monitor cannot race a half-updated
            # dual-threshold and republish this same action as origin=potplayer.
            self._commit_play_intent(desired)
            current = pot_status() == STATUS_PLAYING
            if desired == current:
                self._cached_status = STATUS_PLAYING if desired else STATUS_PAUSED
                self._last_pot_state = self._cached_status
                return True
            ok = pot_play() if desired else pot_pause()
            if not ok:
                return False
            # Confirm once; Mini64 can lag a beat after 10014.
            time.sleep(0.05)
            now_playing = pot_status() == STATUS_PLAYING
            if now_playing != desired:
                pot_cmd(CMD_PLAY_PAUSE)
                time.sleep(0.08)
                now_playing = pot_status() == STATUS_PLAYING
            self._cached_status = STATUS_PLAYING if now_playing else STATUS_PAUSED
            self._last_pot_state = self._cached_status
            self._log_playback(
                'apply_play_state',
                origin=origin,
                desired=desired,
                result=now_playing,
                observed=now_playing,
            )
            return now_playing == desired


    def _publish_play_state(self, playing, actor, *, source_client=None, log=True, origin='local'):
        """Broadcast an authoritative play/pause with a new lobby_seq (host) or send (guest).

        Guests must NOT invent host lobby_seq/play_seq values — those counters are
        host-authoritative. A guest sending play_seq=1 after the host is at 5 was
        rejected as stale, leaving the joiner stuck paused.
        """
        playing = bool(playing)
        origin_key = str(origin or '').lower()
        # One user action → one logical sync event. PotPlayer feedback that matches
        # the last authoritative broadcast must not mint a new play_seq.
        if (
            origin_key in ('potplayer',)
            and self._last_broadcast_playing is not None
            and bool(playing) == bool(self._last_broadcast_playing)
        ):
            self._log_playback(
                'suppress_feedback_publish',
                origin=origin,
                playing=playing,
                last_broadcast=self._last_broadcast_playing,
                decision='dedup_same_state',
            )
            self._mark_remote_play_echo(playing)
            return 0
        if self._bus.should_ignore_play_echo(playing) and origin_key in ('potplayer',):
            self._log_playback(
                'suppress_feedback_publish',
                origin=origin,
                playing=playing,
                decision='echo_window',
            )
            return 0

        cmd_id = new_cmd_id()
        now = time.time()
        self._commit_play_intent(playing)
        # Syncplay ignoringOnTheFly: protect this intentional change from stale undo.
        ignore_token = self._ignore_fly.note_local_change(paused=not playing)
        # Suppress only the matching echo of THIS publish — opposite toggles still sync.
        self._mark_remote_play_echo(playing)

        if self.role == 'host' and self.server.running:
            play_seq = self._next_play_cmd_seq()
            lobby_seq = self._bus.next_lobby_seq()
            self._last_applied_play_seq = max(self._last_applied_play_seq, play_seq)
            self._last_play_sent_at = now
            cmd = PlaybackCommand(
                kind=KIND_PLAY if playing else KIND_PAUSE,
                cmd_id=cmd_id,
                lobby_seq=lobby_seq,
                actor=safe_name(actor) or self.username,
                origin=origin,
                playing=playing,
                sent_at=now,
                programmatic=False,
            )
            ok, reason = self._bus.accept(cmd)
            if ok:
                self._bus.mark_applied(cmd)
            if log:
                if playing:
                    label = 'started the movie' if not self._first_play_seen else 'resumed the movie'
                    self._first_play_seen = True
                else:
                    label = 'paused the movie'
                if origin_key in ('huge',):
                    suffix = ' (Huge)'
                elif origin_key in ('potplayer', 'guest'):
                    suffix = ' (PotPlayer)'
                else:
                    suffix = ''
                self.append_log(
                    f'{label}{suffix}.',
                    actor=actor or self.username,
                    broadcast=True,
                    source_client=source_client,
                )
                self.mark_paused_user(actor or self.username, not playing)
            payload = {
                'action': 'set_playing',
                'playing': playing,
                'play_seq': play_seq,
                'lobby_seq': lobby_seq,
                'cmd_id': cmd_id,
                'seq': self._playback_seq,
                'sent_at': now,
                'remote': True,
                'origin': origin,
                'kind': KIND_PLAY if playing else KIND_PAUSE,
                'ignoring_on_the_fly': {'server': int(ignore_token)},
            }
            self._log_playback(
                'publish_play',
                origin=origin,
                playing=playing,
                play_seq=play_seq,
                lobby_seq=lobby_seq,
                cmd_id=cmd_id,
                accept=reason,
                ignore=ignore_token,
                decision='broadcast',
            )
            self.server.broadcast({'type': 'command', 'action': payload}, exclude=source_client)
            self.broadcast_state(include_position=False)
            return play_seq

        if self.role == 'client' and self.client.running:
            # Proposal only — host assigns lobby_seq/play_seq on relay.
            if log:
                # Guest does not write Live Log; host logs on relay.
                pass
            self._commit_play_intent(playing)
            payload = {
                'action': 'set_playing',
                'playing': playing,
                'cmd_id': cmd_id,
                'seq': self._playback_seq,
                'sent_at': now,
                'detected': True,
                'origin': origin,
                'kind': KIND_PLAY if playing else KIND_PAUSE,
                'ignoring_on_the_fly': {'client': int(ignore_token)},
            }
            self._log_playback(
                'propose_play',
                origin=origin,
                playing=playing,
                cmd_id=cmd_id,
                decision='propose',
            )
            self.client.send({'type': 'command', 'action': payload})
            return 0
        return 0


    def _run_wpf_script(self, script_name, script_body, args, timeout=45):
        folder = os.path.dirname(self._profile_file())
        os.makedirs(folder, exist_ok=True)
        script_path = os.path.join(folder, script_name)
        try:
            with open(script_path, 'w', encoding='utf-8-sig') as fh:
                fh.write(script_body)
            proc = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-STA',
                    '-File', script_path, *[str(a) for a in args],
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
            )
            return proc
        except Exception as exc:
            class _Failed:
                returncode = 2
                stdout = ''
                stderr = str(exc)
            return _Failed()

    def _native_edit_preview(self, source, max_edge=1920):
        """Decode image for the editor. Colors are preserved; no filters applied."""
        if os.name != 'nt':
            return False, 'Windows-native image decoding is required.', {}
        out = os.path.join(os.path.dirname(self._profile_file()), '_edit_preview.jpg')
        ps = r'''Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$srcPath = $args[0]
$outPath = $args[1]
$maxEdge = [int]$args[2]
$stream = $null
$out = $null
try {
    $stream = New-Object System.IO.FileStream($srcPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $decoder = [System.Windows.Media.Imaging.BitmapDecoder]::Create(
        $stream,
        [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
        [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
    )
    $frame = $decoder.Frames[0]
    $w = [int]$frame.PixelWidth
    $h = [int]$frame.PixelHeight
    if ($w -le 0 -or $h -le 0) { throw 'The picture has no usable dimensions.' }
    $scale = 1.0
    $longest = [Math]::Max($w, $h)
    if ($longest -gt $maxEdge) { $scale = $maxEdge / [double]$longest }
    if ($scale -lt 1.0) {
        $transform = New-Object -TypeName System.Windows.Media.ScaleTransform -ArgumentList @($scale,$scale)
        $scaled = New-Object -TypeName System.Windows.Media.Imaging.TransformedBitmap -ArgumentList @($frame,$transform)
    } else {
        $scaled = $frame
    }
    $encoder = New-Object System.Windows.Media.Imaging.JpegBitmapEncoder
    $encoder.QualityLevel = 92
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($scaled))
    $out = New-Object System.IO.FileStream($outPath, [System.IO.FileMode]::Create)
    $encoder.Save($out)
    $out.Dispose(); $out = $null
    Write-Output ("{0}|{1}|{2}|{3}" -f $w, $h, $scaled.PixelWidth, $scaled.PixelHeight)
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
} finally {
    if ($out) { $out.Dispose() }
    if ($stream) { $stream.Dispose() }
}
'''
        proc = self._run_wpf_script('_edit_preview_wpf.ps1', ps, [os.path.abspath(source), out, int(max_edge)])
        if proc.returncode != 0 or not os.path.isfile(out):
            return False, (proc.stderr or proc.stdout or 'Could not open that picture.').strip(), {}
        parts = (proc.stdout or '').strip().split('|')
        try:
            sw, sh, pw, ph = [int(float(p)) for p in parts[:4]]
        except Exception:
            return False, 'Could not read picture dimensions.', {}
        data = self._png_to_b64(out)
        if not data:
            return False, 'Could not prepare picture preview.', {}
        return True, '', {
            'preview_b64': data,
            'preview_width': pw,
            'preview_height': ph,
            'source_width': sw,
            'source_height': sh,
        }

    def _native_crop_avatar(self, source, output_png, x, y, w, h, out_size=512):
        """Crop the chosen square region and save a square PNG (UI clips to a circle)."""
        if os.name != 'nt':
            return False, 'Windows-native image decoding is required for profile pictures.'
        ps = r'''Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$srcPath = $args[0]
$outPath = $args[1]
$x = [int][Math]::Floor([double]$args[2])
$y = [int][Math]::Floor([double]$args[3])
$cw = [int][Math]::Ceiling([double]$args[4])
$ch = [int][Math]::Ceiling([double]$args[5])
$size = [int]$args[6]
$stream = $null
$out = $null
try {
    $stream = New-Object System.IO.FileStream($srcPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $decoder = [System.Windows.Media.Imaging.BitmapDecoder]::Create(
        $stream,
        [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
        [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
    )
    $frame = $decoder.Frames[0]
    $fw = [int]$frame.PixelWidth
    $fh = [int]$frame.PixelHeight
    if ($fw -le 0 -or $fh -le 0) { throw 'The picture has no usable dimensions.' }
    $side = [Math]::Max(1, [Math]::Min($cw, $ch))
    if ($x -lt 0) { $x = 0 }
    if ($y -lt 0) { $y = 0 }
    if ($x + $side -gt $fw) { $x = [Math]::Max(0, $fw - $side) }
    if ($y + $side -gt $fh) { $y = [Math]::Max(0, $fh - $side) }
    if ($x + $side -gt $fw) { $side = $fw - $x }
    if ($y + $side -gt $fh) { $side = $fh - $y }
    $rect = New-Object -TypeName System.Windows.Int32Rect -ArgumentList @($x,$y,[int]$side,[int]$side)
    $crop = New-Object -TypeName System.Windows.Media.Imaging.CroppedBitmap -ArgumentList @($frame,$rect)
    $scale = $size / [double]$side
    $transform = New-Object -TypeName System.Windows.Media.ScaleTransform -ArgumentList @($scale,$scale)
    $scaled = New-Object -TypeName System.Windows.Media.Imaging.TransformedBitmap -ArgumentList @($crop,$transform)
    $encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($scaled))
    $out = New-Object -TypeName System.IO.FileStream -ArgumentList @($outPath,[System.IO.FileMode]::Create,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
    $encoder.Save($out)
    $out.Dispose(); $out = $null
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
} finally {
    if ($out) { $out.Dispose() }
    if ($stream) { $stream.Dispose() }
}
'''
        proc = self._run_wpf_script(
            '_avatar_crop_wpf.ps1', ps,
            [os.path.abspath(source), os.path.abspath(output_png), x, y, w, h, int(out_size)],
        )
        if proc.returncode == 0 and os.path.isfile(output_png) and os.path.getsize(output_png) > 0:
            return True, ''
        return False, (proc.stderr or proc.stdout or 'Could not save that picture.').strip()

    def _native_crop_banner(self, source, output_jpg, x, y, w, h, max_width=1600):
        """Crop the chosen banner region and save JPEG with original colors (no filters)."""
        if os.name != 'nt':
            return False, 'Windows-native image decoding is required for banners.'
        ps = r'''Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$srcPath = $args[0]
$outPath = $args[1]
$x = [int][Math]::Floor([double]$args[2])
$y = [int][Math]::Floor([double]$args[3])
$cw = [int][Math]::Ceiling([double]$args[4])
$ch = [int][Math]::Ceiling([double]$args[5])
$maxW = [int]$args[6]
$stream = $null
$out = $null
try {
    $stream = New-Object System.IO.FileStream($srcPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $decoder = [System.Windows.Media.Imaging.BitmapDecoder]::Create(
        $stream,
        [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
        [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
    )
    $frame = $decoder.Frames[0]
    $fw = [int]$frame.PixelWidth
    $fh = [int]$frame.PixelHeight
    if ($fw -le 0 -or $fh -le 0) { throw 'The picture has no usable dimensions.' }
    if ($x -lt 0) { $x = 0 }
    if ($y -lt 0) { $y = 0 }
    if ($cw -lt 1) { $cw = 1 }
    if ($ch -lt 1) { $ch = 1 }
    if ($x + $cw -gt $fw) { $cw = $fw - $x }
    if ($y + $ch -gt $fh) { $ch = $fh - $y }
    if ($cw -lt 1 -or $ch -lt 1) { throw 'Crop region is empty.' }
    $rect = New-Object -TypeName System.Windows.Int32Rect -ArgumentList @($x,$y,$cw,$ch)
    $crop = New-Object -TypeName System.Windows.Media.Imaging.CroppedBitmap -ArgumentList @($frame,$rect)
    $scale = 1.0
    if ($cw -gt $maxW) { $scale = $maxW / [double]$cw }
    if ($scale -lt 1.0) {
        $transform = New-Object -TypeName System.Windows.Media.ScaleTransform -ArgumentList @($scale,$scale)
        $scaled = New-Object -TypeName System.Windows.Media.Imaging.TransformedBitmap -ArgumentList @($crop,$transform)
    } else {
        $scaled = $crop
    }
    $encoder = New-Object System.Windows.Media.Imaging.JpegBitmapEncoder
    $encoder.QualityLevel = 92
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($scaled))
    $out = New-Object System.IO.FileStream($outPath, [System.IO.FileMode]::Create)
    $encoder.Save($out)
    $out.Dispose(); $out = $null
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
} finally {
    if ($out) { $out.Dispose() }
    if ($stream) { $stream.Dispose() }
}
'''
        proc = self._run_wpf_script(
            '_banner_crop_wpf.ps1', ps,
            [os.path.abspath(source), os.path.abspath(output_jpg), x, y, w, h, int(max_width)],
        )
        if proc.returncode == 0 and os.path.isfile(output_jpg) and os.path.getsize(output_jpg) > 0:
            return True, ''
        return False, (proc.stderr or proc.stdout or 'Could not save that picture.').strip()

    def _avatar_temp_dir(self):
        folder = os.path.join(os.path.dirname(self._profile_file()), 'avatars')
        os.makedirs(folder, exist_ok=True)
        return folder


    def _png_to_b64(self, path):
        try:
            with open(path, 'rb') as fh:
                return base64.b64encode(fh.read()).decode('ascii')
        except Exception:
            return ''


    # Lobby wire budget: never mid-truncate base64 (that yields a black/broken circle).
    LOBBY_AVATAR_MAX_CHARS = 220_000

    def _sanitize_remote_avatar(self, raw):
        """Accept transferable avatar bytes only; reject corrupt/truncated payloads."""
        s = str(raw or '').strip()
        if not s:
            return ''
        mime = ''
        payload = s
        if s.startswith('data:'):
            comma = s.find(',')
            if comma < 0:
                return ''
            mime = s[5:comma].split(';')[0].strip().lower()
            payload = s[comma + 1:].strip()
        if len(payload) > self.LOBBY_AVATAR_MAX_CHARS:
            return ''
        try:
            blob = base64.b64decode(payload, validate=True)
        except Exception:
            try:
                blob = base64.b64decode(payload)
            except Exception:
                return ''
        if len(blob) < 24:
            return ''
        sniffed = gif_image.sniff_image_mime(blob)
        if sniffed not in ('image/png', 'image/jpeg', 'image/gif'):
            return ''
        if mime and mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/jpg'):
            return ''
        # Prefer a data URL so the UI keeps the correct mime (esp. GIF animation).
        return gif_image.data_url(sniffed, base64.b64encode(blob).decode('ascii'))

    def _make_lobby_avatar_b64(self, source_path_or_b64):
        """Compact avatar for TCP hello/room_state (JPEG static or GIF animated)."""
        try:
            src_path = ''
            if isinstance(source_path_or_b64, str) and os.path.isfile(source_path_or_b64):
                src_path = os.path.abspath(source_path_or_b64)
            elif isinstance(source_path_or_b64, str) and source_path_or_b64.strip():
                _mime, blob = gif_image.strip_data_url(source_path_or_b64.strip())
                if not blob:
                    raw = source_path_or_b64.strip()
                    if raw.startswith('data:'):
                        raw = raw.split(',', 1)[-1]
                    blob = base64.b64decode(raw)
                tmp = os.path.join(self._avatar_temp_dir(), 'lobby_src.bin')
                with open(tmp, 'wb') as fh:
                    fh.write(blob)
                src_path = tmp
            if not src_path or not os.path.isfile(src_path):
                return ''

            # Animated / GIF sources → compact GIF thumb (keeps animation across PCs).
            if gif_image.is_gif_file(src_path):
                b64 = gif_image.make_lobby_gif_b64(src_path)
                if b64:
                    return self._sanitize_remote_avatar(gif_image.data_url('image/gif', b64))
                # Fall through to static JPEG if GIF lobby encode failed.

            out = os.path.join(self._avatar_temp_dir(), 'avatar_lobby.jpg')
            if os.name != 'nt':
                b64 = self._png_to_b64(src_path)
                return self._sanitize_remote_avatar(b64)
            ps = r'''Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$srcPath = $args[0]
$outPath = $args[1]
$size = [int]$args[2]
$bytes = [System.IO.File]::ReadAllBytes($srcPath)
$ms = New-Object System.IO.MemoryStream(,$bytes)
$decoder = [System.Windows.Media.Imaging.BitmapDecoder]::Create(
  $ms,
  [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
  [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
)
$frame = $decoder.Frames[0]
$tb = New-Object System.Windows.Media.Imaging.TransformedBitmap
$scale = [Math]::Min($size / $frame.PixelWidth, $size / $frame.PixelHeight)
$tb.BeginInit()
$tb.Source = $frame
$tb.Transform = New-Object System.Windows.Media.ScaleTransform($scale, $scale)
$tb.EndInit()
$enc = New-Object System.Windows.Media.Imaging.JpegBitmapEncoder
$enc.QualityLevel = 82
$enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($tb))
$fs = [System.IO.File]::Open($outPath, [System.IO.FileMode]::Create)
try { $enc.Save($fs) } finally { $fs.Dispose(); $ms.Dispose() }
'''
            proc = self._run_wpf_script('_avatar_lobby_jpeg.ps1', ps, [src_path, out, 128], timeout=20)
            if proc.returncode != 0 or not os.path.isfile(out):
                b64 = self._png_to_b64(src_path)
                return self._sanitize_remote_avatar(b64)
            return self._sanitize_remote_avatar(self._file_to_display_b64(out))
        except Exception:
            return ''

    def _avatar_for_wire(self):
        """Avatar payload for hello / member list sync (never a local file path)."""
        cached = getattr(self, '_avatar_lobby_b64', '') or ''
        if cached:
            return cached
        gif_disk = os.path.join(self._avatar_temp_dir(), 'avatar_final.gif')
        png_disk = os.path.join(self._avatar_temp_dir(), 'avatar_final.png')
        if self.avatar_b64:
            cached = self._make_lobby_avatar_b64(self.avatar_b64)
        elif os.path.isfile(gif_disk):
            cached = self._make_lobby_avatar_b64(gif_disk)
        elif os.path.isfile(png_disk):
            cached = self._make_lobby_avatar_b64(png_disk)
        self._avatar_lobby_b64 = cached or ''
        return self._avatar_lobby_b64

    def _profile_file(self):
        # Multi-process tests set HUGE_PROFILE_DIR so each peer has isolated state.
        override = os.environ.get('HUGE_PROFILE_DIR', '').strip()
        if override:
            os.makedirs(override, exist_ok=True)
            return os.path.join(override, 'profile.json')
        if os.name == 'nt':
            base = os.environ.get('APPDATA') or os.path.expanduser('~')
        else:
            base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
        folder = os.path.join(base, 'HugeWatchParty')
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, 'profile.json')


    def test_inject_loaded_movie(self, path='test_movie.mp4', duration_ms=3_600_000):
        """Headless/fake-pot only: mark media loaded & paused at 0 without launching PotPlayer."""
        if os.environ.get('HUGE_FAKE_POT') != '1':
            raise RuntimeError('test_inject_loaded_movie requires HUGE_FAKE_POT=1')
        try:
            from engine.fake_pot import get_fake
            get_fake().load(duration_ms)
        except Exception:
            pass
        pot_set_current_sync(0, timeout=1.0)
        if pot_status() == STATUS_PLAYING:
            pot_pause()
        self._finish_movie_selection(os.path.abspath(path) if path else 'test_movie.mp4')
        self._cached_duration = int(duration_ms)
        self._cached_position = 0
        self._cached_status = STATUS_PAUSED
        self._last_pot_pos = 0
        self._last_pot_state = STATUS_PAUSED
        self._playback_phase = PHASE_READY
        return True


    def _schedule_profile_save(self):
        if self.closed:
            return
        if self._profile_save_job is not None:
            try:
                self.after_cancel(self._profile_save_job)
            except Exception:
                pass
        self._profile_save_job = self.after(450, self._save_profile)


    def _save_profile(self):
        self._profile_save_job = None
        try:
            payload = {
                'name': self.get_name(),
                'avatar_b64': self.avatar_b64,
                'avatar_path': self.avatar_path,
                'avatar_mime': self.avatar_mime,
                'banner_path': self.banner_path,
                'banner_mime': self.banner_mime,
                'potplayer_path': self.potplayer_path,
                'title_font': self.title_font,
                'title_font_size': int(self.title_font_size),
                'version': 5,
            }
            target = self._profile_file()
            temp = target + '.tmp'
            with open(temp, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(temp, target)
        except Exception:
            pass


    def _valid_potplayer_path(self, path):
        if not path or not os.path.isfile(path):
            return False
        if os.name != 'nt':
            return False
        return os.path.basename(path).lower() in ('potplayermini64.exe', 'potplayer64.exe')


    def handle_local_state_transition(self, is_playing, was_playing):
        """Turn a PotPlayer keyboard/state change into an authoritative room event."""
        if is_playing == was_playing or self.role != 'host' or not self.server.running:
            return
        # Before START MOVIE, never broadcast play/pause. Local autoplay during Select
        # Movie must stay local (force-paused) or both peers briefly start then stop.
        if not self.started or self._playback_phase == PHASE_STARTING:
            return
        if (
            not is_playing
            and time.time() < self._playback_start_grace_until
        ):
            return
        # Feedback from our own Huge/remote apply must not become a second lobby event.
        if (
            self._last_broadcast_playing is not None
            and bool(is_playing) == bool(self._last_broadcast_playing)
        ):
            self._log_playback(
                'suppress_feedback_transition',
                origin='potplayer',
                playing=is_playing,
                decision='same_as_broadcast',
            )
            self._mark_remote_play_echo(bool(is_playing))
            return
        if self._bus.should_ignore_play_echo(bool(is_playing)):
            self._log_playback(
                'suppress_feedback_transition',
                origin='potplayer',
                playing=is_playing,
                decision='echo_window',
            )
            return
        # Lag / ignoringOnTheFly: do not publish a fake Play that undoes Pause.
        now = time.time()
        if (
            now < float(getattr(self, '_echo_play_until', 0) or 0)
            and self._echo_play_expect is not None
        ):
            expect_status = STATUS_PLAYING if self._echo_play_expect else STATUS_PAUSED
            # PotPlayer already matched our last command — opposite sample is a
            # genuine rapid toggle (pause→play), not settle lag.
            if self._last_pot_state == expect_status:
                self._echo_play_confirmed = True
            if (
                bool(is_playing) != bool(self._echo_play_expect)
                and not bool(getattr(self, '_echo_play_confirmed', True))
            ):
                self._apply_play_state(bool(self._echo_play_expect), origin='sync')
                self._log_playback(
                    'monitor_resync_lag',
                    origin='host',
                    expect=self._echo_play_expect,
                    sample=is_playing,
                    decision='resync',
                )
                return
        # Genuine PotPlayer keyboard/mouse toggle — differs from last broadcast.
        self._publish_play_state(
            is_playing,
            self.username,
            source_client=None,
            log=True,
            origin='potplayer',
        )


    def _normalize_log_entry(self, entry):
        if isinstance(entry, dict):
            actor = safe_name(str(entry.get('actor') or ''))
            message = self._log_message_without_actor(str(entry.get('message') or ''), actor)
            out = {
                'time': str(entry.get('time') or '00:00:00'),
                'actor': actor,
                'message': message,
            }
            try:
                lid = int(entry.get('id') or 0)
            except Exception:
                lid = 0
            if lid:
                out['id'] = lid
            return out
        raw = str(entry or '').strip()
        if raw.startswith('[') and ']' in raw:
            raw_time, raw_msg = raw[1:].split(']', 1)
            return {'time': raw_time.strip(), 'actor': '', 'message': raw_msg.strip()}
        return {'time': '00:00:00', 'actor': '', 'message': raw}


    def handle_network_message(self, msg):
        typ = msg.get('type')
        if typ == 'assigned':
            self.user_id = str(msg.get('user_id') or self.user_id)
            return
        if typ == 'room_state':
            self.role = 'client'
            self.lobby_name_value = str(msg.get('room_name') or 'Lobby')
            self.host_name_value = str(msg.get('host_name') or 'Host')
            elapsed = max(0, int(msg.get('elapsed', 0) or 0))
            now = time.time()
            self.lobby_started_at = now - elapsed if elapsed else 0.0
            was_started = bool(self.started)
            self.started = bool(msg.get('started'))
            incoming = msg.get('users') or []
            self.users = {}
            for u in incoming:
                if not isinstance(u, dict):
                    continue
                item = dict(u)
                item['avatar'] = self._sanitize_remote_avatar(item.get('avatar'))
                self.users[str(item.get('id'))] = item
            self.logs = list(msg.get('logs') or [])[-60:]
            self.display_address = self._joined_target or 'Connected'
            self.render_members()
            self.render_logs()
            pos = msg.get('position', -1)
            # Join snapshots may include a position for late joiners. Apply once as a
            # programmatic seek — never let the resulting jump be "detected" as a
            # manual scrub from this client (that caused rewind loops).
            # Never apply snapshot positions during STARTING — that would undo seek-to-zero.
            if (
                self.started
                and self._playback_phase not in (PHASE_STARTING, PHASE_LOADING)
                and isinstance(pos, (int, float))
                and pos >= 0
            ):
                target_pos = int(pos)
                self._remember_programmatic_seek(target_pos)
                pot_set_current(target_pos)
            status = int(msg.get('status', STATUS_STOPPED))
            # After START MOVIE, play/pause is command-bus only (set_playing).
            # Applying PLAYING from every room_state raced guest-originated pauses:
            # host was still PLAYING for a few ms, snapshot forced the joiner back
            # to play, then the PAUSED snapshot was ignored — joiner stuck playing.
            if self.started:
                if not was_started and status in (STATUS_PLAYING, STATUS_PAUSED):
                    # Late join / first in-progress snapshot: sync once.
                    self._set_playing(status == STATUS_PLAYING)
            elif status in (STATUS_PLAYING, STATUS_PAUSED):
                self._set_playing(False)
            self.update_ready_button()
            self.refresh_action_state()
            return
        if typ == 'users':
            incoming = msg.get('users') or []
            self.users = {}
            for u in incoming:
                if not isinstance(u, dict):
                    continue
                item = dict(u)
                item['avatar'] = self._sanitize_remote_avatar(item.get('avatar'))
                self.users[str(item.get('id'))] = item
            self.render_members()
            self.update_ready_button()
            self.refresh_action_state()
            return
        if typ == 'log':
            entry = self._normalize_log_entry(msg.get('entry', {}))
            if entry['message'] or entry['actor']:
                self.logs.append(entry)
                self.logs = self.logs[-60:]
                self.render_logs()
            return
        if typ == 'command':
            action = msg.get('action', {})
            if action:
                self.apply_authoritative_action(action, actor='')
            return
        if typ == 'kicked':
            self.client.stop()
            self.role = None
            self.movie_path = ''
            self.movie_loaded = False
            self._pot_link_active = False
            self.set_connection(str(msg.get('reason') or 'You were removed from the lobby.'), warn=True)
            self.refresh_action_state()
            self._info(APP_NAME, str(msg.get('reason') or 'You were removed from the lobby.'))
            return
        if typ == 'heartbeat_ack':
            return


    def user_records(self):
        out = []
        for user in self.users.values():
            item = dict(user)
            if item.get('host') or item.get('id') == 'host':
                item['avatar'] = self._avatar_for_wire()
            else:
                item['avatar'] = self._sanitize_remote_avatar(item.get('avatar'))
            out.append(item)
        return out


    def host_address(self):
        ips = list(self._cached_ips)
        return f'{ips[0] if ips else "YOUR-IP"}:{PORT}'


    def make_snapshot(self, include_position=True):
        # Never turn a failed PotPlayer position read (-1) into position 0.
        # A transient read failure during pause/resume must not restart the movie.
        current_position = pot_current() if include_position else -1
        safe_position = current_position if isinstance(current_position, int) and current_position >= 0 else -1
        # Prefer Huge's commanded play state over a live pot_status() read. Right after
        # play/pause/start, PotPlayer often still reports the previous status for a beat.
        cached = self._cached_status
        if cached in (STATUS_PLAYING, STATUS_PAUSED):
            status = cached
        else:
            status = pot_status()
            if status not in (STATUS_PLAYING, STATUS_PAUSED):
                status = STATUS_PAUSED if self.movie_loaded else STATUS_STOPPED
        return {
            'type': 'room_state',
            'room_name': self.lobby_name_value,
            'host_name': self.username if self.role == 'host' else getattr(self, 'host_name_value', 'Host'),
            'users': self.user_records(),
            'logs': self.logs[-40:],
            'elapsed': max(0, int(time.time() - self.lobby_started_at)) if self.lobby_started_at else 0,
            'started': self.started,
            'position': safe_position,
            'status': status,
        }


    def broadcast_state(self, include_position=True):
        if self.role == 'host' and self.server.running:
            self.server.broadcast(self.make_snapshot(include_position=include_position))


    def broadcast_users(self):
        if self.role == 'host' and self.server.running:
            self.server.broadcast({'type': 'users', 'users': self.user_records()})
        self.render_members()


    def on_guest_join(self, client):
        self.users[client['id']] = {
            'id': client['id'], 'name': client['name'], 'ready': False, 'movie_loaded': False,
            'host': False, 'avatar': self._sanitize_remote_avatar(client.get('avatar', '')),
        }
        # Ensure host entry carries a wire-safe thumb (not a huge/corrupt PNG).
        if 'host' in self.users:
            self.users['host']['avatar'] = self._avatar_for_wire()
        self.server.send(client, {'type': 'assigned', 'user_id': client['id']})
        self.server.send(client, self.make_snapshot())
        self.broadcast_users()
        self.render_members()
        self.append_log(f'{client["name"]} joined the lobby.', broadcast=True, actor=client['name'])
        self.set_connection(f'{client["name"]} joined.', good=True)


    def on_guest_left(self, client):
        user = self.users.pop(client.get('id'), None)
        if user and not client.get('kicked'):
            self.append_log(f'{user["name"]} left the lobby.', broadcast=True, actor=user['name'])
            self.broadcast_users()


    def on_guest_ready(self, client, ready, movie_loaded):
        user = self.users.get(client['id'])
        if not user:
            return
        user['movie_loaded'] = bool(movie_loaded)
        user['ready'] = bool(ready and movie_loaded)
        self.broadcast_users()
        state_text = 'ready' if user['ready'] else 'not ready'
        self.append_log(f'{user["name"]} is {state_text}.', broadcast=True, actor=user['name'])
        self.refresh_action_state()


    def on_guest_command(self, client, action):
        if not self.role == 'host':
            return
        user = self.users.get(client['id'])
        if not user:
            return
        # Must run on the UI queue — NetServer client threads used to apply
        # play/pause concurrently with the monitor and host local_action, which
        # raced Mini64/FakePot 10014 toggles and dropped guest pauses as stale.
        actor = user['name']
        self.ui(lambda a=action, act=actor, c=client: self.apply_authoritative_action(
            a, actor=act, source_client=c,
        ))


    def on_start_ack(self, client, msg):
        """Host: record a client's START MOVIE acknowledgement."""
        if self.role != 'host':
            return
        try:
            seq = int((msg or {}).get('seq') or 0)
        except Exception:
            seq = 0
        uid = str(client.get('id') or '')
        if not uid or seq <= 0:
            return
        try:
            pos = int((msg or {}).get('position', -1))
        except Exception:
            pos = -1
        try:
            status = int((msg or {}).get('status', STATUS_STOPPED))
        except Exception:
            status = STATUS_STOPPED
        ok = bool((msg or {}).get('ok'))
        self._start_acks[uid] = {
            'seq': seq,
            'ok': ok,
            'position': pos,
            'status': status,
            'error': str((msg or {}).get('error') or ''),
            'name': str(client.get('name') or 'Guest'),
            't': time.time(),
        }
        self._log_playback(
            'start_ack_recv',
            origin='host',
            seq=seq,
            user=uid,
            ok=ok,
            pos=pos,
            status=status,
        )

    # ---------- Host / Join ----------

    def parse_host(self, target):
        target = str(target or '').strip()
        if target.count(':') == 1:
            host, port_text = target.rsplit(':', 1)
            if port_text.isdigit():
                return host.strip(), int(port_text)
        return target, PORT


    def _join_thread(self, host, port):
        ok = self.client.connect(host, self.username, self._avatar_for_wire(), port)
        if ok:
            self.ui(lambda: self.set_connection('Connected • waiting for lobby state.', good=True))
        else:
            self.ui(lambda: self.set_connection('Could not connect. Check the address, firewall, or Tailscale/LAN.', warn=True))


    def connection_lost(self):
        if self.role == 'client':
            self.role = None
            self.user_id = str(uuid.uuid4())
            self.movie_path = ''
            self.movie_loaded = False
            self._pot_link_active = False
            self.set_connection('Disconnected from host.', warn=True)
            self.refresh_action_state()
            self._info(APP_NAME, 'The lobby connection was closed.')

    # ---------- Ready / movie-file flow ----------

    def _set_movie_status(self, loaded, path):
        self.movie_loaded = bool(loaded)
        self.movie_path = path if loaded else ''
        label = os.path.basename(path) if loaded and path else 'No movie selected'
        pass
        self.update_ready_button()
        self.refresh_action_state()


    def _finish_movie_selection(self, path):
        # Selecting/loading a file must never start shared playback.
        self._suppress_detection_until = time.time() + 8.0
        try:
            if pot_status() == STATUS_PLAYING:
                pot_pause()
                self._log_playback('finish_repause', origin='local')
        except Exception:
            pass

        try:
            self._set_movie_status(True, path)
            # Huge only takes ownership of PotPlayer after this explicit action.
            self._pot_link_active = True
            self._pot_ever_seen = pot_window() is not None
            self._pot_launch_grace_until = time.time() + 12.0
            self.pot_badge = 'READY'; self.pot_badge_kind = 'good'
            self._cached_status = STATUS_PAUSED
            self._last_pot_state = STATUS_PAUSED
            self._cached_position = 0
            self._last_pot_pos = 0
            self._playback_phase = PHASE_READY
            self.started = False
            self._log_playback('select_ready', origin='local', path=os.path.basename(path))

            # A newly selected file is not Ready automatically.
            if self.role == 'host':
                self.users['host']['movie_loaded'] = True
                self.users['host']['ready'] = False
                self.broadcast_users()
                self.append_log(f'{self.username} selected a movie file.', actor=self.username, broadcast=True)
            elif self.role == 'client' and self.client:
                self.users.setdefault(self.user_id, {}).update({'movie_loaded': True, 'ready': False})
                self.client.send({'type': 'ready', 'ready': False, 'movie_loaded': True})
                self.render_members()
                self.update_ready_button()

            self.set_connection(f'Movie selected: {os.path.basename(path)} • paused at 00:00 • press READY UP when ready.', good=True)
            self.refresh_action_state()
        except Exception as exc:
            self._movie_load_failed(exc)


    def _ensure_pot_paused_at_zero(self):
        """After media is loaded: pause (toggle when playing) and seek to 0."""
        self._suppress_detection_until = time.time() + 8.0
        status = pot_status()
        if status == STATUS_PLAYING:
            pot_pause()
            time.sleep(0.18)
            status = pot_status()
            self._log_playback('open_pause', origin='local', status=status)

        if status == STATUS_PLAYING:
            pot_cmd(CMD_PLAY_PAUSE)
            time.sleep(0.2)
            status = pot_status()

        if status not in (STATUS_PAUSED, STATUS_STOPPED):
            return status

        pot_set_current_sync(0, timeout=2.0)
        time.sleep(0.1)
        pos = pot_current()
        if isinstance(pos, int) and pos > START_ZERO_TOLERANCE_MS:
            pot_set_current_sync(0, timeout=2.0)
            pos = pot_current()
        if pot_status() == STATUS_PLAYING:
            pot_pause()
            time.sleep(0.15)
        status = pot_status()
        self._log_playback('open_seek_zero', origin='local', pos=pos, status=status)
        return status


    def _media_is_loaded(self):
        """True only when PotPlayer has real media (valid duration + play/pause state)."""
        status = pot_status()
        total = pot_total()
        if status not in (STATUS_PLAYING, STATUS_PAUSED):
            return False
        if not isinstance(total, int) or total <= 0:
            return False
        return True


    def _load_movie_thread(self, path):
        """Load the movie into PotPlayer and leave it paused at zero.

        v2.0.5 regression: `/add` only appends to the playlist and never opens the
        file in the viewer (empty 00:00:00 / 00:00:00). Restored `/current` which
        actually loads media, then pause immediately via Play/Pause toggle (10014)
        — separate Play/Pause IDs do nothing on PotPlayer Mini64.
        """
        finished_via_callback = False
        try:
            if os.name != 'nt':
                raise RuntimeError('Movie loading through PotPlayer requires Windows.')
            exe = find_potplayer_exe()
            if not exe:
                raise RuntimeError('PotPlayer 64-bit was not found.')

            abs_path = os.path.abspath(path)
            if not os.path.isfile(abs_path):
                raise RuntimeError(f'The selected file does not exist:\n{abs_path}')

            self._playback_phase = PHASE_LOADING
            self._log_playback('load_open', origin='local', path=abs_path)

            # Pause any current playback, then open/replace in the current instance.
            try:
                if pot_window() and pot_status() == STATUS_PLAYING:
                    pot_pause()
            except Exception:
                pass

            # /current loads the file into the existing (or new) PotPlayer window.
            # Do NOT use /add — it leaves an empty player with 00:00:00 / 00:00:00.
            subprocess.Popen([exe, abs_path, '/current'], close_fds=True)

            # Phase 1: wait for the window (fast poll — no idle 1s sleep while playing).
            deadline = time.time() + 20.0
            while time.time() < deadline:
                if self.closed:
                    return
                if pot_window():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError('PotPlayer did not open for the selected movie.')

            # Phase 2: as soon as media reports PLAYING, pause immediately.
            # Keep polling until PAUSED/PLAYING with a real duration.
            status = STATUS_STOPPED
            ready = False
            deadline = time.time() + 60.0
            while time.time() < deadline:
                if self.closed:
                    return
                if not pot_window():
                    time.sleep(0.2)
                    continue
                status = pot_status()
                if status == STATUS_PLAYING:
                    pot_pause()
                    self._log_playback('load_pause_autoplay', origin='local')
                    time.sleep(0.12)
                    status = pot_status()
                if status in (STATUS_PLAYING, STATUS_PAUSED):
                    total = pot_total()
                    if isinstance(total, int) and total > 0:
                        ready = True
                        break
                    # Status settled but duration not ready yet (demux) — keep waiting.
                time.sleep(0.08)

            if not ready:
                raise RuntimeError(
                    'PotPlayer opened but did not load the selected video.\n'
                    f'File: {abs_path}\n'
                    'Check that the path is valid and PotPlayer can play this format.'
                )

            # Phase 3: paused at zero + confirm media still loaded.
            status = self._ensure_pot_paused_at_zero()
            if status == STATUS_PLAYING:
                raise RuntimeError('PotPlayer kept playing after open; could not hold pause at 00:00.')
            if not self._media_is_loaded():
                raise RuntimeError(
                    'PotPlayer did not finish loading the movie (no valid duration).\n'
                    f'File: {abs_path}'
                )
            if status != STATUS_PAUSED:
                # STOPPED with duration is unusual; try one pause path then re-check.
                if pot_status() == STATUS_PLAYING:
                    pot_pause()
                    time.sleep(0.15)
                status = pot_status()
                if status != STATUS_PAUSED and not self._media_is_loaded():
                    raise RuntimeError('Movie loaded but PotPlayer was not paused at 00:00.')

            self._suppress_detection_until = time.time() + 8.0
            self._cached_duration = pot_total()
            path_final = abs_path

            def _complete():
                try:
                    self._finish_movie_selection(path_final)
                finally:
                    with self._movie_load_lock:
                        self._movie_loading = False

            self.ui(_complete)
            finished_via_callback = True
            return
        except Exception as exc:
            self._playback_phase = PHASE_IDLE
            self._log_playback('load_fail', origin='local', error=str(exc))
            self.ui(lambda e=exc: self._movie_load_failed(e))
            finished_via_callback = True
        finally:
            if not finished_via_callback:
                with self._movie_load_lock:
                    self._movie_loading = False


    def _movie_load_failed(self, exc):
        with self._movie_load_lock:
            self._movie_loading = False
        self._playback_phase = PHASE_IDLE
        self._suppress_detection_until = time.time() + 1.5
        self.set_connection(f'Could not load the movie in PotPlayer: {exc}', warn=True)
        self._error(APP_NAME, f'Could not load the selected movie in PotPlayer.\n\n{exc}')


    def toggle_ready(self):
        if not self.role:
            return
        if not self.movie_loaded:
            # Ready never opens the file dialog.
            self.set_connection('Select a movie file first, then press READY UP.', warn=True)
            return

        current = self.users.get(self.user_id, {}).get('ready', False)
        new_ready = not current
        if self.role == 'host':
            self.users['host']['ready'] = new_ready
            self.users['host']['movie_loaded'] = True
            self.broadcast_users()
            self.append_log(f'{self.username} is {"ready" if new_ready else "not ready"}.', actor=self.username, broadcast=True)
        else:
            self.client.send({'type': 'ready', 'ready': new_ready, 'movie_loaded': True})
        self.update_ready_button()
        self.refresh_action_state()


    def host_ready_all(self):
        if self.role != 'host':
            return
        made_ready = 0
        for user in self.users.values():
            if user.get('movie_loaded'):
                user['ready'] = True
                made_ready += 1
        self.broadcast_users()
        self.append_log(f'{self.username} made {made_ready} loaded member(s) ready.', actor=self.username, broadcast=True)
        self.refresh_action_state()


    def all_ready(self):
        return bool(self.users) and all(u.get('ready') and u.get('movie_loaded') for u in self.users.values())


    def maybe_auto_start(self, force=False):
        # Deliberately disabled: reaching Ready never starts playback.
        return


    def force_start(self):
        if self.role != 'host':
            return
        if self.started:
            return
        if not self.all_ready():
            self.set_connection('Everyone must choose a movie and be READY before starting.', warn=True)
            return
        self.start_movie()


    def start_movie(self):
        """Two-phase START: prepare (pause+seek 0+ack) → go (play). Never toggle-only."""
        if self.role != 'host' or self.started:
            return
        if not self.all_ready():
            self.set_connection('Everyone must choose a movie and be READY before starting.', warn=True)
            self.refresh_action_state()
            return
        if not self.movie_loaded or self._playback_phase == PHASE_LOADING or self._movie_loading:
            self.set_connection('Wait until the movie finishes loading before starting.', warn=True)
            self.refresh_action_state()
            return
        not_ready = [
            str(u.get('name') or 'Member')
            for u in self.users.values()
            if not (u.get('ready') and u.get('movie_loaded'))
        ]
        if not_ready:
            self.set_connection(
                f'Cannot start — still waiting on: {", ".join(not_ready)}',
                warn=True,
            )
            self.refresh_action_state()
            return

        self._playback_seq += 1
        seq = self._playback_seq
        lobby_seq = self._bus.next_lobby_seq()
        cmd_id = new_cmd_id()
        self._start_cmd_id = cmd_id
        self._playback_phase = PHASE_STARTING
        self._bus.set_phase(PHASE_STARTING)
        # started=True during STARTING so monitors do not force-pause; detection gated by phase.
        self.started = True
        self._first_play_seen = True
        self._start_acks = {}
        self._start_barrier_seq = seq
        with self._skip_lock:
            self._skip_queue.clear()
        now = time.time()
        self._bus.mark_start_grace(START_PLAYBACK_GRACE_MS / 1000)
        self._playback_start_grace_until = self._bus.start_grace_until
        self._suppress_detection_until = max(
            self._suppress_detection_until, self._bus.suppress_until,
        )
        self._echo_play_expect = True
        self._echo_play_until = self._bus.play_echo_until
        self._log_playback(
            'start_prepare_command',
            origin='host',
            seq=seq,
            lobby_seq=lobby_seq,
            cmd_id=cmd_id,
        )

        # Phase A: everyone pauses and seeks to absolute zero, then acks.
        self.server.broadcast({
            'type': 'command',
            'action': {
                'action': 'start_movie',
                'kind': KIND_START_PREPARE,
                'seq': seq,
                'lobby_seq': lobby_seq,
                'cmd_id': cmd_id,
                'seek_ms': 0,
                'phase': 'prepare',
                'origin': ORIGIN_START,
                'sent_at': now,
            },
        })

        ok, err = self._run_start_prepare(seq, origin='host')
        if not ok:
            self._playback_phase = PHASE_READY
            self._bus.set_phase(PHASE_READY)
            self.started = False
            self._log_playback('start_prepare_failed', origin='host', error=err, cmd_id=cmd_id)
            self.set_connection(err or 'Could not seek to 00:00:00.', warn=True)
            self.refresh_action_state()
            return

        # Record host as ready-at-zero.
        self._start_acks['host'] = {
            'seq': seq, 'ok': True, 'position': 0, 'status': STATUS_PAUSED,
            'name': self.username, 't': time.time(), 'phase': 'prepare',
        }

        barrier_ok, missing = self._wait_start_acks(seq, timeout=START_ACK_TIMEOUT_S)
        if not barrier_ok:
            names = ', '.join(missing) if missing else 'guest(s)'
            self._playback_phase = PHASE_READY
            self._bus.set_phase(PHASE_READY)
            self.started = False
            self._log_playback('start_barrier_failed', origin='host', seq=seq, missing=missing)
            self.set_connection(
                f'Cannot start — not everyone reached 00:00:00: {names}',
                warn=True,
            )
            self.refresh_action_state()
            return

        # Phase B: coordinated play from zero using a *relative* delay so peers do
        # not each invent a start time from skewed wall clocks (host log t_play vs
        # client t_play differed by ~1.5s when go_at was absolute host time.time()).
        go_delay_ms = int(START_GO_DELAY_MS)
        go_at = time.time() + (go_delay_ms / 1000.0)  # legacy hint only
        go_seq = self._bus.next_lobby_seq()
        go_id = new_cmd_id()
        self.server.broadcast({
            'type': 'command',
            'action': {
                'action': 'start_movie',
                'kind': KIND_START_GO,
                'seq': seq,
                'lobby_seq': go_seq,
                'cmd_id': go_id,
                'seek_ms': 0,
                'phase': 'go',
                'go': True,
                'go_at': go_at,
                'go_delay_ms': go_delay_ms,
                'origin': ORIGIN_START,
                'sent_at': time.time(),
            },
        })
        ok, err = self._run_start_go(
            seq, origin='host', go_at=go_at, go_delay_ms=go_delay_ms,
        )
        if not ok:
            self._playback_phase = PHASE_READY
            self._bus.set_phase(PHASE_READY)
            self.started = False
            self._log_playback('start_go_failed', origin='host', error=err)
            self.set_connection(err or 'Could not start playback from 00:00:00.', warn=True)
            self.refresh_action_state()
            return

        self.set_connection('Movie started from 00:00:00 on every player.', good=True)
        self.append_log(
            f'{self.username} started the movie from 00:00:00.',
            actor=self.username,
            broadcast=True,
        )
        self.mark_paused_user(self.username, False)
        self.broadcast_users()
        self.broadcast_state(include_position=False)
        self.refresh_action_state()


    def _wait_start_acks(self, seq, timeout=START_ACK_TIMEOUT_S):
        """Host barrier: every connected guest must ack this start seq."""
        expected = [
            str(uid)
            for uid, user in self.users.items()
            if uid != 'host' and not user.get('host')
        ]
        if not expected:
            return True, []
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if self.closed or self._playback_seq != seq:
                break
            done = True
            missing = []
            for uid in expected:
                ack = self._start_acks.get(uid) or {}
                if ack.get('seq') == seq and ack.get('ok'):
                    continue
                done = False
                name = str((self.users.get(uid) or {}).get('name') or ack.get('name') or uid)
                if ack.get('seq') == seq and not ack.get('ok'):
                    missing.append(f"{name} (failed)")
                else:
                    missing.append(name)
            if done:
                return True, []
            time.sleep(0.05)
        missing = []
        for uid in expected:
            ack = self._start_acks.get(uid) or {}
            if ack.get('seq') == seq and ack.get('ok'):
                continue
            name = str((self.users.get(uid) or {}).get('name') or ack.get('name') or uid)
            missing.append(name)
        return False, missing


    def _verify_at_zero(self, origin='local', seq=0):
        """Seek to absolute 0 and confirm PotPlayer reports near-zero position."""
        pos = -1
        for attempt in range(10):
            if seq and seq < self._playback_seq:
                return False, -1, 'A newer start command replaced this one.'
            pot_set_current_sync(0, timeout=2.0)
            time.sleep(0.08 if attempt else 0.12)
            pos = pot_current()
            self._log_playback(
                'start_seek_zero',
                origin=origin,
                pos=pos,
                seq=seq,
                attempt=attempt,
            )
            if isinstance(pos, int) and 0 <= pos <= START_ZERO_TOLERANCE_MS:
                self._remember_programmatic_seek(0)
                self._cached_position = 0
                self._last_pot_pos = 0
                return True, pos, ''
        return False, pos, 'Could not seek PotPlayer to 00:00:00.'


    def _run_start_prepare(self, seq, origin='local'):
        """Phase A: pause + seek absolute 0 + verify. Do not play yet."""
        if seq < self._playback_seq and origin != 'host':
            # Host already bumped seq; clients must match.
            pass
        self._playback_seq = max(self._playback_seq, seq)
        self._playback_phase = PHASE_STARTING
        self._bus.set_phase(PHASE_STARTING)
        self._bus.mark_start_grace(START_PLAYBACK_GRACE_MS / 1000)
        self._playback_start_grace_until = self._bus.start_grace_until
        self._suppress_detection_until = max(
            self._suppress_detection_until, self._bus.suppress_until,
        )
        self._echo_play_expect = True
        self._echo_play_until = self._bus.play_echo_until
        with self._skip_lock:
            self._skip_queue.clear()
        self._log_playback('start_prepare', origin=origin, seq=seq)

        if not pot_window():
            return False, 'PotPlayer is not open. Select the movie again, then press START MOVIE.'
        if not self._media_is_loaded() and not self.movie_loaded:
            return False, 'Movie is not loaded in PotPlayer.'

        status = pot_status()
        if status == STATUS_PLAYING:
            pot_pause()
            time.sleep(0.1)
            status = pot_status()
            if status == STATUS_PLAYING:
                pot_cmd(CMD_PLAY_PAUSE)
                time.sleep(0.1)
                status = pot_status()
        self._log_playback('start_paused', origin=origin, status=status, seq=seq)

        ok_zero, pos, err = self._verify_at_zero(origin=origin, seq=seq)
        if not ok_zero:
            return False, err or 'Could not seek to 00:00:00.'

        # Stay paused at zero until start_go.
        if pot_status() == STATUS_PLAYING:
            pot_pause()
        self._cached_status = STATUS_PAUSED
        self._last_pot_state = STATUS_PAUSED
        self._cached_position = 0
        self._last_pot_pos = 0
        self._last_pos_sample_time = time.time()
        self._expected_position = 0
        self._expected_position_until = time.time() + 2.0
        self._programmatic_position_until = self._expected_position_until
        self._log_playback('start_ready_at_zero', origin=origin, seq=seq, pos=pos)
        return True, ''


    def _run_start_go(self, seq, origin='local', go_at=0.0, go_delay_ms=None):
        """Phase B: play from verified zero after the prepare barrier."""
        if seq < self._playback_seq and origin != 'host':
            if seq < self._playback_seq:
                return False, 'A newer start command replaced this one.'
        self._playback_seq = max(self._playback_seq, seq)
        self._playback_phase = PHASE_STARTING
        self._bus.set_phase(PHASE_STARTING)

        # Prefer relative go_delay_ms (same on every peer). Absolute go_at is only a
        # legacy fallback and must not skip the wait when clock skew makes delay>0.5s.
        wait_s = None
        skew_s = None
        if go_delay_ms is not None:
            try:
                wait_s = max(0.0, min(1.0, float(go_delay_ms) / 1000.0))
            except Exception:
                wait_s = START_GO_DELAY_MS / 1000.0
        elif go_at:
            delay = float(go_at) - time.time()
            skew_s = delay
            if delay > 1.0:
                # Peer clock far behind host: do not wait the full skew.
                wait_s = START_GO_DELAY_MS / 1000.0
            elif delay > 0:
                wait_s = min(delay, 1.0)
            else:
                # Peer clock ahead / message late: start immediately.
                wait_s = 0.0
        if wait_s and wait_s > 0:
            deadline = time.monotonic() + wait_s
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                time.sleep(min(left, 0.05))

        # Re-confirm near zero before play (someone may have sought during wait).
        pos = pot_current()
        if isinstance(pos, int) and pos > START_ZERO_TOLERANCE_MS:
            ok_zero, pos, err = self._verify_at_zero(origin=origin, seq=seq)
            if not ok_zero:
                return False, err or 'Lost zero position before play.'

        t_play = time.time()
        t_play_mono = time.monotonic()
        if not pot_play():
            pot_cmd(CMD_PLAY_PAUSE)
        time.sleep(0.15)
        status = pot_status()
        if status != STATUS_PLAYING:
            pot_cmd(CMD_PLAY_PAUSE)
            time.sleep(0.2)
            status = pot_status()
        if status != STATUS_PLAYING:
            self._log_playback('start_go_failed', origin=origin, status=status, seq=seq)
            return False, 'PotPlayer did not begin playback after START MOVIE.'

        pos_after = pot_current()
        if isinstance(pos_after, int) and pos_after > START_PLAY_POS_MAX_MS:
            pot_pause()
            time.sleep(0.08)
            ok_zero, pos, err = self._verify_at_zero(origin=origin, seq=seq)
            if not ok_zero:
                return False, err or 'Started but position was not at 00:00:00.'
            pot_play()
            time.sleep(0.15)
            status = pot_status()
            pos_after = pot_current()
            if status != STATUS_PLAYING:
                return False, 'PotPlayer did not begin playback after START MOVIE.'
            if isinstance(pos_after, int) and pos_after > START_PLAY_POS_MAX_MS:
                return False, f'PotPlayer started at {pos_after}ms instead of 00:00:00.'

        self._cached_status = STATUS_PLAYING
        self._last_pot_state = STATUS_PLAYING
        self._commit_play_intent(True)
        zero_pos = int(pos_after) if isinstance(pos_after, int) and pos_after >= 0 else 0
        self._global_position_ms = zero_pos
        self._player_position_ms = zero_pos
        self._ignore_fly.clear()
        self._cached_position = 0
        self._last_pot_pos = 0
        self._last_pos_sample_time = time.time()
        self._expected_position = -1
        self._expected_position_until = 0.0
        self._programmatic_position_until = time.time() + 0.4
        self._mark_remote_play_echo(True)
        self._playback_phase = PHASE_PLAYING
        self._bus.set_phase(PHASE_PLAYING)
        self.started = True
        self._play_cmd_seq += 1
        self._last_applied_play_seq = self._play_cmd_seq
        self._bus.lobby_seq = max(self._bus.lobby_seq, self._bus.next_lobby_seq())
        self._log_playback(
            'start_playing',
            origin=origin,
            seq=seq,
            status=status,
            pos=pos_after,
            t_play=t_play,
            t_play_mono=t_play_mono,
            go_delay_ms=go_delay_ms if go_delay_ms is not None else '',
            go_wait_s=wait_s if wait_s is not None else '',
            go_at_skew_s=skew_s if skew_s is not None else '',
            decision='start_go',
        )
        return True, ''


    def _run_authoritative_start(self, seq, origin='local'):
        """Legacy single-shot entry: prepare then go (used by older peers)."""
        ok, err = self._run_start_prepare(seq, origin=origin)
        if not ok:
            return False, err
        return self._run_start_go(seq, origin=origin, go_at=0.0)


    def kick_user(self, user_id):
        if self.role != 'host' or user_id == 'host':
            return
        user = self.users.get(user_id)
        client = self.server.find(user_id)
        if not user or not client:
            return
        name = user['name']
        client['kicked'] = True
        self.users.pop(user_id, None)
        self.append_log(f'{name} was kicked by {self.username}.', actor=self.username, broadcast=True)
        self.broadcast_users()
        self.server.kick(client)

    # ---------- Shared actions / PotPlayer ----------

    def local_action(self, action):
        if not self.role:
            self.set_connection('Create or join a lobby first.', warn=True)
            return
        if action == 'fullscreen':
            pot_cmd(CMD_FULLSCREEN)
            return
        if action == 'toggle_pause':
            if not self.started or self._playback_phase == PHASE_STARTING:
                self.set_connection(
                    'Press START MOVIE first — playback is locked until the lobby starts.',
                    warn=True,
                )
                return

            def _toggle():
                # Compute desired state locally. Guests must NOT send raw toggle_pause —
                # the host would toggle based on the host's PotPlayer, not the guest's intent.
                before = pot_status()
                if before == STATUS_STOPPED and not pot_window():
                    self.set_connection('PotPlayer is not available.', warn=True)
                    return False
                desired = before != STATUS_PLAYING
                applied = self._apply_play_state(desired, origin='huge')
                if not applied:
                    # Still publish/propose intent — a raced status read must not swallow
                    # the user's Huge button.
                    self._log_playback('apply_play_retry_publish', origin='huge', desired=desired)
                self._publish_play_state(
                    desired, self.username, source_client=None, log=True, origin='huge',
                )
                return True

            # Serialize with guest commands / monitor (same UI queue).
            self.ui_call(_toggle)
            return

        # Huge skip buttons (±5s / ±30s): always enqueue so rapid clicks stack and
        # are never dropped by a busy PotPlayer read, coalesce race, or stale position.
        if action.startswith('seek_'):
            try:
                delta = int(action.split('_', 1)[1])
            except Exception:
                return
            self._enqueue_user_skip(delta)
            return

        payload = {'action': action, 'source': 'huge', 'ts': time.time()}
        if self.role == 'host':
            self.apply_authoritative_action(payload, actor=self.username)
        else:
            self.client.send({'type': 'command', 'action': payload})


    def apply_authoritative_action(self, action, actor, source_client=None):
        name = action.get('action') or action.get('name')
        if name == 'toggle_pause':
            if self._playback_phase == PHASE_STARTING:
                return
            # Decide the desired state from the current status, then use
            # status-aware 10014 toggles so we never invert an already-correct player.
            before = pot_status()
            if before == STATUS_STOPPED and not pot_window():
                self.set_connection('PotPlayer is not available.', warn=True)
                return
            desired = before != STATUS_PLAYING
            if not self._apply_play_state(desired, origin='huge'):
                self.set_connection('PotPlayer is not available.', warn=True)
                return
            self._publish_play_state(
                desired,
                actor,
                source_client=source_client,
                log=True,
                origin='huge',
            )
        elif name.startswith('seek_'):
            # Network/legacy seek_* actions use the same reliable skip queue.
            try:
                delta = int(name.split('_', 1)[1])
            except Exception:
                return
            self._enqueue_user_skip(delta, actor=actor, source_client=source_client)
        elif name == 'seek_forward_delta':
            # Forward-only relative synchronization. Apply the exact seek delta to the
            # local PotPlayer position at receive time. This path deliberately avoids
            # using the sender's absolute timestamp and avoids network-age compensation.
            try:
                delta = int(action.get('delta', 0))
            except Exception:
                return
            if delta <= 0:
                return
            before_pos = self._position_for_relative_seek()
            if before_pos < 0:
                return
            new_pos = max(0, before_pos + delta)
            self._remember_programmatic_seek(new_pos)
            pot_set_current(new_pos)
        elif name == 'set_position':
            if self._playback_phase == PHASE_STARTING:
                self._log_playback('ignore_seek_during_start', origin='net')
                return
            before_pos = self._position_for_relative_seek()
            try:
                pos = int(action.get('position', before_pos))
            except Exception:
                return

            delta = 0
            if action.get('detected'):
                try:
                    delta = int(action.get('delta', 0))
                except Exception:
                    delta = 0

                # Timeline scrub / PotPlayer keyboard seek: sender absolute position
                # is authoritative (do not re-add delta on the receiver — that
                # compounds drift). Huge ±5/±30 buttons are relative: apply delta
                # on the host's current position so a slightly lagging guest base
                # cannot rewind the lobby when skips are interleaved.
                kind = str(action.get('kind') or '')
                origin = str(action.get('origin') or '').lower()
                is_huge_rel = (
                    kind == KIND_SKIP_REL
                    or origin in (ORIGIN_HUGE, 'huge')
                ) and delta != 0
                if is_huge_rel and self.role == 'host' and before_pos >= 0:
                    pos = max(0, int(before_pos) + int(delta))
                else:
                    pos = max(0, pos)

            # Remote/network seeks must not carry a stale playback position rewind.
            # Apply the absolute target once through the serialized PotPlayer worker.
            # Ignore an echo of our own seek (including an older stacked skip).
            if action.get('remote') and self._is_echo_of_local_seek(pos):
                return

            # Guest monitor often re-reports the position we just pushed as a new
            # "detected" scrub (keyframe settle). Drop those echoes — they cause
            # the host movie to jump backward repeatedly.
            if action.get('detected'):
                try:
                    requested_check = int(action.get('requested_delta', delta) or 0)
                except Exception:
                    requested_check = int(delta) if delta else 0
                if self.role == 'host' and self._is_echo_of_recent_remote_seek(pos):
                    return
                try:
                    local_jump = (pos - int(before_pos)) if before_pos >= 0 else requested_check
                except Exception:
                    local_jump = requested_check
                if not (
                    self._looks_like_intentional_seek(requested_check)
                    or self._looks_like_intentional_seek(local_jump)
                ):
                    return

            self._remember_programmatic_seek(pos)
            if pot_set_current(pos):
                # PotPlayer can briefly report PAUSED while performing a seek. Suppress
                # playback-state detection during that seek so it cannot be mistaken
                # for a real pause and immediately echoed back through the lobby.
                if action.get('detected'):
                    # Prefer the intentional skip amount from the sender. Never log
                    # current_position - previous_position (keyframe seeks lie).
                    try:
                        requested = int(action.get('requested_delta', delta) or 0)
                    except Exception:
                        requested = int(delta) if delta else 0
                    self._log_detected_seek_action(actor, requested, pos, broadcast=True, source_client=source_client)
                    # Other peers receive the authoritative media position. For Huge
                    # relative skips, include the source so a lagging guest snaps to
                    # the host-corrected target; PotPlayer scrub echoes still exclude
                    # source (guest already at that absolute).
                    payload = {
                        'action': 'set_position',
                        'position': int(pos),
                        'sent_at': time.time(),
                        'seek_sync': True,
                        'remote': True,
                        'playing': bool(action.get('playing', pot_status() == STATUS_PLAYING)),
                    }
                    self._note_remote_seek_broadcast(pos)
                    kind = str(action.get('kind') or '')
                    origin = str(action.get('origin') or '').lower()
                    include_source = kind == KIND_SKIP_REL or origin in (ORIGIN_HUGE, 'huge')
                    if source_client is not None and not include_source:
                        self.server.broadcast({'type': 'command', 'action': payload}, exclude=source_client)
                    else:
                        self.server.broadcast({'type': 'command', 'action': payload})
        elif name == 'set_playing':
            if not self._accept_play_cmd(action):
                return
            desired = bool(action.get('playing'))
            now = time.time()
            # Syncplay ignoringOnTheFly: do not let a stale remote playstate undo a
            # newer local Pause/Play until the lobby acknowledges our intent.
            # Guest proposals (detected) are always fresh intent on the host.
            # Host relays with play_seq/lobby_seq: only clear ignore when the seq is
            # actually newer — a late Pause relay must not wipe a newer local Play.
            self._ignore_fly.observe_remote_ignore(action)
            is_guest_proposal = bool(action.get('detected')) and self.role == 'host'
            try:
                incoming_play_seq = int(action.get('play_seq') or 0)
            except Exception:
                incoming_play_seq = 0
            try:
                incoming_lobby_seq = int(action.get('lobby_seq') or 0)
            except Exception:
                incoming_lobby_seq = 0
            newer_host_relay = (
                self.role == 'client'
                and action.get('remote')
                and (
                    (incoming_play_seq > 0 and incoming_play_seq >= int(self._last_applied_play_seq or 0))
                    or (incoming_play_seq <= 0 and incoming_lobby_seq >= int(self._bus.lobby_seq or 0))
                )
            )
            if (
                not is_guest_proposal
                and not newer_host_relay
                and not self._ignore_fly.should_apply_remote_playstate(paused=not desired)
            ):
                self._log_playback(
                    'ignore_stale_playstate',
                    origin='net',
                    desired=desired,
                    local_ignore=self._ignore_fly.local,
                )
                return
            if is_guest_proposal or newer_host_relay:
                self._ignore_fly.clear()
            # Drop stale PotPlayer "paused" echoes right after START MOVIE — Mini64
            # often reports PAUSED briefly while playback is starting. Do NOT drop
            # intentional Huge-button pauses from guests (origin=huge); those must
            # sync even if the user hits pause within the start-grace window.
            if not desired and self.started and now < self._playback_start_grace_until:
                origin = str(action.get('origin') or '')
                in_start_phase = self._playback_phase in (
                    PHASE_STARTING, PHASE_LOADING, PHASE_READY,
                )
                huge_intent = origin in (ORIGIN_HUGE, 'huge')
                if in_start_phase or (action.get('detected') and not huge_intent):
                    self._log_playback(
                        'ignore_pause',
                        origin='net',
                        detected=bool(action.get('detected')),
                        cmd_origin=origin,
                    )
                    return
            if self._playback_phase == PHASE_STARTING:
                self._log_playback('ignore_play_during_start', origin='net', desired=desired)
                return
            if not self.started and not desired:
                # Pre-start pauses stay local; never amplify them.
                if pot_status() == STATUS_PLAYING:
                    pot_pause()
                return
            # Remote command: apply desired state; never echo the sender's own action.
            if action.get('remote') or (self.role == 'client' and not action.get('detected')):
                # Ignore a matching echo of the state we just applied/published locally.
                now_echo = time.time()
                if (
                    now_echo < float(getattr(self, '_echo_play_until', 0) or 0)
                    and self._echo_play_expect is not None
                    and desired == bool(self._echo_play_expect)
                ):
                    self._log_playback('ignore_remote_play_echo', origin='net', desired=desired)
                    return
                applied = self._apply_play_state(
                    desired, origin=str(action.get('origin') or 'net'),
                )
                self._mark_remote_play_echo(desired)
                self._log_playback(
                    'remote_play_applied',
                    origin=str(action.get('origin') or 'net'),
                    desired=desired,
                    applied=applied,
                    cmd_id=str(action.get('cmd_id') or ''),
                    play_seq=int(action.get('play_seq') or 0),
                    lobby_seq=int(action.get('lobby_seq') or 0),
                    decision='apply_remote',
                )
                if desired:
                    self._playback_phase = PHASE_PLAYING
                elif self.started:
                    self._playback_phase = PHASE_PLAYING
                self.started = self.started or desired
                return
            # Host path for guest-detected play/pause: apply locally, then relay
            # to everyone INCLUDING the source. Guests ignore matching remote echoes
            # (ignore_remote_play_echo); including source corrects a failed local apply.
            if self.role == 'host':
                self._apply_play_state(desired, origin='guest')
                # Prefer the guest's Huge/UI origin when present so the relay is not
                # mis-labeled as potplayer feedback (and so feedback suppress works).
                raw_origin = str(action.get('origin') or '').lower()
                if raw_origin in ('huge', ORIGIN_HUGE):
                    relay_origin = 'huge'
                elif action.get('detected') and raw_origin in ('potplayer', ORIGIN_POTPLAYER, ''):
                    relay_origin = 'potplayer'
                else:
                    relay_origin = raw_origin or 'net'
                self._publish_play_state(
                    desired,
                    actor,
                    source_client=None,
                    log=True,
                    origin=relay_origin,
                )
                if desired:
                    self._playback_phase = PHASE_PLAYING
                elif self.started:
                    self._playback_phase = PHASE_PLAYING
                self.started = self.started or desired
        elif name == 'start_movie':
            if not self._accept_playback_seq(action):
                return
            try:
                seq = int(action.get('seq') or self._playback_seq or 1)
            except Exception:
                seq = max(1, self._playback_seq)
            try:
                lobby_seq = int(action.get('lobby_seq') or 0)
            except Exception:
                lobby_seq = 0
            if lobby_seq:
                self._bus.observe_remote_seq(lobby_seq)
            phase = str(action.get('phase') or action.get('kind') or 'prepare')
            is_go = bool(action.get('go')) or phase in ('go', KIND_START_GO)
            is_prepare = phase in ('prepare', KIND_START_PREPARE)
            # Older hosts sent phase=starting as a single-shot start.
            is_legacy = (not is_go and not is_prepare) or (
                phase == PHASE_STARTING and not action.get('kind')
            )
            # Duplicate go while already playing: ignore.
            if is_go and self.started and self._playback_phase == PHASE_PLAYING and seq <= self._playback_seq:
                self._log_playback('start_go_duplicate_ignored', origin='net', seq=seq)
                return
            if self._movie_loading or self._playback_phase == PHASE_LOADING:
                self._log_playback('start_while_loading', origin='net', seq=seq)
                self.set_connection('START MOVIE arrived before the movie finished loading.', warn=True)
                self._send_start_ack(seq, ok=False, error='Movie still loading', phase='prepare')
                return
            if not self.movie_loaded:
                self._log_playback('start_without_movie', origin='net', seq=seq)
                self.set_connection('Select and load the movie before START MOVIE can run.', warn=True)
                self._send_start_ack(seq, ok=False, error='Movie not loaded', phase='prepare')
                return
            self._playback_seq = max(self._playback_seq, seq)
            self.started = True
            if is_go:
                go_at = float(action.get('go_at') or 0)
                go_delay_ms = action.get('go_delay_ms')
                if go_delay_ms is not None:
                    try:
                        go_delay_ms = int(go_delay_ms)
                    except Exception:
                        go_delay_ms = START_GO_DELAY_MS
                ok, err = self._run_start_go(
                    seq,
                    origin=str(action.get('origin') or 'net'),
                    go_at=go_at,
                    go_delay_ms=go_delay_ms,
                )
                if not ok:
                    self.set_connection(err or 'Could not start the movie.', warn=True)
                else:
                    self.set_connection('Movie started from 00:00:00.', good=True)
            elif is_legacy:
                ok, err = self._run_authoritative_start(seq, origin=str(action.get('origin') or 'net'))
                self._send_start_ack(seq, ok=ok, error=err or '', phase='legacy')
                if not ok:
                    self.started = False
                    self._playback_phase = PHASE_READY
                    self._bus.set_phase(PHASE_READY)
                    self.set_connection(err or 'Could not start the movie.', warn=True)
                else:
                    self.set_connection('Movie started from 00:00:00.', good=True)
            else:
                ok, err = self._run_start_prepare(seq, origin=str(action.get('origin') or 'net'))
                self._send_start_ack(seq, ok=ok, error=err or '', phase='prepare')
                if not ok:
                    self.started = False
                    self._playback_phase = PHASE_READY
                    self._bus.set_phase(PHASE_READY)
                    self.set_connection(err or 'Could not seek to 00:00:00.', warn=True)
                else:
                    self.set_connection('Ready at 00:00:00 — waiting for host to start…', good=True)


    def _send_start_ack(self, seq, ok=True, error='', phase='prepare'):
        """Client → host acknowledgement for the START MOVIE prepare barrier."""
        if self.role != 'client' or not self.client or not self.client.running:
            return
        pos = pot_current()
        status = pot_status()
        self.client.send({
            'type': 'start_ack',
            'seq': int(seq),
            'ok': bool(ok),
            'phase': str(phase or 'prepare'),
            'position': int(pos) if isinstance(pos, int) else -1,
            'status': int(status) if isinstance(status, int) else STATUS_STOPPED,
            'error': str(error or ''),
            'sent_at': time.time(),
        })
        self._log_playback(
            'start_ack_send', origin='client', seq=seq, ok=ok, pos=pos, status=status, phase=phase,
        )


    def finish_toggle_log(self, actor, before, source_client, desired=None):
        """Legacy helper — prefer _publish_play_state for new call sites."""
        if desired is None:
            playing = pot_status() == STATUS_PLAYING
        else:
            playing = bool(desired)
        self._publish_play_state(
            playing,
            actor,
            source_client=source_client,
            log=True,
            origin='huge',
        )
        self.started = self.started or playing


    def _set_playing(self, playing):
        self._apply_play_state(bool(playing), origin='local')
        self._mark_remote_play_echo(bool(playing))


    def export_sync_logs(self):
        """Zip playback_trace.log to the Desktop for multiplayer test sharing."""
        folder = os.path.dirname(self._profile_file())
        os.makedirs(folder, exist_ok=True)
        ok, path = export_sync_logs(
            folder,
            username=self.username or 'pc',
            role=str(self.role or 'none'),
            app_version=APP_VERSION,
        )
        if ok:
            self.set_connection(f'Sync log saved to Desktop:\n{path}', good=True)
            self._log_playback('export_sync_logs', origin='local', path=path)
            return {'ok': True, 'path': path}
        self.set_connection(f'Could not export sync log: {path}', warn=True)
        return {'ok': False, 'error': path}


    def _position_for_relative_seek(self):
        """Base timestamp for stacking relative skips while a seek is still settling.

        PotPlayer often keeps reporting the old position for ~0.5s on 4K/HEVC after
        SET_CURRENT. Using that stale read as the next relative base drops skips or
        seeks the wrong direction. Prefer the last commanded target instead.
        """
        now = time.time()
        # While draining Huge skip buttons, never use a live PotPlayer read as the
        # base — it lags and silently eats stacked skips.
        if self._user_skip_active or self._user_skip_pending():
            if self._expected_position >= 0:
                return int(self._expected_position)
            if self._cached_position >= 0:
                return int(self._cached_position)
            if self._last_pot_pos >= 0:
                return int(self._last_pot_pos)
        if self._expected_position >= 0 and now < self._expected_position_until:
            return int(self._expected_position)
        if (
            self._cached_position >= 0
            and now < self._programmatic_position_until
            and self._programmatic_position_until > 0
        ):
            return int(self._cached_position)
        pos = pot_current()
        if isinstance(pos, int) and pos >= 0:
            return int(pos)
        # Fall back to last known media time instead of failing the skip.
        if self._cached_position >= 0:
            return int(self._cached_position)
        if self._last_pot_pos >= 0:
            return int(self._last_pot_pos)
        return -1


    def _user_skip_pending(self):
        if self._user_skip_active:
            return True
        with self._skip_lock:
            return bool(self._skip_queue)


    def _enqueue_user_skip(self, delta_ms, actor=None, source_client=None):
        """Queue one Huge skip-button delta. Every press is kept and applied in order."""
        if self._playback_phase == PHASE_STARTING or not self.started:
            # Never interrupt an unfinished START MOVIE zero-reset; pre-start skips stay local-off.
            if self._playback_phase == PHASE_STARTING:
                self._log_playback('skip_blocked_starting', origin='local', delta=delta_ms)
                return
        try:
            delta = int(delta_ms)
        except Exception:
            return
        if delta == 0:
            return
        item = {
            'delta': delta,
            'actor': actor or self.username,
            'source_client': source_client,
            'attempts': 0,
        }
        with self._skip_lock:
            self._skip_queue.append(item)
            if self._skip_worker_alive:
                return
            self._skip_worker_alive = True
        threading.Thread(target=self._user_skip_worker, name='HugeSkipQueue', daemon=True).start()


    def _user_skip_worker(self):
        while True:
            with self._skip_lock:
                if not self._skip_queue:
                    self._skip_worker_alive = False
                    return
                item = self._skip_queue.pop(0)
            try:
                self._execute_user_skip(item)
            except Exception:
                # Never kill the worker; continue draining the queue.
                pass


    def _reliable_seek_base(self):
        """Position base for a skip. Retries briefly so a busy PotPlayer cannot drop it."""
        for _ in range(12):
            pos = self._position_for_relative_seek()
            if pos >= 0:
                return int(pos)
            time.sleep(0.05)
        return -1


    def _execute_user_skip(self, item):
        """Apply exactly one queued skip: base → target → sync SET_CURRENT → notify."""
        if self._playback_phase == PHASE_STARTING:
            # Drop — START MOVIE owns the timeline until PLAYING.
            self._log_playback('skip_dropped_starting', origin='local')
            return
        delta = int(item['delta'])
        actor = item.get('actor') or self.username
        source_client = item.get('source_client')
        base = self._reliable_seek_base()
        if base < 0:
            item['attempts'] = int(item.get('attempts', 0)) + 1
            if item['attempts'] < 5:
                with self._skip_lock:
                    self._skip_queue.insert(0, item)
                time.sleep(0.15)
            return

        target = max(0, base + delta)
        playing_now = pot_status() == STATUS_PLAYING
        # Logical target first so a following queued skip stacks on this one immediately.
        self._remember_programmatic_seek(target)
        self._user_skip_active = True
        delivered = False
        try:
            result = pot_set_current_sync(target, timeout=2.0)
            if result is False:
                time.sleep(0.15)
                result = pot_set_current_sync(target, timeout=2.0)
            # None = wait timeout (command may still run) — do not re-queue (avoids double seek).
            # False after retry = PotPlayer rejected; re-queue a limited number of times.
            if result is False:
                item['attempts'] = int(item.get('attempts', 0)) + 1
                if item['attempts'] < 4:
                    with self._skip_lock:
                        self._skip_queue.insert(0, item)
                    time.sleep(0.2)
                    return
            delivered = True
            # Brief settle so the next skip is not piled onto an in-flight demuxer seek.
            time.sleep(0.08)
        finally:
            self._user_skip_active = False

        if not delivered:
            return

        cmd_id = new_cmd_id()
        seek_payload = {
            'action': 'set_position',
            'kind': KIND_SKIP_REL,
            'position': int(target),
            'position_ms': int(target),
            'delta': int(delta),
            'delta_ms': int(delta),
            'requested_delta': int(delta),
            'cmd_id': cmd_id,
            'seq': self._playback_seq,
            'playing': playing_now,
            'sent_at': time.time(),
            'origin': ORIGIN_HUGE,
        }
        if self.role == 'client' and self.client:
            # Guest proposes absolute target; host assigns lobby_seq when relaying.
            self._seek_log_suppress_until = time.time() + SEEK_COMMAND_SUPPRESS_MS / 1000
            seek_payload['detected'] = True
            self._log_playback('skip_propose', origin=ORIGIN_HUGE, cmd_id=cmd_id, target=target, delta=delta)
            self.client.send({'type': 'command', 'action': seek_payload})
        elif self.role == 'host':
            lobby_seq = self._bus.next_lobby_seq()
            seek_payload['lobby_seq'] = lobby_seq
            self._bus.note_remote_seek(target)
            self._log_user_skip(actor, int(delta), broadcast=True, source_client=source_client)
            self._note_remote_seek_broadcast(target)
            seek_payload['seek_sync'] = True
            seek_payload['remote'] = True
            self._log_playback(
                'skip_send',
                origin=ORIGIN_HUGE,
                lobby_seq=lobby_seq,
                cmd_id=cmd_id,
                target=target,
                delta=delta,
            )
            self.server.broadcast({
                'type': 'command',
                'action': seek_payload,
            }, exclude=source_client)


    def _remember_programmatic_seek(self, position_ms):
        """Record a Huge-generated seek so detection/echo logic ignores it."""
        try:
            pos = int(max(0, position_ms))
        except Exception:
            return
        now = time.time()
        until = now + SEEK_COMMAND_SUPPRESS_MS / 1000
        self._last_pot_pos = pos
        self._cached_position = pos
        self._player_position_ms = pos
        self._global_position_ms = pos
        self._last_pos_sample_time = now
        self._expected_position = pos
        self._expected_position_until = until
        self._programmatic_position_until = until
        # Syncplay-style: protect intentional seek from stale room snapshots.
        self._ignore_fly.note_local_change(position_ms=pos)
        self._suppress_detection_until = max(
            self._suppress_detection_until,
            now + (STATE_SUPPRESS_MS + SEEK_SETTLE_GRACE_MS) / 1000,
        )
        self._local_seek_echo_guard = [
            (p, u) for (p, u) in self._local_seek_echo_guard if u > now
        ][-12:]
        self._local_seek_echo_guard.append((pos, until))


    def _note_remote_seek_broadcast(self, position_ms):
        """Host: remember positions we just pushed so guest echoes are dropped."""
        try:
            pos = int(max(0, position_ms))
        except Exception:
            return
        now = time.time()
        until = now + (SEEK_COMMAND_SUPPRESS_MS + SEEK_SETTLE_GRACE_MS) / 1000
        self._recent_remote_seek_guard = [
            (p, u) for (p, u) in self._recent_remote_seek_guard if u > now
        ][-16:]
        self._recent_remote_seek_guard.append((pos, until))


    def _is_echo_of_local_seek(self, position_ms):
        """True only when pos matches our *latest* local seek (not any recent one).

        Matching the full guard list dropped legitimate backward skips that returned
        near an earlier stacked target (e.g. +5 → +30 → −30 lands near the +5
        position and was ignored as an 'echo').
        """
        try:
            pos = int(position_ms)
        except Exception:
            return False
        if not self._local_seek_echo_guard:
            return False
        now = time.time()
        target, until = self._local_seek_echo_guard[-1]
        return now <= until and abs(pos - int(target)) <= 1500


    def _is_echo_of_recent_remote_seek(self, position_ms):
        """True if a guest 'detected' seek bounces our *latest* remote sync back.

        Only the latest broadcast is an echo candidate — older stacked skip
        targets must not suppress a real backward skip to a prior position.
        """
        try:
            pos = int(position_ms)
        except Exception:
            return False
        if not self._recent_remote_seek_guard:
            return False
        now = time.time()
        target, until = self._recent_remote_seek_guard[-1]
        return now <= until and abs(pos - int(target)) <= 2500


    def _is_natural_playback_delta(self, delta_ms, wall_elapsed_s, playing):
        """True when the media position change matches ordinary 1x playback progress.

        Busy monitor ticks (network + Win32) can leave 1–3s gaps between samples.
        Those gaps must never become seek commands — especially not guest→host rewinds.
        """
        try:
            delta_ms = int(delta_ms)
            wall_elapsed_s = float(wall_elapsed_s)
        except Exception:
            return False
        if wall_elapsed_s <= 0:
            return False
        if not playing:
            # While paused, only tiny reporter jitter is "natural".
            return abs(delta_ms) < 400
        if delta_ms < 0:
            # Playback never walks backward on its own.
            return False
        expected = wall_elapsed_s * 1000.0
        # Wide band: PotPlayer reporting + scheduling stalls on 4K/HEVC.
        lo = max(0.0, expected * 0.2 - 500.0)
        hi = expected * 2.8 + 1000.0
        return lo <= float(delta_ms) <= hi


    def _looks_like_intentional_seek(self, delta_ms):
        """Only keyboard/Huge skips and real timeline scrubs — not settle noise."""
        kind, _value = self._classify_seek_delta_for_log(delta_ms)
        return kind in ('skip', 'scrub')


    def close_potplayer(self):
        if os.name != 'nt':
            return
        hwnd = pot_window()
        if not hwnd:
            return
        pid = ctypes.c_ulong(0)
        try:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        except Exception:
            pid.value = 0
        try:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            try:
                user32.SendMessageW(hwnd, 0x0010, 0, 0)
            except Exception:
                pass

        # Give PotPlayer a moment to close cleanly; force-close only its own window process if needed.
        deadline = time.time() + 0.9
        while time.time() < deadline:
            if pot_window() is None:
                return
            time.sleep(0.08)
        if pid.value:
            try:
                flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                subprocess.run(
                    ['taskkill', '/PID', str(pid.value), '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, creationflags=flags, timeout=2,
                )
            except Exception:
                pass


    def _check_potplayer_link(self):
        if self.closed or not self._pot_link_active:
            return
        hwnd = pot_window()
        now = time.time()
        if hwnd:
            self._pot_ever_seen = True
            self.pot_badge = 'READY'; self.pot_badge_kind = 'good'
            return
        if self._pot_ever_seen and now >= self._pot_launch_grace_until and not self._closing_from_pot:
            self._closing_from_pot = True
            self.set_connection('PotPlayer closed • closing Huge.', warn=True)
            self.after(150, self.close)
            return


    def _normalize_forward_seek_delta(self, delta_ms):
        """
        PotPlayer's built-in forward hotkey can report a larger media-position jump
        than the hotkey amount because it seeks to a nearby keyframe (for example,
        a 5-second command can land about 10 seconds ahead). Huge should synchronize
        the *requested* forward amount, not the keyframe overshoot.

        Only forward seeks in the ranges used by Huge's controls are normalized.
        Backward seeking and unusual/large timeline jumps are left untouched.
        """
        try:
            delta_ms = int(delta_ms)
        except Exception:
            return int(delta_ms) if isinstance(delta_ms, (int, float)) else 0
        if delta_ms <= 0:
            return delta_ms

        # PotPlayer 5-second forward hotkeys commonly land between ~6 and ~15s
        # when the seek is performed by keyframe. Treat that as one 5s command.
        if 6000 <= delta_ms <= 15000:
            return 5000

        # Keep the normal 30-second Huge/PotPlayer forward action stable even if
        # keyframe seeking lands somewhat ahead of the requested point.
        if 18000 <= delta_ms <= 45000:
            return 30000

        return delta_ms


    def _classify_seek_delta_for_log(self, delta_ms):
        """Map a detected jump to an intentional user action for Live Log only.

        Returns ('skip', ±5000|±30000) for keyboard/Huge-style skips, or
        ('scrub', None) for timeline drags / large jumps. Never returns the raw
        sampled position difference as a skip amount.
        """
        try:
            delta_ms = int(delta_ms)
        except Exception:
            return None, None
        if not delta_ms:
            return None, None
        amount = abs(delta_ms)
        sign = 1 if delta_ms > 0 else -1
        # Exact Huge button amounts.
        if amount in (5000, 30000):
            return 'skip', sign * amount
        # PotPlayer keyboard 5s (keyframe overshoot/undershoot).
        if 3500 <= amount <= 15000:
            return 'skip', sign * 5000
        # PotPlayer keyboard 30s.
        if 16000 <= amount <= 45000:
            return 'skip', sign * 30000
        # Timeline scrub / chapter jump / other large seek.
        if amount >= SEEK_DETECT_MIN_MS:
            return 'scrub', None
        return None, None


    def _log_user_skip(self, actor, delta_ms, broadcast=True, source_client=None):
        """Record an intentional ±5s / ±30s skip. One action → one log line."""
        kind, value = self._classify_seek_delta_for_log(delta_ms)
        if kind != 'skip' or value is None:
            return False
        actor_name = safe_name(actor) or self.username or 'Someone'
        direction = 'skipped forward' if value > 0 else 'went back'
        amount = '5s' if abs(value) == 5000 else '30s'
        self._seek_log_suppress_until = time.time() + SEEK_COMMAND_SUPPRESS_MS / 1000
        self.append_log(
            f'{direction} {amount}.',
            actor=actor_name,
            broadcast=broadcast,
            source_client=source_client,
        )
        return True


    def _log_user_seek_to(self, actor, position_ms, broadcast=True, source_client=None):
        """Record a timeline scrub as a seek to a timestamp (not ±5/±30)."""
        try:
            pos = max(0, int(position_ms))
        except Exception:
            return False
        actor_name = safe_name(actor) or self.username or 'Someone'
        stamp = elapsed_time(pos / 1000.0)
        self._seek_log_suppress_until = time.time() + SEEK_COMMAND_SUPPRESS_MS / 1000
        self.append_log(
            f'seeked to {stamp}.',
            actor=actor_name,
            broadcast=broadcast,
            source_client=source_client,
        )
        return True


    def _log_detected_seek_action(self, actor, delta_ms, position_ms, broadcast=True, source_client=None):
        """Log a detected PotPlayer/guest seek using intended action, not raw delta text."""
        if time.time() < self._seek_log_suppress_until:
            return False
        kind, value = self._classify_seek_delta_for_log(delta_ms)
        if kind == 'skip':
            return self._log_user_skip(actor, value, broadcast=broadcast, source_client=source_client)
        if kind == 'scrub':
            return self._log_user_seek_to(actor, position_ms, broadcast=broadcast, source_client=source_client)
        return False


    def _monitor_body(self):
        if self.closed:
            return
        # While a movie file is opening/indexing, do not send any PotPlayer Win32
        # traffic from the monitor. Concurrent SendMessage traffic is what freezes
        # PotPlayer during Select Movie (especially 4K/HEVC).
        if self._movie_loading:
            return
        # While Huge skip buttons are draining, do not enqueue monitor snapshots.
        # Polling would starve the skip worker's synchronous SET_CURRENT commands.
        if self._user_skip_pending():
            return
        self._check_potplayer_link()
        now = time.time()

        # Never interleave status/position polling with in-flight PotPlayer commands.
        # This is the main protection against PotPlayer freezes under load.
        if _POT.busy():
            return

        want_total = (now - self._last_duration_read) >= 1.0
        snap = pot_snapshot(want_total=want_total)
        state = int(snap.get('status', STATUS_STOPPED))
        pos = int(snap.get('position', -1))
        self._cached_status = state
        # Never overwrite the logical skip/seek timeline with a stale PotPlayer read
        # while Huge still owns the position (stacked ±5/±30 must keep their base).
        if pos >= 0 and now >= self._programmatic_position_until and self._expected_position < 0:
            self._cached_position = pos
        if want_total:
            total = snap.get('total')
            if isinstance(total, int):
                self._cached_duration = total
            self._last_duration_read = now

        # A remote seek can take a few monitor ticks to appear in PotPlayer. If we know
        # the target, wait until PotPlayer reaches it (or the safety timeout expires) before
        # looking for a user-generated seek. This is the key protection against remote seek
        # echo/repeat loops on the guest machine.
        if self._expected_position >= 0:
            if pos >= 0:
                target_delta = abs(pos - self._expected_position)
                if target_delta <= 1500 and now >= self._programmatic_position_until:
                    # Only release the logical target once Huge's skip/seek ownership
                    # window ends. Keyframe snaps within 1.5s used to clear the target
                    # early and make the next +5/+30 use a stale PotPlayer base.
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                    self._expected_position = -1
                    self._expected_position_until = 0.0
                    self._programmatic_position_until = now + SEEK_SETTLE_GRACE_MS / 1000
                    self._suppress_detection_until = max(
                        self._suppress_detection_until,
                        now + SEEK_SETTLE_GRACE_MS / 1000,
                    )
                    # Never adopt transient PAUSED into baseline during seek settle —
                    # that swallowed rapid resume after pause (v2.0.7/2.0.8 fix).
                    if state == STATUS_PLAYING:
                        self._last_pot_state = state
                    return
                elif now < self._expected_position_until or now < self._programmatic_position_until:
                    # Hold the commanded timeline; do not adopt the lagging keyframe pos.
                    self._last_pot_pos = int(self._expected_position)
                    self._last_pos_sample_time = now
                    self._programmatic_position_until = max(
                        self._programmatic_position_until,
                        self._expected_position_until,
                    )
                    if state == STATUS_PLAYING:
                        self._last_pot_state = state
                    return
                else:
                    # Timeout: PotPlayer landed on an earlier keyframe than the target.
                    # Adopt the LIVE position. Leaving _last_pot_pos at the (ahead) target
                    # made the next sample look like a user rewind → guest→host loop.
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                    self._expected_position = -1
                    self._expected_position_until = 0.0
                    self._programmatic_position_until = now + SEEK_SETTLE_GRACE_MS / 1000
                    self._suppress_detection_until = max(
                        self._suppress_detection_until,
                        now + SEEK_SETTLE_GRACE_MS / 1000,
                    )
                    if state == STATUS_PLAYING:
                        self._last_pot_state = state
                    return

        playing_now = state == STATUS_PLAYING
        wall_elapsed = (
            (now - self._last_pos_sample_time)
            if self._last_pos_sample_time > 0
            else (MONITOR_MS / 1000.0)
        )

        if (pos >= 0 and self._last_pot_pos >= 0
                and self.started
                and self._playback_phase not in (PHASE_LOADING, PHASE_READY, PHASE_STARTING)
                and now >= self._programmatic_position_until
                and self._expected_position < 0
                and now >= self._suppress_detection_until):
            delta = pos - self._last_pot_pos
            # Ordinary playback (including long busy poll gaps) is never a seek.
            if self._is_natural_playback_delta(delta, wall_elapsed, playing_now):
                self._last_pot_pos = pos
                self._last_pos_sample_time = now
            elif (
                abs(delta) >= SEEK_DETECT_MIN_MS
                and abs(delta) <= SEEK_DETECT_MAX_MS
                and self._looks_like_intentional_seek(delta)
            ):
                if self.role == 'host' and self.server.running:
                    self._suppress_detection_until = max(
                        self._suppress_detection_until,
                        now + STATE_SUPPRESS_MS / 1000
                    )
                    if delta > 0:
                        # PotPlayer's keyframe seek can overshoot the requested forward
                        # hotkey. Normalize to the requested Huge forward amount, correct
                        # once through the worker, then broadcast that absolute target.
                        requested_delta = self._normalize_forward_seek_delta(delta)
                        corrected_pos = max(0, self._last_pot_pos + requested_delta)
                        if corrected_pos != pos:
                            if pot_set_current(corrected_pos):
                                pos = corrected_pos
                            else:
                                requested_delta = delta
                                corrected_pos = pos
                        self._remember_programmatic_seek(corrected_pos)
                        # Live Log uses intended action (5s/30s or scrub), not raw delta text.
                        if now >= self._seek_log_suppress_until:
                            self._log_detected_seek_action(
                                self.username, requested_delta, corrected_pos, broadcast=True,
                            )
                        self._note_remote_seek_broadcast(corrected_pos)
                        self.server.broadcast({
                            'type': 'command',
                            'action': {
                                'action': 'set_position',
                                'position': int(corrected_pos),
                                'sent_at': time.time(),
                                'seek_sync': True,
                                'remote': True,
                                'requested_delta': int(requested_delta),
                                'playing': playing_now,
                            },
                        })
                    else:
                        self._remember_programmatic_seek(pos)
                        if now >= self._seek_log_suppress_until:
                            # Backward keyboard: log intended 5s/30s (or scrub), never raw ms.
                            self._log_detected_seek_action(
                                self.username, delta, pos, broadcast=True,
                            )
                        self._note_remote_seek_broadcast(pos)
                        self.server.broadcast({
                            'type': 'command',
                            'action': {
                                'action': 'set_position',
                                'position': int(pos),
                                'sent_at': time.time(),
                                'remote': True,
                                'requested_delta': int(delta),
                                'playing': playing_now,
                            },
                        })
                elif self.role == 'client' and self.client.running:
                    if delta > 0:
                        requested_delta = self._normalize_forward_seek_delta(delta)
                        corrected_pos = max(0, self._last_pot_pos + requested_delta)
                        if corrected_pos != pos:
                            if pot_set_current(corrected_pos):
                                pos = corrected_pos
                            else:
                                requested_delta = delta
                                corrected_pos = pos
                        self._remember_programmatic_seek(corrected_pos)
                        # Guest does not write Live Log; host records one entry from this event.
                        self._seek_log_suppress_until = time.time() + SEEK_COMMAND_SUPPRESS_MS / 1000
                        self.client.send({
                            'type': 'command',
                            'action': {
                                'action': 'set_position',
                                'position': int(corrected_pos),
                                'delta': int(requested_delta),
                                'detected': True,
                                'origin': 'potplayer',
                                'requested_delta': int(requested_delta),
                                'playing': playing_now,
                                'sent_at': time.time(),
                            },
                        })
                    else:
                        self._remember_programmatic_seek(pos)
                        self._seek_log_suppress_until = time.time() + SEEK_COMMAND_SUPPRESS_MS / 1000
                        # Send classified intended amount for logging on the host.
                        log_kind, log_delta = self._classify_seek_delta_for_log(delta)
                        send_delta = int(log_delta) if log_kind == 'skip' else int(delta)
                        self.client.send({
                            'type': 'command',
                            'action': {
                                'action': 'set_position',
                                'position': int(pos),
                                'delta': send_delta,
                                'detected': True,
                                'origin': 'potplayer',
                                'requested_delta': send_delta,
                                'playing': playing_now,
                                'sent_at': time.time(),
                            },
                        })
            else:
                # Noise / undershoot: adopt live timeline without broadcasting.
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now

        # Before START MOVIE (READY/LOADING): keep paused locally only — never broadcast.
        if (
            not self.started
            or self._playback_phase in (PHASE_LOADING, PHASE_READY)
        ) and state == STATUS_PLAYING and (
            self._pot_link_active or self.movie_loaded or self._playback_phase == PHASE_LOADING
        ):
            pot_pause()
            state = STATUS_PAUSED
            self._last_pot_state = STATUS_PAUSED
            self._cached_status = STATUS_PAUSED
            self._suppress_detection_until = now + PLAY_PAUSE_SUPPRESS_MS / 1000
            self._log_playback('monitor_hold_pause', origin='monitor', phase=self._playback_phase)
            if pos >= 0:
                self._last_pot_pos = pos
            return

        if not self.started or self._playback_phase in (PHASE_LOADING, PHASE_READY, PHASE_STARTING):
            # No shared play/pause events until START MOVIE completes.
            if self._playback_phase == PHASE_STARTING and state == STATUS_PLAYING:
                self._last_pot_state = STATUS_PLAYING
                self._cached_status = STATUS_PLAYING
            elif state in (STATUS_PLAYING, STATUS_PAUSED):
                self._last_pot_state = STATUS_PAUSED if state == STATUS_PLAYING else state
                self._cached_status = self._last_pot_state
            elif self._last_pot_state is None:
                self._last_pot_state = state
            if pos >= 0:
                self._last_pot_pos = pos
            return

        if state in (STATUS_PLAYING, STATUS_PAUSED):
            sample_paused = state != STATUS_PLAYING
            sample_pos = int(pos) if isinstance(pos, int) and pos >= 0 else int(self._player_position_ms or 0)
            # Syncplay _determinePlayerStateChange: only treat as user Pause/Play when
            # the sample differs from BOTH tracked player paused AND global paused.
            pause_change, _seeked_dual = determine_player_state_change(
                tracked_player_paused=bool(self._player_paused),
                tracked_player_position_ms=int(self._player_position_ms or 0),
                tracked_global_paused=bool(self._global_paused),
                tracked_global_position_ms=int(self._global_position_ms or 0),
                sample_paused=sample_paused,
                sample_position_ms=sample_pos,
                seek_threshold_ms=SEEK_THRESHOLD_MS,
            )
            is_playing = not sample_paused
            was_playing = not bool(self._player_paused)
            prev_last = self._last_pot_state
            # Ignore transient PAUSED reports while a seek is still settling.
            if (
                sample_paused
                and self._expected_position >= 0
                and now < self._expected_position_until
            ):
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                return
            # Ignore stale PAUSED right after START MOVIE.
            if sample_paused and now < self._playback_start_grace_until:
                self._log_playback('monitor_ignore_pause', origin='monitor')
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                return
            # Echo / lag: commanded state not yet confirmed on PotPlayer.
            if (
                now < self._echo_play_until
                and self._echo_play_expect is not None
                and is_playing == bool(self._echo_play_expect)
            ):
                self._echo_play_confirmed = True
                self._last_pot_state = state
                # Keep dual-threshold tracking aligned so the echo window expiry
                # cannot republish this same state as a fresh potplayer event.
                self._player_paused = sample_paused
                self._global_paused = sample_paused
                if self._ignore_fly.local and self._ignore_fly.expect_paused is not None:
                    if sample_paused == bool(self._ignore_fly.expect_paused):
                        self._ignore_fly.local = 0
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                return
            if (
                now < self._echo_play_until
                and self._echo_play_expect is not None
                and bool(is_playing) != bool(self._echo_play_expect)
                and not bool(getattr(self, '_echo_play_confirmed', True))
            ):
                self._apply_play_state(bool(self._echo_play_expect), origin='sync')
                self._log_playback(
                    'monitor_resync_lag',
                    origin='monitor',
                    expect=self._echo_play_expect,
                    sample=is_playing,
                    decision='resync',
                )
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                return
            # Matching the last broadcast is feedback (Huge/UI/remote apply), not a
            # new user action — even if dual-threshold briefly looks like a change.
            if (
                self._last_broadcast_playing is not None
                and bool(is_playing) == bool(self._last_broadcast_playing)
            ):
                self._player_paused = sample_paused
                self._global_paused = sample_paused
                self._last_pot_state = state
                if sample_pos >= 0:
                    self._player_position_ms = sample_pos
                if pos >= 0:
                    self._last_pot_pos = pos
                    self._last_pos_sample_time = now
                return
            # Syncplay updatePlayerStatus: adopt sample into player tracking AFTER detect.
            self._player_paused = sample_paused
            if sample_pos >= 0:
                self._player_position_ms = sample_pos
            self._last_pot_state = state
            # Clear ignoringOnTheFly once the local player matches our last intent
            # (not only inside the short echo window).
            if (
                self._ignore_fly.local > 0
                and self._ignore_fly.expect_paused is not None
                and sample_paused == bool(self._ignore_fly.expect_paused)
            ):
                self._ignore_fly.local = 0
            # Only publish when Syncplay dual-condition says this is a real pause change
            # and it differs from the last authoritative broadcast (avoid no-op Play
            # republish that would arm ignoringOnTheFly against the next Pause).
            if pause_change and bool(is_playing) != bool(self._last_broadcast_playing):
                ip, wp = is_playing, was_playing
                if self.role == 'host' and self.server.running:
                    self.ui(lambda a=ip, b=wp: self.handle_local_state_transition(a, b))
                elif self.role == 'client' and self.client.running:
                    self.ui(lambda a=ip: self._publish_play_state(
                        a, self.username, source_client=None, log=False, origin='potplayer',
                    ))
        elif state in (STATUS_PLAYING, STATUS_PAUSED):
            self._last_pot_state = state

        # Do not continuously force the host's playback position onto guests.

        if pos >= 0:
            self._last_pot_pos = pos
            self._last_pos_sample_time = now


