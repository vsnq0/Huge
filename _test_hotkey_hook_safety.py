"""Safety tests for WH_KEYBOARD_LL: unmatched keys must never be consumed.

These tests exercise the decision path without requiring PotPlayer. A separate
script (_test_hotkey_live_typing.py) performs a real Windows typing check.
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'

from engine.hotkeys import (  # noqa: E402
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    PlaybackHotkeyManager,
    VK_OEM_COMMA,
    VK_OEM_PERIOD,
    default_shortcuts_dict,
)


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def _mgr(fired=None):
    fired = fired if fired is not None else []
    mgr = PlaybackHotkeyManager(
        on_action=lambda action, scope='huge', mono=None: fired.append((action, scope)),
        get_huge_hwnds=lambda: [1],
        is_text_entry_active=lambda: False,
        find_pot_hwnd=lambda: 0,
    )
    mgr.load_dict(default_shortcuts_dict())
    return mgr, fired


def test_unmatched_letters_pass_through():
    mgr, fired = _mgr()
    passed = True
    for vk, name in ((ord('A'), 'A'), (ord('Z'), 'Z'), (ord('1'), '1'), (0x09, 'Tab'), (0x0D, 'Enter')):
        eaten = mgr.decide_consume_for_test(vk, mods=0, scope='huge', text_entry=False)
        passed &= ok(f'unmatched {name} not consumed', eaten is False)
    passed &= ok('no actions queued from letters', fired == [])
    return passed


def test_matched_shortcut_once():
    mgr, fired = _mgr()
    mgr.start()  # starts worker so queued actions run
    try:
        eaten = mgr.decide_consume_for_test(0x20, mods=0, scope='potplayer')  # Space
        passed = ok('Space consumed when scoped', eaten is True)
        # Debounced repeat also consumed but does not double-fire
        eaten2 = mgr.decide_consume_for_test(0x20, mods=0, scope='potplayer')
        passed &= ok('Space repeat consumed', eaten2 is True)
        for _ in range(50):
            if fired:
                break
            time.sleep(0.01)
        passed &= ok('Space fired once', len(fired) == 1 and fired[0][0] == 'play_pause', fired)
        return passed
    finally:
        mgr.stop()


def test_modifiers_never_consumed_via_decide():
    """Modifier VKs are handled before decide; decide should also not bind them."""
    mgr, _fired = _mgr()
    passed = True
    for vk in (0x10, 0x11, 0x12, 0x5B, 0xA0, 0xA2, 0xA4):
        # Even if forced through decide_consume, modifiers are not in the binding table.
        eaten = mgr.decide_consume_for_test(vk, mods=0, scope='huge')
        passed &= ok(f'modifier vk={vk:#x} not consumed', eaten is False)
    return passed


def test_text_entry_blocks_space_in_huge():
    mgr, fired = _mgr()
    eaten = mgr.decide_consume_for_test(0x20, mods=0, scope='huge', text_entry=True)
    passed = ok('Space not stolen while typing in Huge', eaten is False)
    passed &= ok('no fire while typing', fired == [])
    return passed


def test_out_of_scope_not_consumed():
    mgr, fired = _mgr()
    eaten = mgr.decide_consume_for_test(0x20, mods=0, scope=False)
    passed = ok('Space not consumed outside Huge/PotPlayer', eaten is False)
    passed &= ok('no fire out of scope', fired == [])
    return passed


def test_seek_shortcuts():
    mgr, fired = _mgr()
    mgr.start()
    try:
        # Defaults are Left / Right arrows
        passed = ok(
            'Left consumes',
            mgr.decide_consume_for_test(0x25, mods=0, scope='potplayer') is True,
        )
        time.sleep(0.15)
        passed &= ok(
            'Right consumes',
            mgr.decide_consume_for_test(0x27, mods=0, scope='huge') is True,
        )
        for _ in range(50):
            if len(fired) >= 2:
                break
            time.sleep(0.01)
        actions = [a for a, _s in fired]
        passed &= ok('seek actions', actions == ['seek_back', 'seek_forward'], actions)
        # Unrelated keys / cleared angle-bracket chords must NOT be consumed by defaults
        passed &= ok(
            'Shift+comma passes by default',
            mgr.decide_consume_for_test(VK_OEM_COMMA, mods=MOD_SHIFT, scope='potplayer') is False,
        )
        passed &= ok(
            'bare comma passes',
            mgr.decide_consume_for_test(VK_OEM_COMMA, mods=0, scope='potplayer') is False,
        )
        passed &= ok('letter V passes', mgr.decide_consume_for_test(ord('V'), scope='potplayer') is False)
        passed &= ok('F key passes', mgr.decide_consume_for_test(ord('F'), scope='potplayer') is False)
        # Clearing Left must stop interception
        mgr.clear_shortcut('seek_back')
        time.sleep(0.05)
        passed &= ok(
            'cleared Left no longer consumed',
            mgr.decide_consume_for_test(0x25, mods=0, scope='potplayer') is False,
        )
        return passed
    finally:
        mgr.stop()


def test_stop_releases_hook_flag():
    mgr, _ = _mgr()
    mgr.start()
    time.sleep(0.2)
    running_before = mgr._running and mgr._thread is not None
    mgr.stop()
    time.sleep(0.2)
    passed = ok('was running', running_before)
    passed &= ok('stopped flag', mgr._running is False)
    passed &= ok('hook cleared', mgr._hook is None)
    passed &= ok('thread joined', mgr._thread is None or not mgr._thread.is_alive())
    return passed


def test_hook_callback_does_not_call_enum_providers():
    """Refresh providers must not be invoked from decide_consume."""
    calls = {'huge': 0, 'pot': 0}

    def get_huge():
        calls['huge'] += 1
        return [1]

    def find_pot():
        calls['pot'] += 1
        return 0

    fired = []
    mgr = PlaybackHotkeyManager(
        on_action=lambda *a, **k: fired.append(1),
        get_huge_hwnds=get_huge,
        find_pot_hwnd=find_pot,
        is_text_entry_active=lambda: False,
    )
    mgr.load_dict(default_shortcuts_dict())
    # Seed cache without going through providers in decide path.
    mgr._cached_huge_roots = frozenset({1})
    mgr._cached_pot_root = 0
    mgr._scope_from_cache = lambda: 'huge'  # type: ignore
    mgr.decide_consume_for_test(ord('Q'), scope='huge')
    mgr.decide_consume_for_test(0x20, scope='huge')
    passed = ok('get_huge_hwnds not called from decide', calls['huge'] == 0, calls)
    passed &= ok('find_pot_hwnd not called from decide', calls['pot'] == 0, calls)
    return passed


def test_ctrl_alt_shift_combos_parse_and_match():
    mgr, fired = _mgr()
    mgr.set_shortcut('play_pause', 'Ctrl+Alt+Shift+P')
    mgr.start()
    try:
        mods = MOD_CTRL | MOD_ALT | MOD_SHIFT
        eaten = mgr.decide_consume_for_test(ord('P'), mods=mods, scope='huge')
        passed = ok('combo consumed', eaten is True)
        # Letter without mods must pass
        eaten2 = mgr.decide_consume_for_test(ord('P'), mods=0, scope='huge')
        passed &= ok('bare P not consumed', eaten2 is False)
        for _ in range(50):
            if fired:
                break
            time.sleep(0.01)
        passed &= ok('combo fired', bool(fired) and fired[0][0] == 'play_pause', fired)
        return passed
    finally:
        mgr.stop()


def main():
    tests = [
        test_unmatched_letters_pass_through,
        test_matched_shortcut_once,
        test_modifiers_never_consumed_via_decide,
        test_text_entry_blocks_space_in_huge,
        test_out_of_scope_not_consumed,
        test_seek_shortcuts,
        test_stop_releases_hook_flag,
        test_hook_callback_does_not_call_enum_providers,
        test_ctrl_alt_shift_combos_parse_and_match,
    ]
    results = []
    for fn in tests:
        print(f'\n=== {fn.__name__} ===')
        try:
            results.append(bool(fn()))
        except Exception as exc:
            print('FAIL - exception', exc)
            results.append(False)
    print('\n=== SUMMARY ===')
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
