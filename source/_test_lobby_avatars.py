"""Lobby avatars: wire payload must be valid image data, not truncated trash."""
from __future__ import annotations

import base64
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'

from engine.fake_pot import install_fake_pot  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine  # noqa: E402


def _minimal_png(w=8, h=8, rgba=(40, 40, 40, 255)):
    """Tiny valid PNG (solid color)."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b''.join(b'\x00' + bytes(rgba) * w for _ in range(h))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )


def test_sanitize_rejects_truncated():
    eng = HugeEngine()
    good = base64.b64encode(_minimal_png()).decode('ascii')
    assert eng._sanitize_remote_avatar(good)
    assert eng._sanitize_remote_avatar('not-base64!!!') == ''
    assert eng._sanitize_remote_avatar('') == ''
    # Corrupt payload: valid base64 but not an image.
    junk = base64.b64encode(b'this-is-not-an-image-payload-XXXX').decode('ascii')
    assert eng._sanitize_remote_avatar(junk) == ''
    # Over-budget rejected (do not mid-truncate).
    oversized = good + ('A' * (eng.LOBBY_AVATAR_MAX_CHARS + 10))
    assert eng._sanitize_remote_avatar(oversized) == ''
    print('OK sanitize')


def test_wire_avatar_roundtrip_lobby():
    import _test_multi_lobby as ml
    import time

    work = os.path.join(ROOT, 'test-results', 'avatars')
    os.makedirs(work, exist_ok=True)
    png = _minimal_png(32, 32, (200, 80, 40, 255))
    b64 = base64.b64encode(png).decode('ascii')
    host, clients = ml.fresh_peers(2, 49010, work)
    try:
        # Set avatars before join via profile files is hard; inject after host create
        # by calling set_avatar then re-host/join with connect carrying wire avatar.
        # Simpler: host create, joiners join, then set_avatar_b64 + avatar message.
        hr = host.call({'cmd': 'host', 'name': 'Host', 'lobby': 'Avatars'})
        assert hr.get('ok'), hr
        time.sleep(0.3)
        for c in clients:
            # Put avatar into peer engine before join by writing profile — peer already started.
            # Use set_avatar_b64 then leave/rejoin isn't available; send avatar after join.
            jr = c.call({'cmd': 'join', 'name': c.name, 'address': '127.0.0.1:24816'}, timeout=20)
            assert jr.get('ok'), jr
        host.call({'cmd': 'set_avatar_b64', 'avatar_b64': b64})
        # Force host users host avatar refresh
        for c in clients:
            c.call({'cmd': 'set_avatar_b64', 'avatar_b64': b64})
            # Peer needs to send avatar type — add via inject if set_avatar doesn't send when not client yet
        # Re-send: joiner already client — set_avatar_b64 only sets local; trigger wire send
        # by calling a small RPC that sends avatar if we add it — update set_avatar handler.
        time.sleep(0.5)
        # Directly ask host state for member avatars after manual avatar messages
        # Use Python on host peer through a new cmd — for now verify sanitize + wire_len on set.
        for p in [host] + clients:
            r = p.call({'cmd': 'set_avatar_b64', 'avatar_b64': b64})
            assert r.get('wire_ok'), r
            assert r.get('wire_len', 0) > 0
            print(p.name, 'wire_len', r.get('wire_len'))
        time.sleep(0.6)
        for p in [host] + clients:
            av = p.call({'cmd': 'state'})['state'].get('avatars') or {}
            print(p.name, 'sees', av)
            # Every peer should see valid avatars for all members once synced.
            oks = [v.get('ok') for v in av.values()]
            assert oks and all(oks), (p.name, av)
        print('OK lobby avatar sync')
    finally:
        ml.stop_peers(host, clients)


def main():
    test_sanitize_rejects_truncated()
    test_wire_avatar_roundtrip_lobby()
    print('PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
