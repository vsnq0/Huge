"""Regression: arrow recording labels, Space→F8 rebind, clear vs reset, persist."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'

from engine.hotkeys import (  # noqa: E402
    PlaybackHotkeyManager,
    ShortcutStore,
    format_shortcut,
    normalize_shortcut,
    shortcut_label,
)
from engine.fake_pot import install_fake_pot  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine  # noqa: E402


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def test_arrow_labels_roundtrip():
    passed = ok('norm Left', normalize_shortcut('Left') == (0, 0x25))
    passed &= ok('norm Left Arrow', normalize_shortcut('Left Arrow') == (0, 0x25))
    passed &= ok('norm ArrowLeft', normalize_shortcut('ArrowLeft') == (0, 0x25))
    passed &= ok('norm Right Arrow', normalize_shortcut('Right Arrow') == (0, 0x27))
    passed &= ok('format Left Arrow', format_shortcut(0, 0x25) == 'Left Arrow')
    passed &= ok('format Right Arrow', format_shortcut(0, 0x27) == 'Right Arrow')
    passed &= ok('label Left becomes Left Arrow', shortcut_label('Left') == 'Left Arrow')
    passed &= ok('defaults display', ShortcutStore().get('seek_back') == 'Left Arrow')
    passed &= ok('no OEM_PLUS', format_shortcut(0, 0xBB) == 'Plus')
    return passed


def test_space_reassign_to_f8_immediate_and_persist():
    tmp = tempfile.mkdtemp(prefix='huge-rebind-')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    eng = HugeEngine()
    fired = []

    # Attach to real manager decision path
    mgr = eng._hotkeys
    if mgr is None:
        mgr = PlaybackHotkeyManager(on_action=lambda a, **k: fired.append(a))
        eng._hotkeys = mgr
        mgr.load_dict(eng._keyboard_shortcuts)

    original_on = mgr._on_action

    def _track(action, scope='huge', mono=None):
        fired.append(action)
        if original_on:
            original_on(action, scope=scope, mono=mono)

    mgr._on_action = _track

    passed = ok('starts on Space', eng._keyboard_shortcuts.get('play_pause') == 'Space')
    r = eng.set_keyboard_shortcut('play_pause', 'F8')
    passed &= ok('set F8 ok', r.get('ok') is True, r)
    passed &= ok('engine map F8', eng._keyboard_shortcuts.get('play_pause') == 'F8')
    passed &= ok(
        'hook snapshot has F8 not Space',
        mgr._bind_snapshot.get((0, 0x77)) == 'play_pause'
        and (0, 0x20) not in mgr._bind_snapshot,
        mgr._bind_snapshot,
    )

    mgr.start()
    try:
        fired.clear()
        eaten_space = mgr.decide_consume_for_test(0x20, mods=0, scope='potplayer')
        passed &= ok('Space no longer consumed', eaten_space is False)
        passed &= ok('Space did not fire', fired == [])
        mgr._last_fire.clear()
        eaten_f8 = mgr.decide_consume_for_test(0x77, mods=0, scope='potplayer')
        passed &= ok('F8 consumed', eaten_f8 is True)
        for _ in range(50):
            if 'play_pause' in fired:
                break
            time.sleep(0.01)
        passed &= ok('F8 fired play_pause', 'play_pause' in fired, fired)
    finally:
        mgr.stop()

    eng._save_profile()
    eng2 = HugeEngine()
    passed &= ok(
        'persist F8 after restart',
        eng2._keyboard_shortcuts.get('play_pause') == 'F8',
        eng2._keyboard_shortcuts,
    )
    if eng2._hotkeys:
        passed &= ok(
            'restart hook has no Space',
            (0, 0x20) not in eng2._hotkeys._bind_snapshot,
            eng2._hotkeys._bind_snapshot,
        )
        passed &= ok(
            'restart hook has F8',
            eng2._hotkeys._bind_snapshot.get((0, 0x77)) == 'play_pause',
            eng2._hotkeys._bind_snapshot,
        )

    try:
        eng.close()
        eng2.close()
    except Exception:
        pass
    return passed


def test_clear_does_not_restore_default():
    tmp = tempfile.mkdtemp(prefix='huge-clear-')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    eng = HugeEngine()
    eng.clear_keyboard_shortcut('play_pause')
    passed = ok('cleared empty', eng._keyboard_shortcuts.get('play_pause') == '')
    if eng._hotkeys:
        passed &= ok(
            'hook dropped Space',
            (0, 0x20) not in eng._hotkeys._bind_snapshot,
            eng._hotkeys._bind_snapshot,
        )
        eaten = eng._hotkeys.decide_consume_for_test(0x20, scope='potplayer')
        passed &= ok('Space not consumed after clear', eaten is False)
    eng._save_profile()
    with open(eng._profile_file(), 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    passed &= ok(
        'profile stores empty play_pause',
        data.get('keyboard_shortcuts', {}).get('play_pause') == '',
        data.get('keyboard_shortcuts'),
    )
    eng2 = HugeEngine()
    passed &= ok(
        'restart keeps cleared',
        eng2._keyboard_shortcuts.get('play_pause') == '',
        eng2._keyboard_shortcuts,
    )
    eng2.reset_keyboard_shortcut('play_pause')
    passed &= ok(
        'reset restores Space intentionally',
        eng2._keyboard_shortcuts.get('play_pause') == 'Space',
    )
    try:
        eng.close()
        eng2.close()
    except Exception:
        pass
    return passed


def test_invalid_does_not_revive_space():
    store = ShortcutStore({
        'play_pause': 'F8',
        'seek_back': 'Left Arrow',
        'seek_forward': 'Right Arrow',
    })
    ok_set, err, warnings = store.set('play_pause', 'NOT_A_KEY!!!')
    passed = ok('invalid rejected', ok_set is False, err)
    passed &= ok('still F8', store.get('play_pause') == 'F8')
    return passed


def main():
    tests = [
        test_arrow_labels_roundtrip,
        test_space_reassign_to_f8_immediate_and_persist,
        test_clear_does_not_restore_default,
        test_invalid_does_not_revive_space,
    ]
    failed = 0
    for fn in tests:
        print(f'\n=== {fn.__name__} ===')
        if not fn():
            failed += 1
    print(f'\n=== SUMMARY ===\n{len(tests) - failed}/{len(tests)} passed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
