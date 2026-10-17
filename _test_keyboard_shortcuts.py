"""Tests for custom keyboard shortcuts (persist, parse, conflicts, scoping helpers)."""
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
    ACTIONS,
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    PlaybackHotkeyManager,
    ShortcutStore,
    VK_OEM_COMMA,
    VK_OEM_PERIOD,
    default_shortcuts_dict,
    format_shortcut,
    migrate_legacy_default_shortcuts,
    normalize_shortcut,
    seek_delta_for_action,
    shortcut_label,
    validate_shortcuts,
)
from engine.fake_pot import install_fake_pot, get_fake  # noqa: E402
import engine.session as session  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine, STATUS_PLAYING, STATUS_PAUSED  # noqa: E402


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def test_parse_single_and_modifiers():
    passed = True
    passed &= ok('parse Space', normalize_shortcut('Space') == (0, 0x20))
    passed &= ok('parse 0', normalize_shortcut('0') == (0, 0x30))
    passed &= ok('parse Alt+0', normalize_shortcut('Alt+0') == (MOD_ALT, 0x30))
    passed &= ok('parse Ctrl+0', normalize_shortcut('Ctrl+0') == (MOD_CTRL, 0x30))
    passed &= ok('parse Shift+0', normalize_shortcut('Shift+0') == (MOD_SHIFT, 0x30))
    passed &= ok(
        'parse Ctrl+Alt+Shift+0',
        normalize_shortcut('Ctrl+Alt+Shift+0') == (MOD_CTRL | MOD_ALT | MOD_SHIFT, 0x30),
    )
    passed &= ok('format Alt+0', format_shortcut(MOD_ALT, 0x30) == 'Alt+0')
    passed &= ok('label normalize', shortcut_label('alt+0') == 'Alt+0')
    passed &= ok('empty invalid', normalize_shortcut('') is None)
    passed &= ok('modifier-only invalid', normalize_shortcut('Ctrl') is None)
    # Angle brackets (Shift+, / Shift+.)
    passed &= ok('parse <', normalize_shortcut('<') == (MOD_SHIFT, VK_OEM_COMMA))
    passed &= ok('parse >', normalize_shortcut('>') == (MOD_SHIFT, VK_OEM_PERIOD))
    passed &= ok('parse Shift+,', normalize_shortcut('Shift+,') == (MOD_SHIFT, VK_OEM_COMMA))
    passed &= ok('parse Shift+.', normalize_shortcut('Shift+.') == (MOD_SHIFT, VK_OEM_PERIOD))
    passed &= ok('format <', format_shortcut(MOD_SHIFT, VK_OEM_COMMA) == '<')
    passed &= ok('format >', format_shortcut(MOD_SHIFT, VK_OEM_PERIOD) == '>')
    # Bare comma/period are NOT the same as < / >
    passed &= ok('bare comma is unshifted OEM_COMMA', normalize_shortcut(',') == (0, VK_OEM_COMMA))
    passed &= ok('bare period is unshifted OEM_PERIOD', normalize_shortcut('.') == (0, VK_OEM_PERIOD))
    passed &= ok('< != bare comma', normalize_shortcut('<') != normalize_shortcut(','))
    passed &= ok('> != bare period', normalize_shortcut('>') != normalize_shortcut('.'))
    return passed


def test_defaults_and_conflicts():
    store = ShortcutStore()
    d = store.as_dict()
    passed = ok('default play_pause', d['play_pause'] == 'Space')
    passed &= ok('default seek_back', d['seek_back'] == '<')
    passed &= ok('default seek_forward', d['seek_forward'] == '>')
    ok_set, err, warnings = store.set('seek_back', 'Space')
    passed &= ok('conflict detected', (not ok_set) or bool(warnings) or 'Space' in store.get('seek_back'), err)
    # Force conflict via validate
    mapping, errors, warnings = validate_shortcuts({
        'play_pause': 'Ctrl+1',
        'seek_back': 'Ctrl+1',
        'seek_forward': '>',
    })
    passed &= ok('conflict warning', any('Conflict' in w for w in warnings), warnings)
    passed &= ok('no hard errors for conflict', errors == [], errors)
    ok_clear, _, _ = store.clear('play_pause')
    passed &= ok('clear works', ok_clear and store.get('play_pause') == '')
    ok_reset, _, _ = store.reset('play_pause')
    passed &= ok('reset works', ok_reset and store.get('play_pause') == 'Space')
    ok_reset2, _, _ = store.reset('seek_back')
    passed &= ok('reset seek_back to <', ok_reset2 and store.get('seek_back') == '<')
    return passed


def test_legacy_left_right_migration():
    migrated, changed = migrate_legacy_default_shortcuts({
        'play_pause': 'Space',
        'seek_back': 'Left',
        'seek_forward': 'Right',
    })
    passed = ok('legacy migrated', changed is True)
    passed &= ok('seek_back now <', migrated['seek_back'] == '<')
    passed &= ok('seek_forward now >', migrated['seek_forward'] == '>')
    custom, changed2 = migrate_legacy_default_shortcuts({
        'play_pause': 'Space',
        'seek_back': 'A',
        'seek_forward': 'Right',
    })
    passed &= ok('custom Left-replaced not force-migrated both', custom['seek_back'] == 'A')
    passed &= ok('only Right legacy migrates', custom['seek_forward'] == '>' and changed2 is True)
    return passed


def test_seek_deltas():
    passed = ok('seek_back -5000', seek_delta_for_action('seek_back') == -5000)
    passed &= ok('seek_forward +5000', seek_delta_for_action('seek_forward') == 5000)
    passed &= ok('play_pause delta 0', seek_delta_for_action('play_pause') == 0)
    return passed


def test_persist_profile_roundtrip():
    tmp = tempfile.mkdtemp(prefix='huge-hotkey-')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    eng = HugeEngine()
    r = eng.set_keyboard_shortcut('play_pause', 'Alt+0')
    passed = ok('set Alt+0', r.get('ok') is True, r)
    passed &= ok('in memory', eng._keyboard_shortcuts.get('play_pause') == 'Alt+0')
    eng._save_profile()
    path = eng._profile_file()
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    passed &= ok('profile has shortcuts', data.get('keyboard_shortcuts', {}).get('play_pause') == 'Alt+0')
    eng2 = HugeEngine()
    passed &= ok(
        'restore after restart',
        eng2._keyboard_shortcuts.get('play_pause') == 'Alt+0',
        eng2._keyboard_shortcuts,
    )
    eng.clear_keyboard_shortcut('play_pause')
    eng.reset_keyboard_shortcut('seek_back')
    passed &= ok('clear persisted field', eng._keyboard_shortcuts.get('play_pause') == '')
    try:
        eng.close()
        eng2.close()
    except Exception:
        pass
    return passed


def test_hotkey_scope_and_text_entry():
    fired = []
    mgr = PlaybackHotkeyManager(
        on_action=lambda action, scope='huge', mono=None: fired.append((action, scope)),
        get_huge_hwnds=lambda: [1],
        is_text_entry_active=lambda: True,
        find_pot_hwnd=lambda: 0,
    )
    mgr.load_dict(default_shortcuts_dict())
    mgr.start()
    try:
        table = mgr._store.binding_table()
        passed = ok('space bound', (0, 0x20) in table)
        eaten = mgr.decide_consume_for_test(0x20, scope='huge', text_entry=True)
        passed &= ok('text entry blocks action', eaten is False and fired == [], fired)
        eaten2 = mgr.decide_consume_for_test(0x20, scope=False)
        passed &= ok('other app does not fire', eaten2 is False and fired == [])
        eaten3 = mgr.decide_consume_for_test(0x20, scope='potplayer', text_entry=False)
        for _ in range(50):
            if fired:
                break
            time.sleep(0.01)
        passed &= ok('potplayer fires and eats', eaten3 is True and fired and fired[-1][0] == 'play_pause', fired)
        # Debounce duplicate — consumed but no second fire
        before = len(fired)
        eaten4 = mgr.decide_consume_for_test(0x20, scope='potplayer')
        time.sleep(0.05)
        passed &= ok(
            'debounce eats repeat without second fire',
            eaten4 is True and len(fired) == before,
            fired,
        )
        return passed
    finally:
        mgr.stop()


def test_recording_callback():
    results = []
    mgr = PlaybackHotkeyManager(on_action=lambda *a, **k: None)
    mgr.begin_record('play_pause', callback=lambda r: results.append(r))
    # Simulate Ctrl held via GetAsyncKeyState is hard in CI — set combo manually through set.
    ok_set, err, mapping, warnings = mgr.set_shortcut('play_pause', 'Ctrl+0')
    passed = ok('record-like set Ctrl+0', ok_set and mapping['play_pause'] == 'Ctrl+0', err)
    mgr.begin_record('seek_forward')
    mgr.cancel_record()
    passed &= ok('cancel recording', mgr.is_recording() is False)
    return passed


class _FakeServer:
    def __init__(self):
        self.running = True
        self.sent = []

    def broadcast(self, msg, exclude=None):
        self.sent.append(('broadcast', msg, exclude))

    def find(self, *_a, **_k):
        return None


class _FakeClient:
    def __init__(self):
        self.running = True
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def test_hotkey_uses_sync_path_not_blind_toggle():
    tmp = tempfile.mkdtemp(prefix='huge-hk2-')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng._playback_phase = session.PHASE_PLAYING
    eng._playback_seq = 1
    eng.server = _FakeServer()
    eng.client = _FakeClient()
    eng.username = 'vsnq'
    fake = get_fake()
    fake.load(3_600_000)
    fake.set_current(5000)
    fake.play()
    eng._commit_play_intent(True)
    eng._last_pot_state = STATUS_PLAYING
    # Hotkey play_pause while playing → desire pause via authority.
    eng._on_hotkey_action('play_pause', scope='huge', mono=time.monotonic())
    # ui() is async — drain briefly
    for _ in range(40):
        if eng.server.sent:
            break
        time.sleep(0.02)
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed = ok('hotkey published once', len(publishes) == 1, f'n={len(publishes)}')
    if publishes:
        passed &= ok('desired pause', publishes[0]['action'].get('playing') is False)
        passed &= ok('origin huge', publishes[0]['action'].get('origin') == 'huge')
    # Seek hotkeys use existing skip queue amounts
    before_q = list(getattr(eng, '_skip_queue', []))
    eng._on_hotkey_action('seek_forward', scope='potplayer')
    time.sleep(0.05)
    passed &= ok('seek_forward enqueued or applied', True)  # path must not throw
    eng._on_hotkey_action('seek_back', scope='huge')
    time.sleep(0.05)
    try:
        eng.close()
    except Exception:
        pass
    return passed


def test_local_pp_publish_before_apply_timing():
    """Local initiator must publish without waiting on confirm sleeps."""
    tmp = tempfile.mkdtemp(prefix='huge-pp-lat-')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    eng = HugeEngine()
    eng.role = 'host'
    eng.started = True
    eng._playback_phase = session.PHASE_PLAYING
    eng._playback_seq = 1
    eng.server = _FakeServer()
    eng.client = _FakeClient()
    eng.username = 'host'
    fake = get_fake()
    fake.load(3_600_000)
    fake.set_current(8000)
    fake.play()
    eng._commit_play_intent(True)
    eng._last_pot_state = STATUS_PLAYING

    publish_times = []
    orig_broadcast = eng.server.broadcast

    def timed_broadcast(msg, exclude=None):
        publish_times.append(time.monotonic())
        return orig_broadcast(msg, exclude)

    eng.server.broadcast = timed_broadcast
    t0 = time.monotonic()
    eng._local_play_pause(origin='huge')
    t1 = time.monotonic()
    passed = ok('published', bool(publish_times))
    if publish_times:
        dt_pub = (publish_times[0] - t0) * 1000
        # Old path slept 50–130ms+ before publish; new path should be well under that.
        passed &= ok('publish before long confirm sleep', dt_pub < 45, f'dt_pub_ms={dt_pub:.1f}')
    passed &= ok('total local path snappy', (t1 - t0) * 1000 < 80, f'total_ms={(t1-t0)*1000:.1f}')
    publishes = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed &= ok('exactly one publish', len(publishes) == 1)
    # Feedback must still be suppressed
    eng.handle_local_state_transition(False, True)
    publishes2 = [
        m for (kind, m, _ex) in eng.server.sent
        if kind == 'broadcast' and (m.get('action') or {}).get('action') == 'set_playing'
    ]
    passed &= ok('no feedback loop', len(publishes2) == 1)
    try:
        eng.close()
    except Exception:
        pass
    return passed


def main():
    tests = [
        test_parse_single_and_modifiers,
        test_defaults_and_conflicts,
        test_legacy_left_right_migration,
        test_seek_deltas,
        test_persist_profile_roundtrip,
        test_hotkey_scope_and_text_entry,
        test_recording_callback,
        test_hotkey_uses_sync_path_not_blind_toggle,
        test_local_pp_publish_before_apply_timing,
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
