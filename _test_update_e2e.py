"""End-to-end update test: pretend we are v2.0.3, fetch v2.0.4 Setup, install with /update."""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

SETUP = os.path.join(ROOT, 'release', 'Huge-v2.0.4-Setup.exe')
INSTALL = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Huge')
PROFILE = os.path.join(os.environ.get('APPDATA', ''), 'HugeWatchParty', 'profile.json')


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label, (detail or ''))
    return bool(cond)


def main():
    if not os.path.isfile(SETUP):
        print('FAIL - Setup missing', SETUP)
        return 1

    # Kill Huge if running
    subprocess.run(['taskkill', '/F', '/IM', 'Huge.exe'], capture_output=True)
    time.sleep(0.5)

    before_profile = None
    if os.path.isfile(PROFILE):
        with open(PROFILE, 'r', encoding='utf-8') as fh:
            before_profile = fh.read()

    # Confirm starting from 2.0.3 install (reinstall 2.0.3 if needed)
    ver_path = os.path.join(INSTALL, '_internal', 'VERSION.txt')
    archive_setup = os.path.join(
        ROOT, 'release-archive', 'v2.0.3-20261009-024652', 'Huge-v2.0.3-Setup.exe'
    )
    # Find any archived 2.0.3 setup
    if not os.path.isfile(archive_setup):
        for dirpath, _, files in os.walk(os.path.join(ROOT, 'release-archive')):
            if 'Huge-v2.0.3-Setup.exe' in files:
                archive_setup = os.path.join(dirpath, 'Huge-v2.0.3-Setup.exe')
                break

    need_203 = True
    if os.path.isfile(ver_path):
        cur = open(ver_path, encoding='utf-8').read().strip()
        need_203 = cur != '2.0.3'
        print('Current install version:', cur)
    if need_203:
        if not os.path.isfile(archive_setup):
            print('FAIL - cannot restore v2.0.3 install; archive Setup missing')
            return 1
        print('Restoring v2.0.3 install for e2eâ€¦')
        subprocess.run([archive_setup, '/update'], check=False)
        time.sleep(2)
        subprocess.run(['taskkill', '/F', '/IM', 'Huge.exe'], capture_output=True)
        time.sleep(0.5)
        cur = open(ver_path, encoding='utf-8').read().strip() if os.path.isfile(ver_path) else '?'
        if not ok('baseline install is 2.0.3', cur == '2.0.3', cur):
            return 1

    # Local HTTP server serving the v2.0.4 Setup (simulates GitHub asset CDN)
    sha = hashlib.sha256(open(SETUP, 'rb').read()).hexdigest()
    handler_dir = os.path.dirname(SETUP)
    asset_name = os.path.basename(SETUP)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=handler_dir, **k)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    asset_url = f'http://127.0.0.1:{port}/{asset_name}'

    import engine.updater as u
    from engine.updater import UpdateManager

    u.ALLOWED_HOSTS = frozenset(set(u.ALLOWED_HOSTS) | {'127.0.0.1', 'localhost'})

    def fake_fetch():
        return {
            'version': '2.0.4',
            'tag': 'v2.0.4',
            'name': 'Huge v2.0.4',
            'body': 'Movie playback race fix.',
            'html_url': 'https://github.com/vsnq0/Huge/releases/tag/v2.0.4',
            'asset_name': asset_name,
            'asset_url': asset_url,
            'asset_size': os.path.getsize(SETUP),
            'sha256': sha,
            'published_at': '',
        }

    u.fetch_latest_release = fake_fetch  # type: ignore

    mgr = UpdateManager('2.0.3')
    mgr._check(force=True)
    snap = mgr.snapshot()
    if not ok('2.0.3 detects 2.0.4', snap.get('update_available') and snap.get('latest_version') == '2.0.4', str(snap)):
        httpd.shutdown()
        return 1

    installed_path = {'path': ''}
    done = threading.Event()

    def handoff(path: str):
        installed_path['path'] = path
        # Run the same installer flags the real app uses (without exiting this test process).
        subprocess.Popen(
            [path, '/update', f'/relaunch={os.path.join(INSTALL, "Huge.exe")}'],
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
        done.set()

    mgr.download_and_install_async(handoff)
    # Wait for download + handoff
    for _ in range(120):
        if done.is_set():
            break
        st = mgr.snapshot()
        if st.get('status') == 'error':
            print('FAIL - download error', st.get('error'))
            httpd.shutdown()
            return 1
        time.sleep(0.5)
    else:
        print('FAIL - download timed out', mgr.snapshot())
        httpd.shutdown()
        return 1

    snap2 = mgr.snapshot()
    ok('SHA-256 verified', snap2.get('sha256_verified') is True, str(snap2.get('sha256_verified')))
    ok('downloaded installer exists', os.path.isfile(installed_path['path']), installed_path['path'])

    # Wait for Setup to finish and relaunch
    time.sleep(8)
    subprocess.run(['taskkill', '/F', '/IM', 'Huge.exe'], capture_output=True)
    time.sleep(0.5)

    new_ver = open(ver_path, encoding='utf-8').read().strip() if os.path.isfile(ver_path) else '?'
    ok('installed version is 2.0.4', new_ver == '2.0.4', new_ver)
    ok('runtime present', os.path.isfile(os.path.join(INSTALL, '_internal', 'python313.dll')))
    ok('Huge.exe present', os.path.isfile(os.path.join(INSTALL, 'Huge.exe')))

    if before_profile and os.path.isfile(PROFILE):
        after = open(PROFILE, encoding='utf-8').read()
        # Compare key fields (name / pot path) â€” whitespace may differ
        try:
            b = json.loads(before_profile)
            a = json.loads(after)
            ok('profile name preserved', a.get('name') == b.get('name'), f"{b.get('name')} -> {a.get('name')}")
            ok('potplayer path preserved', a.get('potplayer_path') == b.get('potplayer_path'))
            ok('avatar preserved', bool(a.get('avatar_b64')) == bool(b.get('avatar_b64')))
        except Exception as exc:
            ok('profile parse', False, str(exc))
    else:
        print('PASS - profile check skipped (no prior profile)')

    # Launch briefly
    p = subprocess.Popen([os.path.join(INSTALL, 'Huge.exe')])
    time.sleep(4)
    alive = p.poll() is None
    ok('Huge launches after update', alive)
    subprocess.run(['taskkill', '/F', '/IM', 'Huge.exe'], capture_output=True)

    httpd.shutdown()
    print('--- e2e done ---')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
