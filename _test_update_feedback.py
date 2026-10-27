"""Updater feedback, integrity, and download-phase timing."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.updater import (  # noqa: E402
    DOWNLOAD_CHUNK,
    UpdateManager,
    installer_launch_plan,
    pick_setup_asset,
    version_gt,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


class _Resp:
    def __init__(self, data, fail=False):
        self._data = data
        self._off = 0
        self.headers = {'Content-Length': str(len(data))}
        self.fail = fail

    def read(self, n):
        if self.fail:
            raise OSError('network down')
        if self._off >= len(self._data):
            return b''
        chunk = self._data[self._off:self._off + n]
        self._off += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _prime(mgr, payload, name='Huge-v2.0.35-Setup.exe'):
    digest = hashlib.sha256(payload).hexdigest()
    mgr.state.update(
        status='available',
        update_available=True,
        latest_version='2.0.35',
        asset_name=name,
        asset_url=f'http://127.0.0.1/updates/{name}',
        asset_size=len(payload),
        sha256=digest,
        release_name='Huge v2.0.35',
    )
    return digest


def test_version_and_asset_choice():
    passed = ok('2.0.35 is newer than 2.0.34', version_gt('2.0.35', '2.0.34'))
    passed &= ok('same version is not newer', version_gt('2.0.34', '2.0.34') is False)
    asset = pick_setup_asset({
        'assets': [
            {'name': 'Huge-v2.0.35-windows-portable.zip'},
            {'name': 'Huge Setup.exe'},
            {'name': 'Huge-v2.0.35-Setup.exe', 'digest': 'sha256:abc'},
        ],
    })
    passed &= ok('versioned setup chosen', asset and asset['name'] == 'Huge-v2.0.35-Setup.exe', asset)
    plan = installer_launch_plan(
        r'C:\temp\Huge-v2.0.35-Setup.exe',
        r'D:\Huge\Huge.exe',
        install_dir=r'D:\Huge',
    )
    joined = ' '.join(plan['params'])
    passed &= ok('custom dir', '/DIR=' in joined and plan['target_dir'].endswith('Huge'), joined)
    passed &= ok('relaunch exe', '/relaunch=' in joined and joined.endswith('Huge.exe') or 'Huge.exe' in joined, joined)
    passed &= ok('update flag', plan['params'][0] == '/update')
    return passed


def test_phases_hash_and_failures():
    import engine.updater as updater
    updater.ALLOWED_HOSTS = frozenset(set(updater.ALLOWED_HOSTS) | {'127.0.0.1'})
    payload = b'huge-setup' * 200000
    folder = tempfile.mkdtemp(prefix='huge-upd-')
    old_local = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = folder
    events = []

    def on_event(snap):
        events.append((snap.get('status'), snap.get('message'), snap.get('error')))

    mgr = UpdateManager('2.0.34', on_event=on_event)
    digest = _prime(mgr, payload)
    calls = {'n': 0}

    def opener(req, timeout=60, context=None):
        calls['n'] += 1
        return _Resp(payload)

    try:
        t0 = time.perf_counter()
        handed = []
        mgr._download_and_install(lambda path: handed.append(path), opener=opener)
        elapsed = time.perf_counter() - t0
        passed = ok('handoff once', len(handed) == 1 and os.path.isfile(handed[0]), handed)
        passed &= ok('sha verified', mgr.snapshot().get('sha256_verified') is True)
        passed &= ok('installing', mgr.snapshot().get('status') == 'installing', mgr.snapshot().get('status'))
        messages = [item[1] for item in events]
        passed &= ok('preparing first', events and events[0][0] == 'preparing', events[:2])
        passed &= ok('saw downloading', any(item[0] == 'downloading' for item in events))
        passed &= ok('saw verifying', any(item[0] == 'verifying' for item in events))
        passed &= ok('timings recorded', 'download_ms' in (mgr.snapshot().get('timings') or {}), mgr.snapshot().get('timings'))
        download_emits = [item for item in events if item[0] == 'downloading']
        old_emits = max(1, (len(payload) + DOWNLOAD_CHUNK - 1) // DOWNLOAD_CHUNK)
        print(
            f'TIMING new_download_s={elapsed:.3f} new_download_emits={len(download_emits)} '
            f'old_model_emits={old_emits} old_model_block_s={old_emits * 0.08:.2f}'
        )
        passed &= ok('progress emits throttled', len(download_emits) < old_emits / 2, len(download_emits))

        events.clear()
        mgr2 = UpdateManager('2.0.34', on_event=lambda snap: events.append(snap.get('status')))
        _prime(mgr2, payload)
        mgr2.state['sha256'] = '0' * 64
        mgr2._download_and_install(lambda path: handed.append(path), opener=opener)
        passed &= ok('hash mismatch', mgr2.snapshot().get('status') == 'error' and 'checksum' in mgr2.snapshot().get('error', ''), mgr2.snapshot().get('error'))
        passed &= ok('not busy after mismatch', mgr2._busy is False)

        events.clear()
        mgr3 = UpdateManager('2.0.34', on_event=lambda snap: events.append(snap.get('status')))
        _prime(mgr3, payload)

        def fail_open(req, timeout=60, context=None):
            return _Resp(payload, fail=True)

        mgr3._download_and_install(lambda path: None, opener=fail_open)
        passed &= ok('download failure', mgr3.snapshot().get('status') == 'error' and 'not modified' in mgr3.snapshot().get('error', ''), mgr3.snapshot().get('error'))
        passed &= ok('retry allowed', mgr3._busy is False)

        mgr4 = UpdateManager('2.0.34')
        _prime(mgr4, payload)
        launched = []
        mgr4._download_and_install(
            lambda path: (_ for _ in ()).throw(OSError('installer blocked')),
            opener=opener,
        )
        passed &= ok(
            'launch failure stays usable',
            mgr4.snapshot().get('status') == 'error' and 'Could not start the installer' in mgr4.snapshot().get('error', ''),
            mgr4.snapshot().get('error'),
        )
        passed &= ok('launch failure not busy', mgr4._busy is False)
        passed &= ok('digest still the published one', digest == hashlib.sha256(payload).hexdigest())
        passed &= ok('messages included preparing', 'Preparing update...' in messages)
        return passed
    finally:
        if old_local is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_local


def test_duplicate_click_and_cache():
    import engine.updater as updater
    updater.ALLOWED_HOSTS = frozenset(set(updater.ALLOWED_HOSTS) | {'127.0.0.1'})
    payload = b'x' * 1024
    folder = tempfile.mkdtemp(prefix='huge-upd2-')
    old_local = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = folder
    release = threading.Event()
    original = updater.urllib.request.urlopen

    def blocking_urlopen(req, timeout=60, context=None):
        release.wait(3)
        return _Resp(payload)

    updater.urllib.request.urlopen = blocking_urlopen
    mgr = UpdateManager('2.0.34')
    _prime(mgr, payload)
    try:
        first = mgr.download_and_install_async(lambda path: None)
        passed = ok('first start accepted', first is True)
        passed &= ok(
            'preparing immediately',
            mgr.snapshot().get('status') in ('preparing', 'downloading', 'verifying', 'installing'),
            mgr.snapshot().get('status'),
        )
        second = mgr.download_and_install_async(lambda path: None)
        passed &= ok('second click refused', second is False)
        release.set()
        for _ in range(40):
            if mgr.snapshot().get('status') in ('installing', 'error', 'idle'):
                break
            time.sleep(0.05)

        calls = {'n': 0}

        def counting(req, timeout=60, context=None):
            calls['n'] += 1
            return _Resp(payload)

        dest_dir = os.path.join(folder, 'Huge', 'updates')
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, 'Huge-v2.0.35-Setup.exe')
        with open(dest, 'wb') as fh:
            fh.write(payload)
        mgr5 = UpdateManager('2.0.34')
        _prime(mgr5, payload)
        handed = []
        mgr5._download_and_install(lambda path: handed.append(path), opener=counting)
        passed &= ok('cached installer skipped download', calls['n'] == 0 and len(handed) == 1, calls)
        passed &= ok('cache still verified', mgr5.snapshot().get('sha256_verified') is True)
        return passed
    finally:
        release.set()
        updater.urllib.request.urlopen = original
        if old_local is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_local


def main():
    tests = [
        test_version_and_asset_choice,
        test_phases_hash_and_failures,
        test_duplicate_click_and_cache,
    ]
    results = []
    for fn in tests:
        print(f'\n=== {fn.__name__} ===')
        try:
            results.append(bool(fn()))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print('FAIL - exception', exc)
            results.append(False)
    print(f'\n{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
