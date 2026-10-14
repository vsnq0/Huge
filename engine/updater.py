"""Huge auto-update client for the public Huge GitHub releases.

Checks https://github.com/vsnq0/Huge only. Never uses the private source repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

RELEASES_API = 'https://api.github.com/repos/vsnq0/Huge/releases/latest'
ALLOWED_HOSTS = frozenset({
    'api.github.com',
    'github.com',
    'objects.githubusercontent.com',
    'release-assets.githubusercontent.com',
})
USER_AGENT = 'HugeWatchParty-Updater/2.0'
CHECK_COOLDOWN_SEC = 6 * 60 * 60


def read_app_version(root: str) -> str:
    for candidate in (
        os.path.join(root, 'VERSION.txt'),
        os.path.join(os.path.dirname(root), 'VERSION.txt'),
    ):
        try:
            with open(candidate, 'r', encoding='utf-8') as fh:
                text = fh.read().strip().lstrip('vV')
                if text:
                    return text
        except Exception:
            pass
    return '0.0.0'


def parse_version(text: str):
    text = str(text or '').strip().lstrip('vV')
    parts = []
    for chunk in re.split(r'[.+_-]', text):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            m = re.match(r'^(\d+)', chunk)
            parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_gt(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _ssl_context():
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_json(url: str, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/vnd.github+json',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        raw = resp.read()
    return json.loads(raw.decode('utf-8'))


def _host_allowed(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or '').lower()
        return host in ALLOWED_HOSTS
    except Exception:
        return False


def pick_setup_asset(release: dict) -> Optional[dict]:
    assets = release.get('assets') or []
    preferred = []
    for asset in assets:
        name = str(asset.get('name') or '')
        lower = name.lower()
        if not lower.endswith('.exe'):
            continue
        if 'setup' in lower and lower.startswith('huge-'):
            preferred.append(asset)
    if preferred:
        # Prefer the versioned Setup asset name.
        preferred.sort(key=lambda a: (0 if re.search(r'v?\d+\.\d+\.\d+', a.get('name') or '', re.I) else 1, a.get('name') or ''))
        return preferred[0]
    for asset in assets:
        name = str(asset.get('name') or '').lower()
        if name == 'huge setup.exe':
            return asset
    return None


def asset_sha256(asset: dict) -> Optional[str]:
    digest = str(asset.get('digest') or '').strip()
    if digest.lower().startswith('sha256:'):
        return digest.split(':', 1)[1].strip().lower()
    return None


def fetch_latest_release() -> dict:
    data = _http_json(RELEASES_API)
    if data.get('draft') or data.get('prerelease'):
        raise RuntimeError('Latest release is a draft or prerelease.')
    tag = str(data.get('tag_name') or data.get('name') or '').strip()
    if not tag:
        raise RuntimeError('Release has no version tag.')
    asset = pick_setup_asset(data)
    if not asset:
        raise RuntimeError('No Huge Setup.exe asset found on the latest release.')
    url = str(asset.get('browser_download_url') or '')
    if not url or not _host_allowed(url):
        raise RuntimeError('Installer download URL is not from the official Huge repository.')
    return {
        'version': tag.lstrip('vV'),
        'tag': tag,
        'name': str(data.get('name') or tag),
        'body': str(data.get('body') or '').strip(),
        'html_url': str(data.get('html_url') or ''),
        'asset_name': str(asset.get('name') or ''),
        'asset_url': url,
        'asset_size': int(asset.get('size') or 0),
        'sha256': asset_sha256(asset),
        'published_at': str(data.get('published_at') or ''),
    }


class UpdateManager:
    def __init__(self, app_version: str, on_event: Optional[Callable[[dict], None]] = None):
        self.app_version = app_version
        self.on_event = on_event
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._last_offer = ''
        self._dismissed_version = ''
        self._cancel = threading.Event()
        self.state = {
            'status': 'idle',
            'current_version': app_version,
            'latest_version': '',
            'update_available': False,
            'banner_dismissed': False,
            'release_name': '',
            'release_notes': '',
            'html_url': '',
            'asset_name': '',
            'asset_url': '',
            'sha256': '',
            'sha256_verified': False,
            'sha256_skipped_reason': '',
            'progress': 0.0,
            'bytes_done': 0,
            'bytes_total': 0,
            'error': '',
            'message': '',
        }

    def _emit(self, **kwargs):
        with self._lock:
            self.state.update(kwargs)
            snapshot = dict(self.state)
        if self.on_event:
            try:
                self.on_event(snapshot)
            except Exception:
                pass

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def check_async(self, force: bool = False):
        threading.Thread(target=self._check, args=(force,), daemon=True, name='HugeUpdateCheck').start()

    def _check(self, force: bool):
        now = time.time()
        if not force and (now - self._last_check) < 30:
            return
        self._last_check = now
        self._emit(status='checking', error='', message='Checking for updates…', progress=0.0)
        try:
            release = fetch_latest_release()
            available = version_gt(release['version'], self.app_version)
            notes = release['body']
            if len(notes) > 1200:
                notes = notes[:1200].rstrip() + '…'
            msg = (
                f"Huge {release['version']} is available."
                if available else
                f"Huge is up to date (v{self.app_version})."
            )
            banner_dismissed = bool(
                available and release['version'] == getattr(self, '_dismissed_version', '')
            )
            if available and not force and release['version'] == self._last_offer:
                self._emit(
                    status='idle',
                    latest_version=release['version'],
                    update_available=True,
                    banner_dismissed=banner_dismissed,
                    release_name=release['name'],
                    release_notes=notes,
                    html_url=release['html_url'],
                    asset_name=release['asset_name'],
                    asset_url=release['asset_url'],
                    sha256=release['sha256'] or '',
                    message=msg,
                )
                return
            if available:
                self._last_offer = release['version']
            self._emit(
                status='available' if available else 'up_to_date',
                latest_version=release['version'],
                update_available=available,
                banner_dismissed=banner_dismissed,
                release_name=release['name'],
                release_notes=notes,
                html_url=release['html_url'],
                asset_name=release['asset_name'],
                asset_url=release['asset_url'],
                sha256=release['sha256'] or '',
                sha256_skipped_reason='' if release['sha256'] else 'Release asset has no SHA-256 digest yet.',
                message=msg,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                self._emit(
                    status='error',
                    error='GitHub rate limit or access denied. Your current Huge install is unchanged — try Check for Updates again later.',
                    message='',
                    update_available=False,
                )
            else:
                self._emit(
                    status='error',
                    error=f'Update check failed (HTTP {exc.code}). Huge stays on v{self.app_version}.',
                    message='',
                    update_available=False,
                )
        except Exception as exc:
            self._emit(
                status='error',
                error=f'Update check failed: {exc}. Huge stays on v{self.app_version}; try again when online.',
                message='',
                update_available=False,
            )

    def dismiss(self):
        with self._lock:
            ver = str(self.state.get('latest_version') or '')
            if ver:
                self._dismissed_version = ver
        self._emit(status='idle', message='', error='', banner_dismissed=True)

    def cancel_download(self):
        self._cancel.set()

    def download_and_install_async(self, restart_callback: Callable[[str], None]):
        with self._lock:
            if self.state.get('status') in ('downloading', 'installing'):
                return
        threading.Thread(
            target=self._download_and_install,
            args=(restart_callback,),
            daemon=True,
            name='HugeUpdateInstall',
        ).start()

    def _download_and_install(self, restart_callback: Callable[[str], None]):
        self._cancel.clear()
        snap = self.snapshot()
        url = snap.get('asset_url') or ''
        name = snap.get('asset_name') or 'Huge-Setup.exe'
        expected = (snap.get('sha256') or '').lower()
        if not url or not _host_allowed(url):
            self._emit(status='error', error='Installer URL is missing or not official.')
            return
        if not version_gt(snap.get('latest_version') or '', self.app_version):
            self._emit(status='up_to_date', message=f'Huge is up to date (v{self.app_version}).', update_available=False)
            return

        folder = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Huge', 'updates')
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        partial = dest + '.partial'
        self._emit(status='downloading', progress=0.0, bytes_done=0, bytes_total=int(snap.get('asset_size') or 0), error='', message='Downloading update…')

        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:
                total = int(resp.headers.get('Content-Length') or snap.get('asset_size') or 0)
                hasher = hashlib.sha256()
                done = 0
                with open(partial, 'wb') as out:
                    while True:
                        if self._cancel.is_set():
                            self._emit(status='idle', message='Download cancelled.', progress=0.0)
                            try:
                                os.remove(partial)
                            except Exception:
                                pass
                            return
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        pct = (done / total) if total else 0.0
                        self._emit(progress=min(0.99, pct), bytes_done=done, bytes_total=total, message='Downloading update…')
            os.replace(partial, dest)
            digest = hasher.hexdigest().lower()
            if expected:
                if digest != expected:
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
                    self._emit(status='error', error='Installer checksum mismatch. Update aborted.', sha256_verified=False)
                    return
                self._emit(sha256_verified=True, sha256_skipped_reason='')
            else:
                self._emit(
                    sha256_verified=False,
                    sha256_skipped_reason='No SHA-256 digest was published for this release asset; file host was verified as official GitHub only.',
                )

            self._emit(
                status='installing',
                progress=1.0,
                message='Download complete. Closing Huge and running the official installer…',
                bytes_done=done,
                bytes_total=total or done,
            )
            # Handoff to the app shell so it can close cleanly, then run Setup /update.
            try:
                restart_callback(dest)
            except Exception as exc:
                self._emit(
                    status='error',
                    error=(
                        f'Could not start the installer: {exc}. '
                        f'Huge v{self.app_version} is still installed. '
                        'Use Check for Updates to retry, or run the downloaded Setup from '
                        f'{folder}.'
                    ),
                    message='',
                )
        except Exception as exc:
            self._emit(
                status='error',
                error=(
                    f'Update failed: {exc}. '
                    f'Your Huge v{self.app_version} install was not modified. '
                    'Check your connection and try Update Now again.'
                ),
                message='',
            )
            try:
                if os.path.isfile(partial):
                    os.remove(partial)
            except Exception:
                pass


def launch_installer_and_exit(
    installer_path: str,
    huge_exe_path: str,
    install_dir: str = '',
    cleanup_old: str = '',
    elevate: bool = False,
    user_sid: str = '',
):
    """Start Setup in update mode, then the caller should exit Huge.

    Always pass /DIR= so updates land in the current custom install location
    instead of the default %LOCALAPPDATA%\\Programs\\Huge path.

    When elevate=True, launches via ShellExecute "runas" (UAC). Raises
    PermissionError if the user cancels UAC or elevation fails. Does not
    exit this process — the caller decides whether to exit after success.
    """
    if not os.path.isfile(installer_path):
        raise FileNotFoundError(installer_path)
    target_dir = (install_dir or '').strip() or os.path.dirname(os.path.abspath(huge_exe_path))
    relaunch = os.path.abspath(huge_exe_path)
    # Prefer relaunching the exe inside the target dir (migration / update).
    preferred = os.path.join(target_dir, 'Huge.exe')
    if os.path.normcase(os.path.dirname(relaunch)) != os.path.normcase(target_dir):
        relaunch = preferred
    # Quote-safe parameter string for ShellExecute / CreateProcess.
    params = [
        '/update',
        f'/DIR={target_dir}',
        f'/relaunch={relaunch}',
    ]
    old = (cleanup_old or '').strip()
    if old and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(target_dir)):
        params.append(f'/cleanup-old={os.path.abspath(old)}')
    sid = (user_sid or '').strip()
    if sid:
        params.append(f'/usersid={sid}')
    if elevate:
        params.append('/elevate')

    if elevate and os.name == 'nt':
        import ctypes

        def q(value: str) -> str:
            if not value:
                return '""'
            if any(ch in value for ch in ' \t"'):
                return '"' + value.replace('"', '\\"') + '"'
            return value

        param_line = ' '.join(q(p) for p in params)
        # ShellExecuteW "runas" shows the UAC prompt. Return value > 32 means success.
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(
                None,
                'runas',
                installer_path,
                param_line,
                os.path.dirname(installer_path) or None,
                1,  # SW_SHOWNORMAL
            )
        )
        if rc <= 32:
            # 5 = access denied / UAC cancel on many systems; 1223 via GetLastError sometimes.
            err = ctypes.GetLastError()
            if rc in (5, 32) or err in (5, 1223):
                raise PermissionError(
                    'Administrator permission was cancelled. '
                    'Your current Huge installation was not changed.'
                )
            raise OSError(rc, f'Could not start elevated Setup (ShellExecute={rc}, WinError={err}).')
        return

    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    subprocess.Popen(
        [installer_path, *params],
        close_fds=True,
        creationflags=flags,
    )


def _local_setup_fallback() -> str:
    """Prefer an explicit env path, then newest Huge-*-Setup.exe under LocalAppData\\Huge\\updates."""
    env = (os.environ.get('HUGE_SETUP_PATH') or '').strip().strip('"')
    if env and os.path.isfile(env):
        return env
    folder = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Huge', 'updates')
    if not os.path.isdir(folder):
        return ''
    candidates = []
    for name in os.listdir(folder):
        lower = name.lower()
        if lower.startswith('huge-') and lower.endswith('-setup.exe'):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                candidates.append(path)
    if not candidates:
        return ''
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def download_setup_for_migration(
    on_event: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download the official Setup for installer-managed migration (may be same version).

    Falls back to HUGE_SETUP_PATH or a previously downloaded Setup under
    %LOCALAPPDATA%\\Huge\\updates when the network fetch fails — so a privately
    staged diagnostic build can relocate before GitHub publish.
    """
    folder = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Huge', 'updates')
    os.makedirs(folder, exist_ok=True)
    cancel = cancel_event or threading.Event()

    def emit(**kwargs):
        if on_event:
            try:
                on_event(kwargs)
            except Exception:
                pass

    # Explicit diagnostic override — use before hitting GitHub so unreleased
    # builds can relocate with matching Setup + ACL support.
    env_setup = (os.environ.get('HUGE_SETUP_PATH') or '').strip().strip('"')
    if env_setup and os.path.isfile(env_setup):
        emit(status='installing', message='Using staged Setup for relocation…', progress=1.0, error='')
        return env_setup

    local = _local_setup_fallback()
    try:
        release = fetch_latest_release()
    except Exception as fetch_exc:
        if local:
            emit(
                status='downloading',
                message='Using local Setup for relocation…',
                progress=1.0,
                error='',
            )
            return local
        raise RuntimeError(
            f'Could not download the official Setup ({fetch_exc}). '
            'Place Huge-*-Setup.exe in %LOCALAPPDATA%\\Huge\\updates or set HUGE_SETUP_PATH.'
        ) from fetch_exc

    url = release['asset_url']
    name = release['asset_name'] or 'Huge-Setup.exe'
    expected = (release.get('sha256') or '').lower()
    dest = os.path.join(folder, name)
    partial = dest + '.partial'

    emit(status='downloading', message='Downloading installer for relocation…', progress=0.0, error='')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:
        total = int(resp.headers.get('Content-Length') or release.get('asset_size') or 0)
        hasher = hashlib.sha256()
        done = 0
        with open(partial, 'wb') as out:
            while True:
                if cancel.is_set():
                    try:
                        os.remove(partial)
                    except Exception:
                        pass
                    raise RuntimeError('Relocation download cancelled.')
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                pct = (done / total) if total else 0.0
                emit(progress=min(0.99, pct), bytes_done=done, bytes_total=total, message='Downloading installer for relocation…')
    os.replace(partial, dest)
    digest = hasher.hexdigest().lower()
    if expected and digest != expected:
        try:
            os.remove(dest)
        except Exception:
            pass
        raise RuntimeError('Installer checksum mismatch. Relocation aborted.')
    emit(status='installing', progress=1.0, message='Download complete. Closing Huge and relocating…')
    return dest
