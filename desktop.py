import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path

import webview

# When packaged as Huge.exe (PyInstaller onedir), bundled assets live in
# sys._MEIPASS (usually the _internal folder). Dev runs use this source folder.
# Behavior of the app is unchanged.
if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    ROOT = getattr(sys, '_MEIPASS', EXE_DIR)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT = EXE_DIR
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.session import APP_NAME, APP_VERSION, HugeEngine
from engine import install_location as install_loc
from engine.updater import (
    UpdateManager,
    download_setup_for_migration,
    launch_installer_and_exit,
)

_ICON_CANDIDATES = (
    os.path.join(EXE_DIR, 'huge.ico'),
    os.path.join(ROOT, 'huge.ico'),
)
ICON_PATH = next((p for p in _ICON_CANDIDATES if os.path.isfile(p)), _ICON_CANDIDATES[0])
APP_USER_MODEL_ID = 'vsnq.Huge'


def ui_index():
    return os.path.join(ROOT, 'ui', 'dist', 'index.html')


def _claim_app_id():
    if os.name != 'nt':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _hwnd_for_pid(pid, title='Huge'):
    found = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value != title:
            return True
        proc = ctypes.c_uint(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid:
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _load_icon_handle(icon_path, size):
    user32 = ctypes.windll.user32
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    return user32.LoadImageW(None, icon_path, IMAGE_ICON, size, size, LR_LOADFROMFILE)


def _ensure_window_size(window, width, height):
    """Some hosts start the WinForms window tiny; force a usable client size."""
    if os.name != 'nt':
        return
    try:
        hwnd = None
        if window is not None and getattr(window, 'native', None) is not None:
            try:
                hwnd = int(window.native.Handle.ToInt64())
            except Exception:
                hwnd = None
        if not hwnd:
            hwnd = _hwnd_for_pid(os.getpid())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        cur_w = int(rect.right - rect.left)
        cur_h = int(rect.bottom - rect.top)
        if cur_w >= 800 and cur_h >= 500:
            return
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(hwnd, 0, 60, 30, int(width), int(height), SWP_NOZORDER | SWP_SHOWWINDOW)
    except Exception:
        pass


def _apply_window_icon(window, icon_path):
    """Set the title-bar / taskbar icon via Win32 only.

    Avoid System.Drawing.Icon / Form.Icon here: pywebview already walks the
    native form for JS bridging, and assigning Icon on the UI thread has
    hung Huge before. LoadImage + WM_SETICON is enough for the chrome icon.
    """
    if os.name != 'nt' or not icon_path or not os.path.isfile(icon_path):
        return
    try:
        hwnd = None
        if window is not None and getattr(window, 'native', None) is not None:
            try:
                hwnd = int(window.native.Handle.ToInt64())
            except Exception:
                hwnd = None
        if not hwnd:
            hwnd = _hwnd_for_pid(os.getpid())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        WM_SETICON = 0x0080
        small = _load_icon_handle(icon_path, 16)
        big = _load_icon_handle(icon_path, 32)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, small)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, big)
    except Exception:
        pass


class Api:
    """Only public methods are exposed to JavaScript.

    Private attributes must stay underscored. pywebview walks every public
    attribute on this object at startup; a live Window/engine here used to
    recurse through WinForms AccessibilityObject.Bounds.Empty and freeze
    the UI thread.
    """

    def __init__(self, engine: HugeEngine, updater=None):
        self._engine = engine
        self._updater = updater
        self._window = None
        self._update_ui = {
            'status': 'idle',
            'current_version': APP_VERSION,
            'latest_version': '',
            'update_available': False,
            'release_name': '',
            'release_notes': '',
            'html_url': '',
            'asset_name': '',
            'progress': 0.0,
            'bytes_done': 0,
            'bytes_total': 0,
            'error': '',
            'message': '',
            'sha256_verified': False,
            'sha256_skipped_reason': '',
            'banner_dismissed': False,
        }
        self._install_ui = {
            'path': install_loc.current_install_dir(),
            'frozen': install_loc.is_frozen_install(),
            'can_change': install_loc.is_frozen_install(),
            'default_path': install_loc.default_install_dir(),
            'status': 'idle',
            'message': '',
            'error': '',
            'progress': 0.0,
        }
        self._migrate_lock = threading.Lock()
        self._migrate_cancel = threading.Event()
        self._push_lock = threading.Lock()

    def _on_update_event(self, snapshot: dict):
        self._update_ui = {
            'status': snapshot.get('status') or 'idle',
            'current_version': snapshot.get('current_version') or APP_VERSION,
            'latest_version': snapshot.get('latest_version') or '',
            'update_available': bool(snapshot.get('update_available')),
            'release_name': snapshot.get('release_name') or '',
            'release_notes': snapshot.get('release_notes') or '',
            'html_url': snapshot.get('html_url') or '',
            'asset_name': snapshot.get('asset_name') or '',
            'progress': float(snapshot.get('progress') or 0),
            'bytes_done': int(snapshot.get('bytes_done') or 0),
            'bytes_total': int(snapshot.get('bytes_total') or 0),
            'error': snapshot.get('error') or '',
            'message': snapshot.get('message') or '',
            'sha256_verified': bool(snapshot.get('sha256_verified')),
            'sha256_skipped_reason': snapshot.get('sha256_skipped_reason') or '',
            'banner_dismissed': bool(snapshot.get('banner_dismissed')),
        }
        try:
            self._engine._push()
        except Exception:
            pass

    def _push_state_to_ui(self, _partial=None):
        """Push full state to React immediately (Live Log, lobby, update progress)."""
        win = self._window
        if not win:
            return
        try:
            payload = json.dumps(self.get_state(), ensure_ascii=False)
        except Exception:
            return
        js = f'window.__hugePush&&window.__hugePush({payload})'
        with self._push_lock:
            try:
                win.evaluate_js(js)
            except Exception:
                pass

    def _dialog(self, file_types=None, directory=False):
        if not self._window:
            return None
        kind = webview.FileDialog.FOLDER if directory else webview.FileDialog.OPEN
        kwargs = {'allow_multiple': False}
        if not directory and file_types:
            kwargs['file_types'] = file_types
        result = self._window.create_file_dialog(kind, **kwargs)
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return result[0] if result else None
        return result

    def get_state(self):
        state = self._engine.ui_state()
        state['app_version'] = APP_VERSION
        state['update'] = dict(self._update_ui)
        info = install_loc.install_info()
        state['install'] = {
            **self._install_ui,
            'path': info['path'],
            'frozen': info['frozen'],
            'can_change': info['can_change'] and self._install_ui.get('status') not in ('downloading', 'installing'),
            'default_path': info['default_path'],
            'diag_log': info.get('diag_log') or install_loc.diag_log_path(),
        }
        return state

    def get_install_info(self):
        info = install_loc.install_info()
        return {
            **self._install_ui,
            'path': info['path'],
            'frozen': info['frozen'],
            'can_change': info['can_change'] and self._install_ui.get('status') not in ('downloading', 'installing'),
            'default_path': info['default_path'],
            'diag_log': info.get('diag_log') or install_loc.diag_log_path(),
            'ok': True,
        }

    def browse_install_location(self):
        if not install_loc.is_frozen_install():
            return {'ok': False, 'error': 'Change installation location is only available in the installed Huge app.'}
        if self._install_ui.get('status') in ('downloading', 'installing'):
            return {'ok': False, 'error': 'A relocation is already in progress.'}
        if self._updater and self._updater.snapshot().get('status') in ('downloading', 'installing'):
            return {'ok': False, 'error': 'Finish or cancel the update before changing the installation location.'}
        selected = self._dialog(directory=True)
        if not selected:
            install_loc.diag_log('browse cancelled')
            return {'ok': False, 'cancelled': True}
        install_loc.diag_log(f'browse selected={selected!r}')
        try:
            dest = install_loc.normalize_dest_dir(selected)
        except ValueError as exc:
            install_loc.diag_log(f'browse normalize fail: {exc}')
            return {'ok': False, 'error': str(exc), 'reason': 'invalid'}
        assessed = install_loc.assess_dest_dir(dest)
        if not assessed.get('ok'):
            install_loc.diag_log(
                f"browse assess fail reason={assessed.get('reason')} detail={assessed.get('detail')!r}"
            )
            return {
                'ok': False,
                'error': assessed.get('error') or 'Could not use that destination.',
                'reason': assessed.get('reason'),
                'detail': assessed.get('detail') or '',
                'diag_log': install_loc.diag_log_path(),
            }
        dest = assessed.get('dest') or dest
        needs_elevation = bool(assessed.get('needs_elevation'))
        old = install_loc.current_install_dir()
        if install_loc.paths_same(old, dest):
            return {
                'ok': False,
                'same': True,
                'error': 'That folder is already the current installation location. No move is necessary.',
                'path': old,
                'dest': dest,
                'reason': 'same',
            }
        elev_note = (
            '\n\nWindows may ask for administrator permission so Setup can prepare '
            'this folder for your account. Cancel the prompt to keep your current install.'
            if needs_elevation else ''
        )
        install_loc.diag_log(
            f'browse OK old={old!r} dest={dest!r} needs_elevation={int(needs_elevation)} '
            f'reason={assessed.get("reason")} installer_not_launched_yet=1'
        )
        return {
            'ok': True,
            'path': old,
            'dest': dest,
            'needs_elevation': needs_elevation,
            'reason': assessed.get('reason') or 'ok',
            'detail': assessed.get('detail') or '',
            'confirm_message': (
                f'Move Huge from:\n{old}\n\nto:\n{dest}\n\n'
                'Huge will close and reopen after the move. '
                'Your profile and settings will be preserved.'
                f'{elev_note}'
            ),
            'diag_log': install_loc.diag_log_path(),
        }

    def start_install_migration(self, dest: str):
        if not install_loc.is_frozen_install():
            return {'ok': False, 'error': 'Change installation location is only available in the installed Huge app.'}
        if not self._migrate_lock.acquire(blocking=False):
            return {'ok': False, 'error': 'A relocation is already in progress.'}
        started = False
        try:
            if self._install_ui.get('status') in ('downloading', 'installing'):
                return {'ok': False, 'error': 'A relocation is already in progress.'}
            if self._updater and self._updater.snapshot().get('status') in ('downloading', 'installing'):
                return {'ok': False, 'error': 'Finish or cancel the update before changing the installation location.'}
            assessed = install_loc.assess_dest_dir(dest)
            if not assessed.get('ok'):
                install_loc.diag_log(
                    f"migrate assess fail reason={assessed.get('reason')} detail={assessed.get('detail')!r}"
                )
                return {
                    'ok': False,
                    'error': assessed.get('error') or 'Could not use that destination.',
                    'reason': assessed.get('reason'),
                    'diag_log': install_loc.diag_log_path(),
                }
            dest = assessed.get('dest') or dest
            needs_elevation = bool(assessed.get('needs_elevation'))
            old = install_loc.current_install_dir()
            if install_loc.paths_same(old, dest):
                return {
                    'ok': False,
                    'same': True,
                    'error': 'That folder is already the current installation location. No move is necessary.',
                }

            user_sid = install_loc.current_user_sid()
            install_loc.diag_log(
                f'migrate start old={old!r} dest={dest!r} needs_elevation={int(needs_elevation)} '
                f'user_sid={user_sid!r}'
            )
            self._migrate_cancel.clear()
            self._install_ui.update(status='downloading', message='Preparing relocation…', error='', progress=0.0)

            def emit(partial: dict):
                self._install_ui.update(
                    status=partial.get('status') or self._install_ui.get('status'),
                    message=partial.get('message') or '',
                    error=partial.get('error') or '',
                    progress=float(partial.get('progress') or self._install_ui.get('progress') or 0),
                )
                try:
                    self._engine._push()
                except Exception:
                    pass

            def worker():
                try:
                    setup = download_setup_for_migration(on_event=emit, cancel_event=self._migrate_cancel)
                    install_loc.diag_log(f'migrate setup downloaded path={setup!r}')
                    new_exe = os.path.join(dest, 'Huge.exe')
                    try:
                        self._engine.close()
                    except Exception:
                        pass
                    install_loc.diag_log(
                        f'migrate launching setup elevate={int(needs_elevation)} '
                        f'/DIR={dest!r} /cleanup-old={old!r} /relaunch={new_exe!r} /usersid={user_sid!r}'
                    )
                    launch_installer_and_exit(
                        setup,
                        new_exe,
                        install_dir=dest,
                        cleanup_old=old,
                        elevate=needs_elevation,
                        user_sid=user_sid,
                    )
                    install_loc.diag_log('migrate setup process started; exiting Huge')
                    try:
                        if self._window is not None:
                            self._window.destroy()
                    except Exception:
                        pass
                    os._exit(0)
                except PermissionError as exc:
                    # UAC cancelled — keep current install running.
                    install_loc.diag_log(
                        f'migrate UAC cancelled/failed: {exc!r}; old install intact at {old!r}'
                    )
                    self._install_ui.update(
                        status='error',
                        error=str(exc),
                        message='',
                    )
                    try:
                        self._engine._push()
                    except Exception:
                        pass
                except Exception as exc:
                    install_loc.diag_log(
                        f'migrate FAILED before/during setup launch: {exc!r}; '
                        f'old install intact at {old!r}; profile untouched'
                    )
                    self._install_ui.update(
                        status='error',
                        error=(
                            f'Relocation failed: {exc}. '
                            f'Your Huge install at {old} was not removed. '
                            'You can try again when online. '
                            f'Diagnostic log: {install_loc.diag_log_path()}'
                        ),
                        message='',
                    )
                    try:
                        self._engine._push()
                    except Exception:
                        pass
                finally:
                    try:
                        self._migrate_lock.release()
                    except Exception:
                        pass

            threading.Thread(target=worker, daemon=True, name='HugeInstallMigrate').start()
            started = True
            return {
                'ok': True,
                **self._install_ui,
                'path': old,
                'dest': dest,
                'needs_elevation': needs_elevation,
                'reason': assessed.get('reason'),
            }
        finally:
            if not started:
                try:
                    self._migrate_lock.release()
                except Exception:
                    pass

    def cancel_install_migration(self):
        self._migrate_cancel.set()
        if self._install_ui.get('status') == 'downloading':
            self._install_ui.update(status='idle', message='Relocation cancelled.', error='')
        return {'ok': True, **self._install_ui}

    def set_name(self, value):
        self._engine.set_name_draft(value)
        return {'ok': True}

    def flush_name(self, value):
        """Flush the final username (e.g. on blur) and persist immediately."""
        self._engine.set_name_draft(value)
        try:
            self._engine._save_profile()
        except Exception:
            pass
        return {'ok': True, 'username': self._engine.get_name()}

    def check_for_updates(self):
        if not self._updater:
            return {'ok': False, 'error': 'Updater unavailable.'}
        self._updater.check_async(force=True)
        return {'ok': True, **self._update_ui}

    def get_update_state(self):
        return dict(self._update_ui)

    def dismiss_update(self):
        if self._updater:
            self._updater.dismiss()
            self._on_update_event(self._updater.snapshot())
        return {'ok': True, **self._update_ui}

    def start_update(self):
        if not self._updater:
            return {'ok': False, 'error': 'Updater unavailable.'}
        snap = self._updater.snapshot()
        if snap.get('status') in ('downloading', 'installing'):
            return {'ok': False, 'error': 'An update is already in progress.'}
        if not snap.get('update_available'):
            return {'ok': False, 'error': 'No update available.'}

        def handoff(installer_path: str):
            exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
            install_dir = os.path.dirname(os.path.abspath(exe)) if getattr(sys, 'frozen', False) else install_loc.current_install_dir()
            try:
                # Keep registry in sync with the live install before Setup runs.
                install_loc.write_registry_install_dir(install_dir, APP_VERSION)
            except Exception:
                pass
            try:
                self._engine.close()
            except Exception:
                pass
            try:
                launch_installer_and_exit(installer_path, exe, install_dir=install_dir)
            except Exception as exc:
                self._on_update_event({
                    **self._updater.snapshot(),
                    'status': 'error',
                    'error': f'Could not start installer: {exc}',
                })
                return
            # Close the window after the installer process is spawned.
            try:
                if self._window is not None:
                    self._window.destroy()
            except Exception:
                pass
            os._exit(0)

        self._updater.download_and_install_async(handoff)
        return {'ok': True, **self._update_ui}

    def cancel_update_download(self):
        if self._updater:
            self._updater.cancel_download()
        return {'ok': True, **self._update_ui}

    def list_title_fonts(self):
        try:
            return {'ok': True, 'fonts': self._engine.list_title_fonts()}
        except Exception as exc:
            return {'ok': False, 'fonts': [], 'error': str(exc)}

    def set_title_font(self, value):
        return self._engine.set_title_font(value)

    def set_title_font_size(self, value):
        return self._engine.set_title_font_size(value)

    def set_lobby_name(self, value):
        self._engine.set_lobby_name_draft(value)
        return {'ok': True}

    def set_join_address(self, value):
        self._engine.set_join_address_draft(value)
        return {'ok': True}

    def create_lobby(self, name):
        self._engine.create_room(name)
        return {'ok': True}

    def join_lobby(self, address):
        self._engine.join_room(address)
        return {'ok': True}

    def leave_lobby(self):
        self._engine.leave_lobby()
        return {'ok': True}

    def copy_address(self):
        return self._engine.copy_address()

    def choose_avatar(self):
        path = self._dialog(('Pictures (*.png;*.jpg;*.jpeg;*.jfif;*.gif;*.bmp;*.tif;*.tiff;*.ico;*.webp)', 'All files (*.*)'))
        if not path:
            return {'ok': False, 'cancelled': True}
        return self._engine.prepare_image_edit(path, 'avatar')

    def choose_banner(self):
        path = self._dialog(('Pictures (*.png;*.jpg;*.jpeg;*.jfif;*.gif;*.bmp;*.tif;*.tiff;*.webp)', 'All files (*.*)'))
        if not path:
            return {'ok': False, 'cancelled': True}
        return self._engine.prepare_image_edit(path, 'banner')

    def save_image_crop(self, payload):
        return self._engine.save_image_crop(payload or {})

    def choose_potplayer(self):
        path = self._dialog(('PotPlayer (*.exe)', 'Executables (*.exe)', 'All files (*.*)'))
        if path:
            self._engine.apply_potplayer_path(path)
        return {'ok': True}

    def choose_movie(self):
        path = self._dialog((
            'Movie files (*.mp4;*.mkv;*.avi;*.mov;*.m4v;*.wmv;*.webm;*.ts;*.m2ts;*.flv)',
            'All files (*.*)',
        ))
        if path:
            self._engine.begin_select_movie(path)
        return {'ok': True}

    def toggle_ready(self):
        self._engine.toggle_ready()
        return {'ok': True}

    def ready_everyone(self):
        self._engine.host_ready_all()
        return {'ok': True}

    def start_movie(self):
        self._engine.force_start()
        return {'ok': True}

    def play_pause(self):
        self._engine.local_action('toggle_pause')
        return {'ok': True}

    def skip(self, ms):
        try:
            self._engine.local_action(f'seek_{int(ms)}')
        except Exception:
            pass
        return {'ok': True}

    def fullscreen(self):
        self._engine.local_action('fullscreen')
        return {'ok': True}

    def kick(self, user_id):
        self._engine.kick_user(str(user_id))
        return {'ok': True}

    def export_sync_logs(self):
        """Save playback_trace.zip to the Desktop for multiplayer debugging."""
        try:
            return self._engine.export_sync_logs()
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}


def main():
    _claim_app_id()
    index = ui_index()
    if not os.path.isfile(index):
        raise SystemExit(
            'Huge UI is not built yet.\n'
            'From the huge folder run:\n'
            '  cd ui\n'
            '  npm install\n'
            '  npm run build\n'
            '  cd ..\n'
            '  py -3 desktop.py'
        )

    engine = HugeEngine()
    updater = UpdateManager(APP_VERSION)
    api = Api(engine, updater)
    updater.on_event = api._on_update_event
    engine.subscribe(api._push_state_to_ui)
    icon = ICON_PATH if os.path.isfile(ICON_PATH) else None
    window = webview.create_window(
        APP_NAME,
        url=Path(index).resolve().as_uri(),
        js_api=api,
        width=1440,
        height=920,
        min_size=(1100, 720),
        background_color='#09090e',
    )
    api._window = window

    def on_shown():
        def apply():
            # Brief delay so the native handle is ready; never touch Form.Icon.
            import time
            time.sleep(0.2)
            _ensure_window_size(window, 1440, 920)
            _apply_window_icon(window, icon)
            _apply_window_icon(None, icon)
            # Background update check — must not block startup.
            time.sleep(1.5)
            try:
                updater.check_async(force=False)
            except Exception:
                pass

        threading.Thread(target=apply, daemon=True).start()

    def on_closing():
        engine.close()
        return True

    window.events.shown += on_shown
    window.events.closing += on_closing
    # Do not pass icon= on Windows: pywebview's Form.Icon assignment can hang.
    if os.name == 'nt':
        webview.start(debug=False)
    else:
        webview.start(debug=False, icon=icon)


if __name__ == '__main__':
    main()
