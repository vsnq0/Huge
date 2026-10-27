"""Chord recording, conflicts, auto-repeat, and real hook dispatch."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'
os.environ['HUGE_HOTKEY_ALLOW_INJECTED'] = '1'

from engine.hotkeys import (  # noqa: E402
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    MOD_WIN,
    PlaybackHotkeyManager,
    ShortcutStore,
    format_shortcut,
    normalize_shortcut,
    reserved_shortcut_reason,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def test_parse_requested_chords():
    passed = ok('Alt+Space', normalize_shortcut('Alt+Space') == (MOD_ALT, 0x20))
    passed &= ok('Ctrl+Space', normalize_shortcut('Ctrl+Space') == (MOD_CTRL, 0x20))
    passed &= ok('Shift+Space', normalize_shortcut('Shift+Space') == (MOD_SHIFT, 0x20))
    passed &= ok(
        'Ctrl+Alt+S',
        normalize_shortcut('Ctrl+Alt+S') == (MOD_CTRL | MOD_ALT, ord('S')),
    )
    passed &= ok(
        'Ctrl+Shift+Left Arrow',
        normalize_shortcut('Ctrl+Shift+Left Arrow') == (MOD_CTRL | MOD_SHIFT, 0x25),
    )
    passed &= ok('Alt+F', normalize_shortcut('Alt+F') == (MOD_ALT, ord('F')))
    passed &= ok(
        'modifier order',
        normalize_shortcut('Shift+Ctrl+Alt+Space') == normalize_shortcut('Ctrl+Alt+Shift+Space'),
    )
    passed &= ok(
        'display order',
        format_shortcut(MOD_SHIFT | MOD_CTRL | MOD_ALT, 0x20) == 'Ctrl+Alt+Shift+Space',
    )
    passed &= ok('Win+S', normalize_shortcut('Win+S') == (MOD_WIN, ord('S')))
    passed &= ok('semicolon', normalize_shortcut(';') == (0, 0xBA))
    passed &= ok('slash', normalize_shortcut('/') == (0, 0xBF))
    passed &= ok('Num3', normalize_shortcut('Num3') == (0, 0x63))
    passed &= ok('F13', normalize_shortcut('F13') == (0, 0x7C))
    passed &= ok('modifier only rejected', normalize_shortcut('Alt') is None)
    passed &= ok('Escape reserved', reserved_shortcut_reason(0, 0x1B))
    passed &= ok('Ctrl+Alt+Delete reserved', bool(reserved_shortcut_reason(MOD_CTRL | MOD_ALT, 0x2E)))
    passed &= ok('Win+L reserved', bool(reserved_shortcut_reason(MOD_WIN, ord('L'))))
    passed &= ok('Alt+Space not reserved', reserved_shortcut_reason(MOD_ALT, 0x20) is None)
    return passed


def test_duplicate_keeps_existing_and_legacy_conflict_loads():
    store = ShortcutStore()
    ok_set, err, _warnings = store.set('seek_forward', 'Space')
    passed = ok('duplicate refused', ok_set is False and 'Conflict' in err, err)
    passed &= ok('play/pause kept', store.get('play_pause') == 'Space')
    passed &= ok('seek forward kept', store.get('seek_forward') == 'Right Arrow')
    ok_clear, _, _ = store.clear('play_pause')
    passed &= ok('clear', ok_clear and store.get('play_pause') == '')
    ok_reset, _, _ = store.reset('play_pause')
    passed &= ok('reset', ok_reset and store.get('play_pause') == 'Space')
    legacy = ShortcutStore({
        'play_pause': 'Ctrl+Alt+S',
        'seek_back': 'Ctrl+Alt+S',
        'seek_forward': 'Right Arrow',
    })
    passed &= ok('saved conflict still loads', legacy.get('play_pause') == 'Ctrl+Alt+S')
    passed &= ok('saved conflict warning', any('Conflict' in w for w in legacy.warnings), legacy.warnings)
    bad, err2, _ = store.set('play_pause', 'Win+L')
    passed &= ok('Win+L refused', bad is False and 'Win+L' in err2, err2)
    passed &= ok('Win+L did not replace Space', store.get('play_pause') == 'Space')
    return passed


def test_repeat_release_and_scope():
    fired = []
    mgr = PlaybackHotkeyManager(
        on_action=lambda action, scope='huge', mono=None: fired.append((action, scope)),
    )
    mgr.load_dict({
        'play_pause': 'Alt+Space',
        'seek_back': 'Ctrl+Shift+Left Arrow',
        'seek_forward': 'Ctrl+Alt+S',
    })
    mgr.start()
    try:
        passed = ok(
            'Alt+Space consumes',
            mgr.decide_consume_for_test(0x20, mods=MOD_ALT, scope='potplayer') is True,
        )
        passed &= ok(
            'repeat eaten',
            mgr.decide_consume_for_test(0x20, mods=MOD_ALT, scope='potplayer') is True,
        )
        for _ in range(40):
            if fired:
                break
            time.sleep(0.01)
        passed &= ok('one play action', len(fired) == 1 and fired[0][0] == 'play_pause', fired)
        mgr.release_key_for_test(0x20)
        passed &= ok(
            'second press fires',
            mgr.decide_consume_for_test(0x20, mods=MOD_ALT, scope='huge', text_entry=False) is True,
        )
        for _ in range(40):
            if len(fired) >= 2:
                break
            time.sleep(0.01)
        passed &= ok('two presses', len(fired) == 2, fired)
        mgr.release_key_for_test(0x20)
        before = len(fired)
        passed &= ok(
            'typing blocks',
            mgr.decide_consume_for_test(0x20, mods=MOD_ALT, scope='huge', text_entry=True) is False,
        )
        passed &= ok('typing did not queue', len(fired) == before)
        passed &= ok(
            'other apps pass',
            mgr.decide_consume_for_test(ord('S'), mods=MOD_CTRL | MOD_ALT, scope=False) is False,
        )
        mgr.release_key_for_test(0x20)
        passed &= ok(
            'Ctrl+Shift+Left seeks back',
            mgr.decide_consume_for_test(0x25, mods=MOD_CTRL | MOD_SHIFT, scope='potplayer') is True,
        )
        mgr.release_key_for_test(0x25)
        passed &= ok(
            'Ctrl+Alt+S seeks forward',
            mgr.decide_consume_for_test(ord('S'), mods=MOD_CTRL | MOD_ALT, scope='huge') is True,
        )
        for _ in range(40):
            if len(fired) >= 4:
                break
            time.sleep(0.01)
        actions = [item[0] for item in fired]
        passed &= ok(
            'dispatch order',
            actions == ['play_pause', 'play_pause', 'seek_back', 'seek_forward'],
            actions,
        )
        passed &= ok('potplayer scope kept', fired[0][1] == 'potplayer', fired)
        return passed
    finally:
        mgr.stop()


def test_record_escape_and_chord():
    saved = []
    mgr = PlaybackHotkeyManager(on_action=lambda *a, **k: None)
    mgr.load_dict({
        'play_pause': 'Space',
        'seek_back': 'Left Arrow',
        'seek_forward': 'Right Arrow',
    })
    mgr.start()
    try:
        mgr.begin_record('play_pause', callback=lambda result: saved.append(result))
        passed = ok('recording', mgr.is_recording() is True)
        mgr._mods_down = MOD_ALT
        eaten = mgr.decide_consume_for_test(0x20, mods=MOD_ALT, scope='huge')
        for _ in range(40):
            if saved:
                break
            time.sleep(0.01)
        passed &= ok('recorded Alt+Space', eaten and saved and saved[-1].get('ok') and saved[-1].get('shortcut') == 'Alt+Space', saved)
        passed &= ok('stored', mgr.shortcuts().get('play_pause') == 'Alt+Space', mgr.shortcuts())
        saved.clear()
        mgr.begin_record('seek_back', callback=lambda result: saved.append(result))
        cancelled = mgr.decide_consume_for_test(0x1B, mods=0, scope='huge')
        for _ in range(40):
            if saved:
                break
            time.sleep(0.01)
        passed &= ok('escape cancels', cancelled and saved and saved[-1].get('cancelled') is True, saved)
        passed &= ok('seek back unchanged', mgr.shortcuts().get('seek_back') == 'Left Arrow')
        passed &= ok('not recording', mgr.is_recording() is False)
        return passed
    finally:
        mgr.stop()


def test_live_hook_dispatch():
    if os.name != 'nt':
        print('PASS - live hook skipped')
        return True
    import ctypes
    from ctypes import wintypes

    fired = []
    mgr = PlaybackHotkeyManager(
        on_action=lambda action, scope='huge', mono=None: fired.append(action),
    )
    mgr._allow_injected = True
    mgr.load_dict({
        'play_pause': 'Alt+Space',
        'seek_back': 'Ctrl+Shift+Left Arrow',
        'seek_forward': 'Ctrl+Alt+S',
    })
    mgr._scope_from_cache = lambda: 'huge'  # type: ignore
    mgr._is_text_entry_active = lambda: False
    user32 = ctypes.windll.user32
    KEYUP = 0x0002

    def tap(vk, modifiers):
        for mod in modifiers:
            user32.keybd_event(mod, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk, 0, KEYUP, 0)
        for mod in reversed(modifiers):
            user32.keybd_event(mod, 0, KEYUP, 0)
        time.sleep(0.05)

    mgr.start()
    try:
        deadline = time.time() + 1.5
        while mgr._hook is None and time.time() < deadline:
            time.sleep(0.02)
        passed = ok('hook installed', bool(mgr._hook))
        tap(0x20, (0x12,))  # Alt, Space
        tap(0x25, (0x11, 0x10))  # Ctrl, Shift, Left
        tap(ord('S'), (0x11, 0x12))  # Ctrl, Alt, S
        for _ in range(50):
            if len(fired) >= 3:
                break
            time.sleep(0.02)
        passed &= ok('live hook dispatched chords', fired == ['play_pause', 'seek_back', 'seek_forward'], fired)
        return passed
    finally:
        mgr.stop()


def main():
    tests = [
        test_parse_requested_chords,
        test_duplicate_keeps_existing_and_legacy_conflict_loads,
        test_repeat_release_and_scope,
        test_record_escape_and_chord,
        test_live_hook_dispatch,
    ]
    results = []
    for fn in tests:
        print(f'\n=== {fn.__name__} ===')
        try:
            results.append(bool(fn()))
        except Exception as exc:
            print('FAIL - exception', exc)
            results.append(False)
    print(f'\n{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
