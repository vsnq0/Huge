"""Read-only PotPlayer keyframe notice. Does not write the registry."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'

from engine.session import (  # noqa: E402
    POT_EXACT_NOTICE,
    POT_KEYFRAME_NOTICE,
    POT_UNKNOWN_NOTICE,
    HugeEngine,
    notice_from_read_state,
    potplayer_settings_key,
    read_play_use_keyframe,
)

REG_DWORD = 4
fails = 0


def check(label, cond, detail=''):
    global fails
    print(('PASS' if cond else 'FAIL'), label, detail)
    if not cond:
        fails += 1
    return cond


def snapshot_value(key_path):
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return ('missing_key',)
    except OSError as exc:
        return ('error', type(exc).__name__)
    try:
        try:
            value, typ = winreg.QueryValueEx(key, 'PlayUseKeyFrame')
            return ('present', value, typ)
        except FileNotFoundError:
            return ('absent',)
    finally:
        winreg.CloseKey(key)


def test_mapping():
    mini = potplayer_settings_key(r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe')
    full = potplayer_settings_key(r'D:\apps\PotPlayer64.exe')
    other = potplayer_settings_key(r'C:\vlc.exe')
    check('mini64 registry path', mini == r'Software\DAUM\PotPlayerMini64\Settings', mini)
    check('potplayer64 registry path', full == r'Software\DAUM\PotPlayer64\Settings', full)
    check('unsupported exe has no registry path', other == '', other)
    seen = []

    def opener(key_path):
        seen.append(key_path)
        return (0, REG_DWORD)

    state = read_play_use_keyframe(r'C:\PotPlayerMini64.exe', opener=opener)
    check('mini64 read uses mini key', seen == [mini], str(seen))
    check('opened value 0', state == ('value', 0), str(state))
    seen.clear()
    state = read_play_use_keyframe(r'C:\PotPlayer64.exe', opener=opener)
    check('potplayer64 read uses its own key', seen == [full], str(seen))


def test_states():
    cases = (
        (('value', 0), 'exact', POT_EXACT_NOTICE),
        (('value', 1), 'keyframe', POT_KEYFRAME_NOTICE),
        (('absent',), 'keyframe', POT_KEYFRAME_NOTICE),
        (('unreadable',), 'unknown', POT_UNKNOWN_NOTICE),
        (('unsupported',), 'unknown', POT_UNKNOWN_NOTICE),
    )
    for state, status, text in cases:
        got_status, got_text = notice_from_read_state(state)
        check(f'notice {state}', got_status == status and got_text == text, f'{got_status} {got_text[:40]}')

    def missing(_key_path):
        raise FileNotFoundError('PlayUseKeyFrame')

    def denied(_key_path):
        raise PermissionError('access denied')

    def weird(_key_path):
        return ('yes', 1)

    def other_number(_key_path):
        return (2, REG_DWORD)

    check(
        'missing value is keyframe notice',
        notice_from_read_state(read_play_use_keyframe(r'C:\PotPlayerMini64.exe', opener=missing))[0] == 'keyframe',
    )
    check(
        'registry failure is unknown notice',
        notice_from_read_state(read_play_use_keyframe(r'C:\PotPlayerMini64.exe', opener=denied))[0] == 'unknown',
    )
    check(
        'non-dword is unknown notice',
        notice_from_read_state(read_play_use_keyframe(r'C:\PotPlayerMini64.exe', opener=weird))[0] == 'unknown',
    )
    check(
        'value 2 is unknown notice',
        notice_from_read_state(read_play_use_keyframe(r'C:\PotPlayer64.exe', opener=other_number))[0] == 'unknown',
    )
    check(
        'unsupported exe is unknown notice',
        notice_from_read_state(read_play_use_keyframe(r'C:\notes.txt'))[0] == 'unknown',
    )


def test_ui_state_does_not_block_lobby():
    eng = HugeEngine()
    folder = tempfile.mkdtemp(prefix='huge_notice_')
    exe = os.path.join(folder, 'PotPlayerMini64.exe')
    with open(exe, 'wb') as fh:
        fh.write(b'MZ')
    eng.username = 'Tester'
    eng.potplayer_path = exe
    eng.update_potplayer_path_ui()
    try:
        import engine.session as session
        original = session.read_play_use_keyframe
        for state, status, text in (
            (('value', 0), 'exact', POT_EXACT_NOTICE),
            (('value', 1), 'keyframe', POT_KEYFRAME_NOTICE),
            (('absent',), 'keyframe', POT_KEYFRAME_NOTICE),
            (('unreadable',), 'unknown', POT_UNKNOWN_NOTICE),
        ):
            session.read_play_use_keyframe = lambda _path, state=state: state
            ui = eng.ui_state()
            check(
                f'ui {status}',
                ui.get('pot_keyframe_status') == status and ui.get('pot_keyframe_notice') == text,
                ui.get('pot_keyframe_status'),
            )
            check(
                f'ui {status} keeps pot ready and lobby controls',
                ui.get('pot_ready') is True
                and ui.get('pot_badge') == 'PATH SAVED'
                and ui.get('can_create') is True
                and ui.get('can_join') is True
                and ui.get('last_error') == ''
                and 'play_pause' in (ui.get('keyboard_shortcuts') or {}),
                str({k: ui.get(k) for k in ('pot_ready', 'pot_badge', 'can_create', 'can_join', 'last_error')}),
            )
        session.read_play_use_keyframe = original
    finally:
        eng.close()


def test_real_registry_is_unchanged():
    import engine.session as session
    key_path = potplayer_settings_key(r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe')
    before = snapshot_value(key_path)
    state = read_play_use_keyframe(r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe')
    after = snapshot_value(key_path)
    status, text = notice_from_read_state(state)
    check('real registry unchanged', before == after, f'{before} -> {after}')
    check('real read maps to a notice', status in ('keyframe', 'exact', 'unknown') and bool(text), status)
    # ui_state uses the same read-only path.
    eng = HugeEngine()
    eng.potplayer_path = r'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe'
    try:
        ui = eng.ui_state()
        again = snapshot_value(key_path)
        check('ui_state does not write registry', again == before, str(again))
        check(
            'ui_state notice matches the read',
            ui.get('pot_keyframe_notice') == text and ui.get('pot_keyframe_status') == status,
            ui.get('pot_keyframe_status'),
        )
        check('notice is not the error banner field', ui.get('last_error') == '', ui.get('last_error'))
    finally:
        eng.close()
    check('reader still the module function', session.read_play_use_keyframe is read_play_use_keyframe)


def test_panel_source_binds_notice():
    app = open(os.path.join(ROOT, 'ui', 'src', 'App.tsx'), encoding='utf-8').read()
    panel = app.split('className="panel potplayer-panel"', 1)[1].split('</section>', 1)[0]
    check('panel renders pot_keyframe_notice', 'state.pot_keyframe_notice' in panel and 'pot-keyframe-notice' in panel)
    check('panel does not use the error banner', 'error-banner' not in panel and 'last_error' not in panel)


def main():
    test_mapping()
    test_states()
    test_ui_state_does_not_block_lobby()
    test_real_registry_is_unchanged()
    test_panel_source_binds_notice()
    print(f'FAILS {fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
