"""Animated GIF validation, preview, crop, and lobby-thumb helpers (Pillow)."""
from __future__ import annotations

import base64
import io
import os
from typing import Optional, Tuple

# Hard limits — keep UI/network responsive.
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 2048
MAX_FRAMES = 120
MAX_PREVIEW_EDGE = 960
MAX_AVATAR_OUT = 512
MAX_BANNER_OUT_W = 1600
MAX_LOBBY_EDGE = 96
MAX_LOBBY_FRAMES = 48
MAX_LOBBY_BYTES = 120_000
MAX_DECODED_PIXELS = 40_000_000  # width*height*frames budget


def _pil():
    from PIL import Image, ImageSequence  # type: ignore
    return Image, ImageSequence


def is_gif_file(path: str) -> bool:
    try:
        with open(path, 'rb') as fh:
            head = fh.read(6)
        return head[:3] == b'GIF' and head[3:6] in (b'87a', b'89a')
    except Exception:
        return False


def is_gif_bytes(blob: bytes) -> bool:
    return bool(blob) and blob[:3] == b'GIF' and blob[3:6] in (b'87a', b'89a')


def sniff_image_mime(blob: bytes) -> str:
    if is_gif_bytes(blob):
        return 'image/gif'
    if len(blob) >= 8 and blob[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if len(blob) >= 2 and blob[:2] == b'\xff\xd8':
        return 'image/jpeg'
    return ''


def data_url(mime: str, b64: str) -> str:
    mime = mime or 'application/octet-stream'
    return f'data:{mime};base64,{b64}'


def strip_data_url(raw: str) -> Tuple[str, bytes]:
    s = str(raw or '').strip()
    mime = ''
    if s.startswith('data:'):
        try:
            header, payload = s.split(',', 1)
            mime = header.split(';')[0][5:]
            blob = base64.b64decode(payload)
            return mime, blob
        except Exception:
            return '', b''
    try:
        blob = base64.b64decode(s)
    except Exception:
        return '', b''
    return sniff_image_mime(blob), blob


def validate_gif_file(path: str) -> Tuple[bool, str, dict]:
    if not path or not os.path.isfile(path):
        return False, 'Could not open that picture.', {}
    try:
        size = os.path.getsize(path)
    except Exception:
        return False, 'Could not read that picture.', {}
    if size <= 0:
        return False, 'That picture file is empty.', {}
    if size > MAX_FILE_BYTES:
        return False, f'GIF is too large (max {MAX_FILE_BYTES // (1024 * 1024)} MB).', {}
    if not is_gif_file(path):
        return False, 'Not a GIF file.', {}
    Image, ImageSequence = _pil()
    try:
        with Image.open(path) as im:
            w, h = im.size
            if w < 1 or h < 1:
                return False, 'The GIF has no usable dimensions.', {}
            if w > MAX_DIMENSION or h > MAX_DIMENSION:
                return False, f'GIF dimensions are too large (max {MAX_DIMENSION}px).', {}
            n = 0
            for _ in ImageSequence.Iterator(im):
                n += 1
                if n > MAX_FRAMES:
                    return False, f'GIF has too many frames (max {MAX_FRAMES}).', {}
            if n < 1:
                return False, 'The GIF has no frames.', {}
            if w * h * n > MAX_DECODED_PIXELS:
                return False, 'GIF is too large to decode safely.', {}
            animated = n > 1 or bool(getattr(im, 'is_animated', False))
            return True, '', {
                'width': w,
                'height': h,
                'frames': n,
                'animated': animated,
                'loop': int(im.info.get('loop', 0) or 0),
            }
    except Exception as exc:
        return False, f'Could not read GIF: {exc}', {}


def is_animated_gif(path: str) -> bool:
    ok, _, meta = validate_gif_file(path)
    return bool(ok and meta.get('animated'))


def _frame_duration(frame, default=100) -> int:
    try:
        d = int(frame.info.get('duration', default) or default)
    except Exception:
        d = default
    return max(20, min(d, 5000))


def prepare_gif_preview(path: str, max_edge: int = MAX_PREVIEW_EDGE) -> Tuple[bool, str, dict]:
    ok, err, meta = validate_gif_file(path)
    if not ok:
        return False, err, {}
    Image, ImageSequence = _pil()
    try:
        with Image.open(path) as im:
            sw, sh = im.size
            scale = 1.0
            edge = max(sw, sh)
            if edge > max_edge:
                scale = max_edge / float(edge)
            pw = max(1, int(round(sw * scale)))
            ph = max(1, int(round(sh * scale)))
            frames = []
            durations = []
            for frame in ImageSequence.Iterator(im):
                rgba = frame.convert('RGBA')
                if scale < 1.0:
                    rgba = rgba.resize((pw, ph), Image.Resampling.LANCZOS)
                frames.append(rgba)
                durations.append(_frame_duration(frame))
            buf = io.BytesIO()
            save_kwargs = {
                'format': 'GIF',
                'save_all': True,
                'append_images': frames[1:] if len(frames) > 1 else [],
                'duration': durations,
                'loop': int(im.info.get('loop', 0) or 0),
                'disposal': 2,
                'optimize': False,
            }
            frames[0].save(buf, **save_kwargs)
            raw = buf.getvalue()
            b64 = base64.b64encode(raw).decode('ascii')
            return True, '', {
                'preview_b64': b64,
                'preview_width': pw,
                'preview_height': ph,
                'source_width': sw,
                'source_height': sh,
                'mime': 'image/gif',
                'animated': len(frames) > 1,
                'frames': len(frames),
            }
    except Exception as exc:
        return False, f'Could not prepare GIF preview: {exc}', {}


def crop_animated_gif(
    source: str,
    output: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    square: bool = False,
    out_size: Optional[int] = None,
    max_width: Optional[int] = None,
) -> Tuple[bool, str]:
    ok, err, _meta = validate_gif_file(source)
    if not ok:
        return False, err
    Image, ImageSequence = _pil()
    try:
        with Image.open(source) as im:
            fw, fh = im.size
            ix = int(max(0, min(fw - 1, round(x))))
            iy = int(max(0, min(fh - 1, round(y))))
            iw = int(max(1, round(w)))
            ih = int(max(1, round(h)))
            if square:
                side = max(1, min(iw, ih))
                ix = int(max(0, min(fw - side, ix + (iw - side) // 2)))
                iy = int(max(0, min(fh - side, iy + (ih - side) // 2)))
                iw = ih = side
            if ix + iw > fw:
                iw = fw - ix
            if iy + ih > fh:
                ih = fh - iy
            if iw < 1 or ih < 1:
                return False, 'Crop region is empty.'

            target_w, target_h = iw, ih
            if out_size and square:
                target_w = target_h = int(out_size)
            elif max_width and iw > max_width:
                scale = max_width / float(iw)
                target_w = int(max_width)
                target_h = max(1, int(round(ih * scale)))

            frames = []
            durations = []
            for frame in ImageSequence.Iterator(im):
                rgba = frame.convert('RGBA').crop((ix, iy, ix + iw, iy + ih))
                if (target_w, target_h) != (iw, ih):
                    rgba = rgba.resize((target_w, target_h), Image.Resampling.LANCZOS)
                frames.append(rgba)
                durations.append(_frame_duration(frame))
            if not frames:
                return False, 'GIF has no frames.'

            os.makedirs(os.path.dirname(os.path.abspath(output)) or '.', exist_ok=True)
            frames[0].save(
                output,
                format='GIF',
                save_all=True,
                append_images=frames[1:] if len(frames) > 1 else [],
                duration=durations,
                loop=int(im.info.get('loop', 0) or 0),
                disposal=2,
                optimize=False,
            )
        if not os.path.isfile(output) or os.path.getsize(output) <= 0:
            return False, 'Could not save cropped GIF.'
        return True, ''
    except Exception as exc:
        return False, f'Could not crop GIF: {exc}'


def make_lobby_gif_b64(source_path: str) -> str:
    """Compact animated (or static) GIF for lobby TCP transfer."""
    ok, err, meta = validate_gif_file(source_path)
    if not ok:
        return ''
    Image, ImageSequence = _pil()
    try:
        with Image.open(source_path) as im:
            w, h = im.size
            scale = min(1.0, MAX_LOBBY_EDGE / float(max(w, h)))
            tw = max(1, int(round(w * scale)))
            th = max(1, int(round(h * scale)))
            frames = []
            durations = []
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if i >= MAX_LOBBY_FRAMES:
                    break
                rgba = frame.convert('RGBA')
                if scale < 1.0:
                    rgba = rgba.resize((tw, th), Image.Resampling.BILINEAR)
                # Quantize for smaller wire size while keeping transparency.
                q = rgba.convert('P', palette=Image.Palette.ADAPTIVE, colors=64)
                frames.append(q)
                durations.append(_frame_duration(frame))
            if not frames:
                return ''
            buf = io.BytesIO()
            frames[0].save(
                buf,
                format='GIF',
                save_all=True,
                append_images=frames[1:] if len(frames) > 1 else [],
                duration=durations,
                loop=0,
                disposal=2,
                optimize=True,
            )
            raw = buf.getvalue()
            if len(raw) > MAX_LOBBY_BYTES:
                # Drop to fewer frames / smaller edge.
                step = max(1, len(frames) // 24)
                slim = frames[::step][:24]
                slim_d = durations[::step][:24]
                buf2 = io.BytesIO()
                slim[0].save(
                    buf2,
                    format='GIF',
                    save_all=True,
                    append_images=slim[1:] if len(slim) > 1 else [],
                    duration=slim_d,
                    loop=0,
                    disposal=2,
                    optimize=True,
                )
                raw = buf2.getvalue()
            if len(raw) > MAX_LOBBY_BYTES:
                return ''
            return base64.b64encode(raw).decode('ascii')
    except Exception:
        return ''
