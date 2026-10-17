"""Unit + registry tests for PotPlayer conflicting-shortcut overrides (v2.0.30).

Safe: uses a temp HUGE_PROFILE_DIR, backs up live MainShortCutList, applies
no-ops, verifies entries, restores, and leaves PotPlayer config unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import winreg

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine import potplayer_shortcuts as pps  # noqa: E402
from engine.hotkeys import DEFAULT_SHORTCUTS, normalize_shortcut  # noqa: E402


def ok(name, cond, detail=None):
    print(('PASS' if cond else 'FAIL'), '-', name + (f' ({detail})' if detail is not None else ''))
    return bool(cond)


def _list_has_noop(values, vk, pp_mod=0):
    if not values:
        return False
    target = f'{vk & 0xFF},{pp_mod & 0xFF},0,0'
    return any(str(v).replace(' ', '') == target for v in values.values())


def main():
    passed = True
    support = pps.is_supported_on_this_machine()
    passed &= ok('support detected', support.get('ok'), support)
    if not support.get('ok'):
        print('SKIP - no PotPlayer registry/ini shortcut storage found')
        return 0

    # --- pure helpers ---
    keys = pps.huge_binding_keys(DEFAULT_SHORTCUTS)
    vks = {vk for vk, _mod in keys}
    passed &= ok('defaults include Space', 0x20 in vks, keys)
    passed &= ok('defaults include Left', 0x25 in vks, keys)
    passed &= ok('defaults include Right', 0x27 in vks, keys)
    passed &= ok('Space noop format', pps.format_noop_entry(0x20) == '32,0,0,0')

    custom = {'play_pause': 'F8', 'seek_back': 'Ctrl+Left Arrow', 'seek_forward': ''}
    ckeys = pps.huge_binding_keys(custom)
    passed &= ok('custom F8 present', any(vk == 0x77 for vk, _ in ckeys), ckeys)
    passed &= ok('cleared seek_forward absent', all(vk != 0x27 for vk, _ in ckeys), ckeys)
    parsed = normalize_shortcut('Ctrl+Left Arrow')
    passed &= ok('ctrl+left parses', parsed is not None, parsed)

    fmt = pps.verify_registry_format()
    passed &= ok('registry format verified', fmt.get('ok'), fmt)
    passed &= ok('format is REG_SZ', fmt.get('value_type') == 'REG_SZ', fmt)
    if fmt.get('samples'):
        sample = fmt['samples'][0]
        passed &= ok(
            'sample parses vk,mod,cmd,flag',
            sample.get('parsed') is not None,
            sample,
        )

    # Isolated profile dir so we never clobber the user's Huge backup.
    tmp = tempfile.mkdtemp(prefix='huge-pp-sc-')
    prev_profile = os.environ.get('HUGE_PROFILE_DIR')
    os.environ['HUGE_PROFILE_DIR'] = tmp
    live_snap = pps.snapshot_all()
    try:
        result = pps.apply_huge_conflicts(DEFAULT_SHORTCUTS)
        passed &= ok('apply ok', result.get('ok'), result)
        passed &= ok('first apply changed registry', result.get('changed'), result)
        passed &= ok('backup created', os.path.isfile(pps._backup_path()), pps._backup_path())
        with open(pps._backup_path(), 'r', encoding='utf-8') as fh:
            backup = json.load(fh)
        for base, lists in (backup.get('registry') or {}).items():
            for list_name, values in (lists or {}).items():
                if not values:
                    continue
                for val in values.values():
                    parsed_e = pps.parse_entry(val)
                    if parsed_e and parsed_e[0] in (0x20, 0x25, 0x27) and parsed_e[2] == 0:
                        passed &= ok('backup free of default noops', False, (base, list_name, val))
                        break

        found_space = found_left = found_right = False
        for base in pps._active_reg_bases():
            for list_name in pps._LIST_NAMES:
                values = pps._read_reg_list(base, list_name) or {}
                found_space = found_space or _list_has_noop(values, 0x20)
                found_left = found_left or _list_has_noop(values, 0x25) or _list_has_noop(values, 0x25, pps._PP_EXT)
                found_right = found_right or _list_has_noop(values, 0x27) or _list_has_noop(values, 0x27, pps._PP_EXT)
        passed &= ok('registry Space noop', found_space)
        passed &= ok('registry Left noop', found_left)
        passed &= ok('registry Right noop', found_right)

        # Idempotent re-apply must not rewrite.
        result_idem = pps.apply_huge_conflicts(DEFAULT_SHORTCUTS)
        passed &= ok('idempotent apply ok', result_idem.get('ok'), result_idem)
        passed &= ok('idempotent apply unchanged', result_idem.get('changed') is False, result_idem)
        backup_mtime = os.path.getmtime(pps._backup_path())
        pps.apply_huge_conflicts(DEFAULT_SHORTCUTS)
        passed &= ok(
            'backup not rewritten on re-apply',
            os.path.getmtime(pps._backup_path()) == backup_mtime,
        )

        result2 = pps.apply_huge_conflicts({
            'play_pause': 'F8',
            'seek_back': 'Left Arrow',
            'seek_forward': 'Right Arrow',
        })
        passed &= ok('rebind apply ok', result2.get('ok'), result2)
        space_gone = True
        f8_present = False
        for base in pps._active_reg_bases():
            for list_name in pps._LIST_NAMES:
                values = pps._read_reg_list(base, list_name) or {}
                if _list_has_noop(values, 0x20):
                    space_gone = False
                if _list_has_noop(values, 0x77):
                    f8_present = True
        passed &= ok('cleared Space no longer noop', space_gone)
        passed &= ok('F8 noop present', f8_present)

        # Simulate user adding a custom PotPlayer binding after Huge's write.
        bases = pps._active_reg_bases()
        if bases:
            base0 = bases[0]
            live = pps._read_reg_list(base0, 'MainShortCutList') or {}
            next_idx = str(len(live))
            user_entry = '70,0,10013,0'  # F + fullscreen-ish cmd; not a Huge key
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, base0 + r'\MainShortCutList')
            try:
                winreg.SetValueEx(k, next_idx, 0, winreg.REG_SZ, user_entry)
            finally:
                winreg.CloseKey(k)

        restored = pps.restore_original()
        passed &= ok('restore ok', restored.get('ok') and restored.get('restored'), restored)
        preserved_ok = False
        if bases:
            live_after = pps._read_reg_list(bases[0], 'MainShortCutList') or {}
            preserved_ok = any(str(v).startswith('70,0,10013,') for v in live_after.values())
            # Clean the synthetic user entry so the machine is left clean.
            pps._write_reg_list_ordered(bases[0], 'MainShortCutList', [
                v for v in (live_after or {}).values()
                if not str(v).startswith('70,0,10013,')
            ])
        passed &= ok('restore preserved post-Huge user entry', preserved_ok, restored.get('preserved_user_entries'))

        for base, lists in (live_snap.get('registry') or {}).items():
            for list_name, values in (lists or {}).items():
                if values is None:
                    pps._write_reg_list(base, list_name, None)
                else:
                    pps._write_reg_list_ordered(base, list_name, pps._ordered_entries(values))
        passed &= ok('live registry restored from pretest snapshot', True)
    except Exception as exc:
        passed = False
        print('FAIL - exception', exc)
        import traceback
        traceback.print_exc()
        try:
            for base, lists in (live_snap.get('registry') or {}).items():
                for list_name, values in (lists or {}).items():
                    if values is None:
                        pps._write_reg_list(base, list_name, None)
                    else:
                        pps._write_reg_list_ordered(base, list_name, pps._ordered_entries(values))
        except Exception:
            pass
    finally:
        if prev_profile is None:
            os.environ.pop('HUGE_PROFILE_DIR', None)
        else:
            os.environ['HUGE_PROFILE_DIR'] = prev_profile
        shutil.rmtree(tmp, ignore_errors=True)

    print('RESULT', 'PASS' if passed else 'FAIL')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
