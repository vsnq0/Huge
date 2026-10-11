"""Headless verification for Huge v2.0.3 username, crop math, and updater."""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.updater import (  # noqa: E402
    UpdateManager,
    parse_version,
    pick_setup_asset,
    version_gt,
)
from engine.session import APP_VERSION, HugeEngine  # noqa: E402


def ok(label, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'[{status}] {label}' + (f' â€” {detail}' if detail else ''))
    return bool(cond)


def test_version():
    passed = True
    passed &= ok('APP_VERSION is 2.0.3', APP_VERSION == '2.0.3', APP_VERSION)
    passed &= ok('parse_version strips v', parse_version('v2.0.3') == (2, 0, 3))
    passed &= ok('version_gt newer', version_gt('2.0.3', '2.0.2'))
    passed &= ok('version_gt same is false', not version_gt('2.0.2', '2.0.2'))
    passed &= ok('version_gt older is false', not version_gt('2.0.1', '2.0.2'))
    return passed


def test_pick_asset():
    release = {
        'assets': [
            {'name': 'Huge-v2.0.3-windows-portable.zip', 'browser_download_url': 'https://github.com/x'},
            {
                'name': 'Huge-v2.0.3-Setup.exe',
                'browser_download_url': 'https://github.com/x/Setup.exe',
                'digest': 'sha256:abc',
            },
            {'name': 'notes.txt', 'browser_download_url': 'https://github.com/x'},
        ]
    }
    asset = pick_setup_asset(release)
    return ok(
        'pick_setup_asset prefers Setup.exe',
        asset and asset['name'].endswith('Setup.exe'),
        str(asset and asset['name']),
    )


def test_username_debounce():
    """Use a temp APPDATA so we never touch the real user profile."""
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        old_appdata = os.environ.get('APPDATA')
        os.environ['APPDATA'] = tmp
        try:
            engine = HugeEngine()
            pushes = []
            original_push = engine._push

            def counting_push(*_a, **_k):
                pushes.append(time.time())
                return original_push()

            engine._push = counting_push  # type: ignore
            name = ''
            for ch in 'FastUsernameTypingTest':
                name += ch
                engine.set_name_draft(name)
            after_draft = len(pushes)
            engine._save_profile()
            profile_path = engine._profile_file()
            loaded = HugeEngine()
            loaded._load_profile()
            passed &= ok(
                'set_name_draft does not _push each keystroke',
                after_draft == 0,
                f'pushes={after_draft}',
            )
            passed &= ok(
                'final username persisted',
                loaded.username == 'FastUsernameTypingTest',
                loaded.username,
            )
            passed &= ok('profile written under temp APPDATA', profile_path.startswith(tmp), profile_path)
        finally:
            if old_appdata is None:
                os.environ.pop('APPDATA', None)
            else:
                os.environ['APPDATA'] = old_appdata
    return passed


def _crop_math(iw, ih, fw, fh, zoom, ox, oy, sw, sh, avatar=False):
    """Mirror ImageEditor.tsx buildCrop."""
    cover = max(fw / iw, fh / ih)
    scale = cover * zoom
    drawn_w = iw * scale
    drawn_h = ih * scale
    max_ox = max(0, (drawn_w - fw) / 2)
    max_oy = max(0, (drawn_h - fh) / 2)
    ox = max(-max_ox, min(max_ox, ox))
    oy = max(-max_oy, min(max_oy, oy))
    img_left = (fw - drawn_w) / 2 + ox
    img_top = (fh - drawn_h) / 2 + oy
    px = -img_left / scale
    py = -img_top / scale
    pw = fw / scale
    ph = fh / scale
    if avatar:
        side = min(pw, ph)
        px += (pw - side) / 2
        py += (ph - side) / 2
        pw = ph = side
    px = max(0, min(iw - 1, px))
    py = max(0, min(ih - 1, py))
    pw = max(1, min(iw - px, pw))
    ph = max(1, min(ih - py, ph))
    sx, sy = sw / iw, sh / ih
    x, y, w, h = px * sx, py * sy, pw * sx, ph * sy
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    w = max(1, min(sw - x, w))
    h = max(1, min(sh - y, h))
    if avatar:
        side = min(w, h)
        x += (w - side) / 2
        y += (h - side) / 2
        w = h = side
    return x, y, w, h, img_left, img_top


def test_crop_math():
    passed = True
    cases = [
        ('square banner', 800, 800, 544, 200, 1.0, 0, 0, 800, 800, False),
        ('landscape banner', 1920, 1080, 544, 200, 1.0, 0, 0, 1920, 1080, False),
        ('portrait banner', 900, 1600, 544, 200, 1.5, 40, -20, 900, 1600, False),
        ('huge landscape', 4000, 2000, 740, 272, 2.0, -100, 50, 4000, 2000, False),
        ('square avatar', 512, 512, 340, 340, 1.0, 0, 0, 512, 512, True),
        ('portrait avatar', 900, 1600, 340, 340, 1.8, 30, -60, 900, 1600, True),
        ('landscape avatar', 1920, 1080, 340, 340, 1.2, -80, 10, 1920, 1080, True),
        ('tiny image', 120, 80, 340, 340, 1.0, 0, 0, 120, 80, True),
    ]
    for label, iw, ih, fw, fh, zoom, ox, oy, sw, sh, avatar in cases:
        x, y, w, h, img_left, img_top = _crop_math(iw, ih, fw, fh, zoom, ox, oy, sw, sh, avatar)
        in_bounds = (
            x >= 0
            and y >= 0
            and x + w <= sw + 0.5
            and y + h <= sh + 0.5
            and w > 0
            and h > 0
        )
        if avatar:
            in_bounds = in_bounds and abs(w - h) < 0.5
        cover = max(fw / iw, fh / ih)
        scale = cover * zoom
        drawn_w, drawn_h = iw * scale, ih * scale
        covers = (
            img_left <= 0.01
            and img_top <= 0.01
            and img_left + drawn_w >= fw - 0.01
            and img_top + drawn_h >= fh - 0.01
        )
        passed &= ok(f'crop bounds: {label}', in_bounds, f'x={x:.1f} y={y:.1f} w={w:.1f} h={h:.1f}')
        passed &= ok(f'no empty space: {label}', covers)
    return passed


def _make_quad_jpg(path: str, w=1600, h=900) -> bool:
    """Create a 4-quadrant JPEG via WPF (no Pillow dependency)."""
    ps = r'''
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$w = [int]$args[0]; $h = [int]$args[1]; $outPath = $args[2]
$dv = New-Object System.Windows.Media.DrawingVisual
$dc = $dv.RenderOpen()
$dc.DrawRectangle([System.Windows.Media.Brushes]::Red, $null, (New-Object System.Windows.Rect 0,0,($w/2),($h/2)))
$dc.DrawRectangle([System.Windows.Media.Brushes]::Lime, $null, (New-Object System.Windows.Rect ($w/2),0,($w/2),($h/2)))
$dc.DrawRectangle([System.Windows.Media.Brushes]::Blue, $null, (New-Object System.Windows.Rect 0,($h/2),($w/2),($h/2)))
$dc.DrawRectangle([System.Windows.Media.Brushes]::Yellow, $null, (New-Object System.Windows.Rect ($w/2),($h/2),($w/2),($h/2)))
$dc.Close()
$rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $w,$h,96,96,([System.Windows.Media.PixelFormats]::Pbgra32)
$rtb.Render($dv)
$enc = New-Object System.Windows.Media.Imaging.JpegBitmapEncoder
$enc.QualityLevel = 95
$enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
$fs = [System.IO.File]::Open($outPath, [System.IO.FileMode]::Create)
$enc.Save($fs); $fs.Dispose()
'''
    script = path + '.ps1'
    with open(script, 'w', encoding='utf-8') as fh:
        fh.write(ps)
    import subprocess

    proc = subprocess.run(
        [
            'powershell',
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            script,
            str(w),
            str(h),
            path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        os.remove(script)
    except Exception:
        pass
    return proc.returncode == 0 and os.path.isfile(path)


def _image_size(path: str):
    import struct

    with open(path, 'rb') as fh:
        data = fh.read(32)
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return struct.unpack('>II', data[16:24])
    if data[:2] == b'\xff\xd8':
        # Parse JPEG SOF
        with open(path, 'rb') as fh:
            fh.read(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2:
                    break
                if marker[0] != 0xFF:
                    break
                typ = marker[1]
                if typ in (0xC0, 0xC1, 0xC2):
                    fh.read(3)
                    h, w = struct.unpack('>HH', fh.read(4))
                    return w, h
                if typ == 0xD9 or typ == 0xDA:
                    break
                (seglen,) = struct.unpack('>H', fh.read(2))
                fh.read(seglen - 2)
    return None


def _png_center_rgb(path: str, cx: int, cy: int):
    """Read center-ish pixel via WPF."""
    ps = r'''
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$src = $args[0]; $x = [int]$args[1]; $y = [int]$args[2]
$fs = [System.IO.File]::OpenRead($src)
$dec = [System.Windows.Media.Imaging.BitmapDecoder]::Create($fs, [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat, [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
$frame = $dec.Frames[0]
$cropped = New-Object System.Windows.Media.Imaging.CroppedBitmap($frame, (New-Object System.Windows.Int32Rect $x,$y,1,1))
$bytes = New-Object byte[] 4
$cropped.CopyPixels($bytes, 4, 0)
$fs.Dispose()
Write-Output ("{0},{1},{2},{3}" -f $bytes[2],$bytes[1],$bytes[0],$bytes[3])
'''
    script = path + '_px.ps1'
    with open(script, 'w', encoding='utf-8') as fh:
        fh.write(ps)
    import subprocess

    proc = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, path, str(cx), str(cy)],
        capture_output=True,
        text=True,
    )
    try:
        os.remove(script)
    except Exception:
        pass
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or '').strip().split(',')
    if len(parts) < 3:
        return None
    return tuple(int(p) for p in parts[:3])


def test_native_crop_roundtrip():
    engine = HugeEngine()
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'src.jpg')
        if not _make_quad_jpg(src):
            return ok('create test JPEG via WPF', False)
        passed &= ok('create test JPEG via WPF', True)

        out_b = os.path.join(tmp, 'banner.jpg')
        ok_b, err_b = engine._native_crop_banner(src, out_b, 0, 200, 1600, 588, 1600)
        passed &= ok('native banner crop ok', ok_b, err_b or '')
        if ok_b and os.path.isfile(out_b):
            size = _image_size(out_b)
            if size:
                passed &= ok(
                    'banner aspect ~2.72',
                    abs(size[0] / size[1] - 2.72) < 0.15,
                    f'{size}',
                )
            else:
                passed &= ok('banner output readable', False)

        out_a = os.path.join(tmp, 'avatar.png')
        ok_a, err_a = engine._native_crop_avatar(src, out_a, 0, 0, 450, 450, 512)
        passed &= ok('native avatar crop ok', ok_a, err_a or '')
        if ok_a and os.path.isfile(out_a):
            size = _image_size(out_a)
            passed &= ok('avatar is square', size and size[0] == size[1], str(size))
            px = _png_center_rgb(out_a, 256, 256)
            passed &= ok(
                'avatar crop region matches source (red quadrant)',
                px is not None and px[0] > 200 and px[1] < 80,
                str(px),
            )
    return passed


def test_updater_live_api():
    mgr = UpdateManager('2.0.3')
    mgr._check(force=True)
    result = mgr.snapshot()
    status = result.get('status')
    latest = result.get('latest_version') or ''
    available = bool(result.get('update_available'))
    err = result.get('error') or ''
    passed = True
    if status == 'error':
        passed &= ok('updater API error handled gracefully', True, err)
    else:
        passed &= ok(
            'updater reached GitHub',
            status in ('idle', 'available', 'up_to_date', 'checked'),
            status,
        )
        if latest:
            if parse_version(latest) <= parse_version('2.0.3'):
                passed &= ok(
                    'no downgrade/reinstall of same-or-older',
                    not available,
                    f'latest={latest}',
                )
            else:
                passed &= ok('newer remote detected', available, f'latest={latest}')
    passed &= ok('2.0.2 would see 2.0.3 as update', version_gt('2.0.3', '2.0.2'))
    return passed


def test_updater_offline():
    mgr = UpdateManager('2.0.2')
    import engine.updater as u

    old = u.RELEASES_API
    u.RELEASES_API = 'https://127.0.0.1:1/repos/vsnq0/Huge/releases/latest'
    try:
        mgr._check(force=True)
        result = mgr.snapshot()
        passed = ok(
            'offline/API failure is graceful',
            result.get('status') == 'error' and bool(result.get('error')),
            (result.get('error') or '')[:120],
        )
    finally:
        u.RELEASES_API = old
    return passed



def test_invalid_asset_rejected():
    """Downloader must refuse non-GitHub URLs."""
    from engine.updater import _host_allowed

    passed = True
    passed &= ok('allows api.github.com', _host_allowed('https://api.github.com/repos/x'))
    passed &= ok(
        'allows objects.githubusercontent.com',
        _host_allowed('https://objects.githubusercontent.com/github-production-release-asset-2e65be/x'),
    )
    passed &= ok('rejects evil host', not _host_allowed('https://evil.example/Setup.exe'))
    return passed


def main():
    print('=== Huge v2.0.3 verification ===')
    print('APP_VERSION =', APP_VERSION)
    results = [
        ('version', test_version()),
        ('pick_asset', test_pick_asset()),
        ('username', test_username_debounce()),
        ('crop_math', test_crop_math()),
        ('native_crop', test_native_crop_roundtrip()),
        ('updater_api', test_updater_live_api()),
        ('updater_offline', test_updater_offline()),
        ('invalid_asset', test_invalid_asset_rejected()),
    ]
    failed = [n for n, p in results if not p]
    print('---')
    if failed:
        print('FAILED groups:', ', '.join(failed))
        return 1
    print('ALL GROUPS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
