"""Build huge.ico from the official huge-logo.png without redesigning it."""
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGO = ROOT / 'huge-logo.png'
ICO = ROOT / 'huge.ico'
SIZES = (16, 24, 32, 48, 64, 128, 256)


def dump_bgra(src_png: Path):
    dest = src_png.parent
    ps = r'''
Add-Type -AssemblyName System.Drawing
$src = $args[0]
$destDir = $args[1]
$img = [System.Drawing.Bitmap]::FromFile($src)

# Crop transparent padding only — keep the logo artwork unchanged.
$minX = $img.Width; $minY = $img.Height; $maxX = -1; $maxY = -1
$lockRect = New-Object System.Drawing.Rectangle 0, 0, $img.Width, $img.Height
$data = $img.LockBits($lockRect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$stride = $data.Stride
$buf = New-Object byte[] ($stride * $img.Height)
[System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $buf, 0, $buf.Length)
$img.UnlockBits($data)
for ($y = 0; $y -lt $img.Height; $y++) {
  $row = $y * $stride
  for ($x = 0; $x -lt $img.Width; $x++) {
    if ($buf[$row + $x * 4 + 3] -lt 8) { continue }
    if ($x -lt $minX) { $minX = $x }
    if ($y -lt $minY) { $minY = $y }
    if ($x -gt $maxX) { $maxX = $x }
    if ($y -gt $maxY) { $maxY = $y }
  }
}
if ($maxX -lt $minX) { throw 'Logo PNG has no opaque pixels' }
$cw = $maxX - $minX + 1
$ch = $maxY - $minY + 1
$cropRect = New-Object System.Drawing.Rectangle $minX, $minY, $cw, $ch
$cropped = $img.Clone($cropRect, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$img.Dispose()
Write-Host ("cropped ${cw}x${ch}")

foreach ($s in @(16,24,32,48,64,128,256)) {
  $bmp = New-Object System.Drawing.Bitmap $s, $s
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
  $g.Clear([System.Drawing.Color]::FromArgb(0,0,0,0))

  # Fit the original logo into the square; do not recolor or restyle.
  $pad = [Math]::Max(0, [int]($s * 0.04))
  $box = $s - (2 * $pad)
  $scale = [Math]::Min($box / [double]$cropped.Width, $box / [double]$cropped.Height)
  $dw = [Math]::Max(1, [int]($cropped.Width * $scale))
  $dh = [Math]::Max(1, [int]($cropped.Height * $scale))
  $dx = [int](($s - $dw) / 2)
  $dy = [int](($s - $dh) / 2)
  $g.DrawImage($cropped, $dx, $dy, $dw, $dh)
  $g.Dispose()

  $rectLock = New-Object System.Drawing.Rectangle 0, 0, $s, $s
  $bits = $bmp.LockBits($rectLock, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $bytes = New-Object byte[] ($s * $s * 4)
  [System.Runtime.InteropServices.Marshal]::Copy($bits.Scan0, $bytes, 0, $bytes.Length)
  $bmp.UnlockBits($bits)
  [System.IO.File]::WriteAllBytes((Join-Path $destDir ("icon-{0}.bgra" -f $s)), $bytes)
  $bmp.Dispose()
}
$cropped.Dispose()
'''
    script = dest / '_resize_icon.ps1'
    script.write_text(ps, encoding='utf-8')
    subprocess.run(
        ['powershell', '-NoProfile', '-File', str(script), str(src_png), str(dest)],
        check=True,
    )
    script.unlink(missing_ok=True)
    return [dest / f'icon-{s}.bgra' for s in SIZES]


def dib_from_bgra(size: int, top_down_bgra: bytes) -> bytes:
    stride = size * 4
    xor = bytearray(stride * size)
    for y in range(size):
        src = (size - 1 - y) * stride
        xor[y * stride:(y + 1) * stride] = top_down_bgra[src:src + stride]
    mask_stride = ((size + 31) // 32) * 4
    mask = bytearray(mask_stride * size)
    for y in range(size):
        src_y = size - 1 - y
        for x in range(size):
            alpha = top_down_bgra[(src_y * size + x) * 4 + 3]
            if alpha == 0:
                mask[y * mask_stride + (x >> 3)] |= 0x80 >> (x & 7)
    header = struct.pack(
        '<IIIHHIIIIII',
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(xor) + len(mask),
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor) + bytes(mask)


def write_ico(bgra_paths, dest: Path):
    images = []
    for path in bgra_paths:
        size = int(path.stem.split('-')[1])
        images.append((size, dib_from_bgra(size, path.read_bytes())))
    count = len(images)
    offset = 6 + 16 * count
    entries = []
    blobs = []
    for size, data in images:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries.append(struct.pack('<BBBBHHII', width, height, 0, 0, 1, 32, len(data), offset))
        blobs.append(data)
        offset += len(data)
    dest.write_bytes(struct.pack('<HHH', 0, 1, count) + b''.join(entries) + b''.join(blobs))


def main():
    if not LOGO.is_file():
        raise SystemExit(f'Missing official logo: {LOGO}')
    paths = dump_bgra(LOGO)
    write_ico(paths, ICO)
    for path in paths:
        path.unlink(missing_ok=True)
    print('wrote', ICO, ICO.stat().st_size)


if __name__ == '__main__':
    main()
