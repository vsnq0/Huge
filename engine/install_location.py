"""Installation location helpers for Huge (Windows).

The real install directory is where Huge.exe lives. The Setup writes
HKCU\\Software\\vsnq\\Huge\\InstallLocation (or HKU\\<usersid> when elevated);
the app reads that and/or dirname(sys.executable) when frozen.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

REG_KEY = r'Software\vsnq\Huge'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\vsnq.Huge'
APP_ID = 'vsnq.Huge'

# GetDriveTypeW values
_DRIVE_NAMES = {
    0: 'UNKNOWN',
    1: 'NO_ROOT',
    2: 'REMOVABLE',
    3: 'FIXED',
    4: 'REMOTE',
    5: 'CDROM',
    6: 'RAMDISK',
}

# assess_dest_dir reason codes (stable for UI / diagnostics)
REASON_OK = 'ok'
REASON_NEEDS_ELEVATION = 'access_denied_needs_elevation'
REASON_INVALID = 'invalid'
REASON_DRIVE_UNAVAILABLE = 'drive_unavailable'
REASON_UNSUPPORTED = 'unsupported'
REASON_PROTECTED = 'protected'
REASON_NONEMPTY = 'nonempty'
REASON_NO_SPACE = 'insufficient_space'
REASON_ACCESS_DENIED = 'access_denied'


def default_install_dir() -> str:
    local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(local, 'Programs', 'Huge')


def current_install_dir() -> str:
    """Directory that contains the running Huge.exe (or source desktop.py)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'desktop.py')))


def is_frozen_install() -> bool:
    return bool(getattr(sys, 'frozen', False))


def huge_exe_path(install_dir: Optional[str] = None) -> str:
    return os.path.join(install_dir or current_install_dir(), 'Huge.exe')


def diag_log_path() -> str:
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.path.expanduser('~')
    folder = os.path.join(base, 'HugeWatchParty')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, 'install_location.log')


_diag_log_path = diag_log_path


def diag_log(message: str) -> None:
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        with open(diag_log_path(), 'a', encoding='utf-8') as fh:
            fh.write(line)
    except Exception:
        pass


def read_registry_install_dir() -> str:
    if os.name != 'nt':
        return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, 'InstallLocation')
            text = str(value or '').strip().strip('"')
            return os.path.normpath(text) if text else ''
    except Exception:
        return ''


def write_registry_install_dir(install_dir: str, version: str = '') -> None:
    if os.name != 'nt':
        return
    install_dir = os.path.normpath(install_dir)
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
        winreg.SetValueEx(key, 'InstallLocation', 0, winreg.REG_SZ, install_dir)
        if version:
            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, version)


def normalize_dest_dir(selected: str) -> str:
    """Normalize a user-picked folder into the intended Huge install directory."""
    path = os.path.normpath(os.path.expandvars(os.path.expanduser(str(selected or '').strip().strip('"'))))
    if not path or path in ('.', '\\', '/'):
        raise ValueError('Choose a valid installation folder.')
    drive, tail = os.path.splitdrive(path)
    rest = tail.replace('/', '\\').strip('\\')
    if drive and not rest:
        return os.path.normpath(os.path.join(drive + os.sep, 'Huge'))
    return path


def paths_same(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def drive_type_name(root: str) -> str:
    if os.name != 'nt':
        return 'UNKNOWN'
    try:
        import ctypes
        value = int(ctypes.windll.kernel32.GetDriveTypeW(root))
        return _DRIVE_NAMES.get(value, f'TYPE_{value}')
    except Exception:
        return 'UNKNOWN'


def current_user_sid() -> str:
    """SID of the interactive user running Huge (empty if unavailable)."""
    if os.name != 'nt':
        return ''
    # Prefer whoami — reliable without fragile ctypes HANDLE prototypes.
    try:
        import subprocess
        import re
        out = subprocess.check_output(
            ['whoami', '/user', '/fo', 'csv', '/nh'],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ).strip()
        # "domain\user","S-1-5-21-..."
        for part in out.replace('"', '').split(','):
            part = part.strip()
            if part.upper().startswith('S-1-'):
                return part
        m = re.search(r'(S-1-\d+(?:-\d+)+)', out)
        if m:
            return m.group(1)
    except Exception as exc:
        diag_log(f'current_user_sid whoami failed: {exc!r}')
    try:
        import ctypes
        from ctypes import wintypes

        TokenUser = 1
        advapi = ctypes.windll.advapi32
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.OpenProcessToken.restype = wintypes.BOOL

        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]

        class TOKEN_USER(ctypes.Structure):
            _fields_ = [('User', SID_AND_ATTRIBUTES)]

        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return ''
        try:
            needed = wintypes.DWORD(0)
            advapi.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed))
            buf = ctypes.create_string_buffer(max(needed.value, 1))
            if not advapi.GetTokenInformation(token, TokenUser, buf, needed, ctypes.byref(needed)):
                return ''
            tu = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
            sid_str = wintypes.LPWSTR()
            advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
            advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
            if not advapi.ConvertSidToStringSidW(tu.User.Sid, ctypes.byref(sid_str)):
                return ''
            try:
                return str(sid_str.value or '')
            finally:
                kernel.LocalFree(sid_str)
        finally:
            kernel.CloseHandle(token)
    except Exception as exc:
        diag_log(f'current_user_sid ctypes failed: {exc!r}')
        return ''


def _format_os_error(exc: BaseException) -> str:
    parts = [type(exc).__name__]
    win = getattr(exc, 'winerror', None)
    errno = getattr(exc, 'errno', None)
    if win is not None:
        parts.append(f'winerror={win}')
    if errno is not None:
        parts.append(f'errno={errno}')
    text = str(exc).strip()
    if text:
        parts.append(text)
    return ', '.join(parts)


def _is_access_denied(exc: BaseException) -> bool:
    win = getattr(exc, 'winerror', None)
    errno = getattr(exc, 'errno', None)
    if win in (5, 0x5):  # ERROR_ACCESS_DENIED
        return True
    if errno in (13,):  # EACCES
        return True
    if isinstance(exc, PermissionError):
        return True
    text = str(exc).lower()
    return 'permission denied' in text or 'access is denied' in text


def _probe_writable_dir(folder: str) -> Tuple[bool, str, Optional[BaseException]]:
    """Create a real temp file (no leading dot) inside folder. Delete is best-effort."""
    probe = os.path.join(folder, f'HugeWriteProbe_{os.getpid()}_{int(time.time() * 1000)}.tmp')
    try:
        with open(probe, 'wb') as fh:
            fh.write(b'huge-write-probe')
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
    except Exception as exc:
        return False, _format_os_error(exc), exc
    try:
        os.remove(probe)
    except Exception as exc:
        diag_log(f'probe delete failed (ignored) path={probe!r} err={_format_os_error(exc)}')
    return True, '', None


def assess_dest_dir(dest: str, required_bytes: int = 0) -> Dict[str, Any]:
    """Assess a destination for install/migration.

    Returns a dict:
      ok: True if migration may proceed (possibly after UAC elevation)
      dest: normalized path when ok
      needs_elevation: True when the unelevated process cannot write but Setup may
      reason: machine-readable category
      error: user-facing message when not ok (or advisory when needs_elevation)
      detail: OS error string when relevant
      drive_type: GetDriveType name
    """
    result: Dict[str, Any] = {
        'ok': False,
        'dest': '',
        'needs_elevation': False,
        'reason': REASON_INVALID,
        'error': '',
        'detail': '',
        'drive_type': 'UNKNOWN',
        'diag_log': diag_log_path(),
    }
    try:
        dest = normalize_dest_dir(dest)
    except ValueError as exc:
        diag_log(f'assess normalize fail: {exc}')
        result['error'] = str(exc)
        result['reason'] = REASON_INVALID
        return result

    result['dest'] = dest
    drive, _ = os.path.splitdrive(dest)
    if not drive:
        result['error'] = 'Installation path must include a drive letter (for example C:\\ or D:\\).'
        result['reason'] = REASON_INVALID
        return result

    root = drive + os.sep
    dtype = drive_type_name(root)
    result['drive_type'] = dtype
    diag_log(f'assess begin dest={dest!r} root={root!r} drive_type={dtype}')

    if not os.path.isdir(root):
        msg = f'Drive {drive} is not available (Windows reports {dtype}). Choose another location.'
        diag_log(msg)
        result['error'] = msg
        result['reason'] = REASON_DRIVE_UNAVAILABLE
        return result

    if dtype == 'CDROM':
        msg = f'Drive {drive} is an optical drive and cannot be used for installation.'
        diag_log(msg)
        result['error'] = msg
        result['reason'] = REASON_UNSUPPORTED
        return result
    if dtype == 'NO_ROOT':
        msg = f'Drive {drive} is not ready. Check that the drive is connected and accessible.'
        diag_log(msg)
        result['error'] = msg
        result['reason'] = REASON_DRIVE_UNAVAILABLE
        return result

    protected = (
        os.path.normcase(os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), '')),
        os.path.normcase(r'c:\windows'),
        os.path.normcase(r'c:\windows\system32'),
        os.path.normcase(r'c:\program files'),
        os.path.normcase(r'c:\program files (x86)'),
    )
    try:
        abs_dest = os.path.abspath(dest)
    except Exception as exc:
        diag_log(f'abspath failed: {_format_os_error(exc)}')
        result['error'] = f'Invalid destination path:\n{dest}'
        result['reason'] = REASON_INVALID
        result['detail'] = _format_os_error(exc)
        return result
    norm = os.path.normcase(abs_dest)
    for p in protected:
        if p and (norm == p.rstrip('\\') or norm.startswith(p.rstrip('\\') + '\\')):
            result['error'] = (
                'That folder is protected by Windows. '
                'Choose a folder under your user profile or another writable drive '
                '(for example D:\\Apps\\Huge).'
            )
            result['reason'] = REASON_PROTECTED
            return result

    created = False
    try:
        os.makedirs(dest, exist_ok=True)
        created = True
    except Exception as exc:
        detail = _format_os_error(exc)
        diag_log(f'makedirs failed dest={dest!r} err={detail}')
        if _is_access_denied(exc):
            result['ok'] = True
            result['needs_elevation'] = True
            result['reason'] = REASON_NEEDS_ELEVATION
            result['detail'] = detail
            result['error'] = (
                f'Huge cannot create files at:\n{dest}\n\n'
                f'Detail: {detail}\n'
                f'Drive type: {dtype}\n\n'
                'Windows may ask for administrator permission so Setup can prepare '
                'this folder for your account. Your current Huge install will stay '
                'intact if you cancel or if Setup fails.'
            )
            diag_log(f'assess needs_elevation (makedirs) dest={dest!r}')
            return result
        result['error'] = (
            f'Cannot create installation folder:\n{dest}\n\n'
            f'Detail: {detail}\n'
            f'Drive type: {dtype}'
        )
        result['reason'] = REASON_ACCESS_DENIED
        result['detail'] = detail
        return result

    if not os.path.isdir(dest):
        diag_log(f'dest missing after makedirs: {dest!r}')
        result['error'] = f'Cannot reach folder:\n{dest}'
        result['reason'] = REASON_DRIVE_UNAVAILABLE
        return result

    try:
        entries = [
            e for e in os.listdir(dest)
            if not e.startswith('HugeWriteProbe_') and not e.startswith('.huge_write_probe_')
        ]
    except PermissionError as exc:
        detail = _format_os_error(exc)
        diag_log(f'listdir permission dest={dest!r} err={detail}')
        result['ok'] = True
        result['needs_elevation'] = True
        result['reason'] = REASON_NEEDS_ELEVATION
        result['detail'] = detail
        result['error'] = (
            f'Permission denied reading:\n{dest}\n\n'
            f'Detail: {detail}\n\n'
            'Windows may ask for administrator permission so Setup can prepare this folder.'
        )
        return result
    except Exception as exc:
        detail = _format_os_error(exc)
        diag_log(f'listdir failed dest={dest!r} err={detail}')
        result['error'] = f'Cannot read destination:\n{dest}\n\nDetail: {detail}'
        result['reason'] = REASON_ACCESS_DENIED
        result['detail'] = detail
        return result

    if entries:
        has_huge = os.path.isfile(os.path.join(dest, 'Huge.exe'))
        if not has_huge:
            diag_log(f'non-empty non-Huge dest={dest!r} entries={entries[:8]!r}')
            result['error'] = (
                'The selected folder is not empty and does not look like an existing Huge install.\n'
                'Choose an empty folder, or a folder that already contains Huge.exe.'
            )
            result['reason'] = REASON_NONEMPTY
            return result

    ok_probe, probe_err, probe_exc = _probe_writable_dir(dest)
    if not ok_probe:
        diag_log(f'write probe failed dest={dest!r} err={probe_err} drive_type={dtype} created={created}')
        if probe_exc is not None and _is_access_denied(probe_exc):
            result['ok'] = True
            result['needs_elevation'] = True
            result['reason'] = REASON_NEEDS_ELEVATION
            result['detail'] = probe_err
            result['error'] = (
                f'Huge cannot write application files at:\n{dest}\n\n'
                f'Detail: {probe_err}\n'
                f'Drive type: {dtype}\n'
                f'Probe folder: {dest}\n\n'
                'Windows may ask for administrator permission. Setup will prepare this '
                'folder for your Windows account only (not the whole drive). '
                'Your current Huge install and profile stay intact if you cancel or if Setup fails.\n'
                f'Diagnostic log:\n{diag_log_path()}'
            )
            diag_log(f'assess needs_elevation (probe) dest={dest!r}')
            return result
        result['error'] = (
            f'Cannot write to:\n{dest}\n\n'
            f'Detail: {probe_err}\n'
            f'Drive type: {dtype}\n'
            f'Probe folder: {dest}\n\n'
            f'Diagnostic log:\n{diag_log_path()}'
        )
        result['reason'] = REASON_ACCESS_DENIED
        result['detail'] = probe_err
        return result

    if required_bytes > 0:
        try:
            import shutil
            usage = shutil.disk_usage(root)
            if usage.free < required_bytes + (8 * 1024 * 1024):
                need_mb = max(1, (required_bytes + 8 * 1024 * 1024) // (1024 * 1024))
                free_mb = usage.free // (1024 * 1024)
                msg = (
                    f'Not enough free space on {drive}.\n'
                    f'Need about {need_mb} MB free; {free_mb} MB available.'
                )
                diag_log(msg)
                result['error'] = msg
                result['reason'] = REASON_NO_SPACE
                return result
        except Exception as exc:
            diag_log(f'disk_usage skipped: {_format_os_error(exc)}')

    diag_log(f'assess OK dest={dest!r} drive_type={dtype} needs_elevation=0')
    result['ok'] = True
    result['needs_elevation'] = False
    result['reason'] = REASON_OK
    result['error'] = ''
    return result


def validate_dest_dir(dest: str, required_bytes: int = 0) -> Tuple[bool, str]:
    """Return (ok, error_message_or_normalized_path).

    Destinations that only need elevation still return ok=True with the path,
    matching assess_dest_dir. Callers that must require immediate write access
    should check assess_dest_dir()['needs_elevation'].
    """
    assessed = assess_dest_dir(dest, required_bytes=required_bytes)
    if assessed.get('ok'):
        return True, str(assessed.get('dest') or dest)
    return False, str(assessed.get('error') or 'Invalid destination.')


def install_info() -> dict:
    path = current_install_dir()
    frozen = is_frozen_install()
    reg = read_registry_install_dir()
    return {
        'path': path,
        'frozen': frozen,
        'registry_path': reg,
        'default_path': default_install_dir(),
        'can_change': frozen,
        'exe': huge_exe_path(path) if frozen else '',
        'diag_log': diag_log_path(),
        'user_sid': current_user_sid(),
    }
