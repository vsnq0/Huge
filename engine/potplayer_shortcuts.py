"""Disable PotPlayer native bindings that conflict with Huge playback shortcuts.

Verified format (PotPlayer Mini64, Windows registry — not binary blobs):

  HKCU\\Software\\DAUM\\PotPlayerMini64\\MainShortCutList   (subkey)
  HKCU\\Software\\DAUM\\PotPlayerMini64\\PlayShortCutList   (subkey)
  (also PotPlayer64 / PotPlayerMini / PotPlayer when those bases exist)

  Each value under the subkey:
    name  = contiguous decimal index \"0\", \"1\", …   (REG_SZ name)
    type  = REG_SZ
    data  = \"<vk>,<mod>,<cmd>,0\"
            vk  = Windows virtual-key code (decimal)
            mod = PotPlayer HOTKEYF-style bits (1=Shift, 2=Ctrl, 4=Alt, 8=EXT)
            cmd = PotPlayer command id; **0 = no-op** (disables that key binding)
            last field is typically 0

User-defined ShortCutList entries override PotPlayer built-in defaults.
Writing Space/Left/Right (or any Huge binding) as cmd=0 stops PotPlayer's
native Play/Pause or seek for that key without touching unrelated bindings.

PotPlayer reads these lists at **process start**. Huge never kills PotPlayer
to apply changes; a one-time restart (or next movie launch) is required for
registry overrides to take effect inside an already-running player.

Restore policy
--------------
- Backup is taken once (first apply) and never overwritten by later applies.
- Explicit Settings → Restore, or Huge exit (integration off), restores the
  backup baseline.
- Before restoring, live values are compared to the fingerprint of Huge's
  last write. Entries the user added or changed in PotPlayer after that
  write are preserved; Huge no-ops are removed.
"""
from __future__ import annotations

import json
import os
import re
import threading
import winreg
from typing import Dict, Iterable, List, Optional, Tuple

from engine.hotkeys import (
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    MOD_WIN,
    normalize_shortcut,
)

# PotPlayer hotkey modifier bits (HOTKEYF_* style), NOT Huge's MOD_* masks.
_PP_SHIFT = 0x01
_PP_CTRL = 0x02
_PP_ALT = 0x04
_PP_EXT = 0x08

_REG_BASES = (
    r'Software\DAUM\PotPlayerMini64',
    r'Software\DAUM\PotPlayer64',
    r'Software\DAUM\PotPlayerMini',
    r'Software\DAUM\PotPlayer',
)
_LIST_NAMES = ('MainShortCutList', 'PlayShortCutList')

_NOOP_CMD = 0
_BACKUP_NAME = 'potplayer_shortcut_backup.json'
_META_NAME = 'potplayer_shortcut_meta.json'

_lock = threading.RLock()


def _profile_dir() -> str:
    override = os.environ.get('HUGE_PROFILE_DIR')
    if override:
        return override
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'HugeWatchParty')


def _backup_path() -> str:
    return os.path.join(_profile_dir(), _BACKUP_NAME)


def _meta_path() -> str:
    return os.path.join(_profile_dir(), _META_NAME)


def huge_mods_to_pp(mods: int) -> int:
    """Map Huge MOD_* bitfield to PotPlayer HOTKEYF-style modifiers."""
    mods = int(mods or 0)
    pp = 0
    if mods & MOD_SHIFT:
        pp |= _PP_SHIFT
    if mods & MOD_CTRL:
        pp |= _PP_CTRL
    if mods & MOD_ALT:
        pp |= _PP_ALT
    return pp


def format_noop_entry(vk: int, huge_mods: int = 0) -> str:
    """Format a no-op ShortCutList value (verified: Space/Left/Right use mod 0)."""
    pp_mod = huge_mods_to_pp(huge_mods)
    return f'{int(vk) & 0xFF},{pp_mod},{_NOOP_CMD},0'


def parse_entry(value: str) -> Optional[Tuple[int, int, int, int]]:
    """Return (vk, pp_mod, cmd, flag) or None if the REG_SZ is not the known format."""
    parts = [p.strip() for p in str(value or '').split(',')]
    if len(parts) < 3:
        return None
    try:
        vk = int(parts[0]) & 0xFF
        pp_mod = int(parts[1]) & 0xFF
        cmd = int(parts[2])
        flag = int(parts[3]) if len(parts) > 3 else 0
        return vk, pp_mod, cmd, flag
    except Exception:
        return None


def entry_key(vk: int, pp_mod: int) -> Tuple[int, int]:
    return int(vk) & 0xFF, int(pp_mod) & 0xFF


def canonicalize_entry(value: str) -> Optional[str]:
    parsed = parse_entry(value)
    if not parsed:
        return None
    vk, pp_mod, cmd, flag = parsed
    return f'{vk},{pp_mod},{cmd},{flag}'


# Arrow / navigation VKs are often marked EXTENDED in PotPlayer's own defaults.
_EXTENDED_VKS = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
    0x5B, 0x5C,
})


def huge_binding_keys(shortcuts: Dict[str, str]) -> List[Tuple[int, int]]:
    """List of (vk, pp_mod) for armed Huge playback shortcuts.

    For extended keys (arrows, etc.) also reserve the EXT-bit variant so both
    PotPlayer default encodings are covered.
    """
    keys = []
    seen = set()
    for action in ('play_pause', 'seek_back', 'seek_forward'):
        parsed = normalize_shortcut((shortcuts or {}).get(action))
        if not parsed:
            continue
        huge_mods, vk = parsed
        pp_mod = huge_mods_to_pp(huge_mods)
        variants = [pp_mod]
        if (vk & 0xFF) in _EXTENDED_VKS and not (pp_mod & _PP_EXT):
            variants.append(pp_mod | _PP_EXT)
        for mod in variants:
            key = entry_key(vk, mod)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _read_reg_list(base: str, list_name: str) -> Optional[Dict[str, str]]:
    path = base + '\\' + list_name
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        return None
    values: Dict[str, str] = {}
    unknown = []
    try:
        i = 0
        while True:
            try:
                name, value, typ = winreg.EnumValue(k, i)
                i += 1
            except OSError:
                break
            if typ != winreg.REG_SZ:
                unknown.append((str(name), typ))
                continue
            values[str(name)] = str(value)
    finally:
        winreg.CloseKey(k)
    if unknown:
        # Refuse to mutate lists we cannot decode (non-REG_SZ values).
        raise RuntimeError(
            f'Unsupported PotPlayer shortcut value type under {path}: {unknown!r}'
        )
    return values or None


def _ordered_entries(values: Optional[Dict[str, str]]) -> List[str]:
    if not values:
        return []
    return [
        values[k] for k in sorted(
            values.keys(), key=lambda x: int(x) if str(x).isdigit() else 10_000,
        )
    ]


def _entry_set(entries: Iterable[str]) -> set:
    out = set()
    for value in entries:
        canon = canonicalize_entry(value)
        if canon:
            out.add(canon)
    return out


def _write_reg_list_ordered(base: str, list_name: str, entries: List[str]) -> bool:
    """Write ShortCutList values surgically. Returns True if the registry changed.

    Does not DeleteKey the whole subkey when updating in place: existing REG_SZ
    values are overwritten / removed by name so unrelated entries we intend to
    keep are rewritten contiguously (PotPlayer expects 0..n-1 indices).
    """
    path = base + '\\' + list_name
    desired = [canonicalize_entry(e) or str(e) for e in entries]
    try:
        live = _read_reg_list(base, list_name)
    except RuntimeError:
        raise
    live_ordered = _ordered_entries(live)
    live_canon = [canonicalize_entry(e) or str(e) for e in live_ordered]
    if live_canon == desired:
        return False  # idempotent: already correct

    if not desired:
        # Fall back to built-in defaults by removing the override key.
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            return True
        except FileNotFoundError:
            return False

    # Create or open; replace value names 0..n-1; delete any extras.
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    changed = False
    try:
        existing_names = set()
        i = 0
        while True:
            try:
                name, _value, _typ = winreg.EnumValue(k, i)
                existing_names.add(str(name))
                i += 1
            except OSError:
                break
        for idx, entry in enumerate(desired):
            name = str(idx)
            prev = None
            if name in existing_names:
                try:
                    prev, _typ = winreg.QueryValueEx(k, name)
                except OSError:
                    prev = None
            if prev != entry:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, entry)
                changed = True
            existing_names.discard(name)
        for name in list(existing_names):
            try:
                winreg.DeleteValue(k, name)
                changed = True
            except OSError:
                pass
    finally:
        winreg.CloseKey(k)
    return changed


def _write_reg_list(base: str, list_name: str, values: Optional[Dict[str, str]]) -> bool:
    if not values:
        return _write_reg_list_ordered(base, list_name, [])
    return _write_reg_list_ordered(base, list_name, _ordered_entries(values))


def discover_ini_paths(potplayer_exe: str = '') -> List[str]:
    """Candidate PotPlayer*.ini paths (portable / 'store settings in ini')."""
    paths: List[str] = []
    exe = str(potplayer_exe or '').strip()
    if exe and os.path.isfile(exe):
        folder = os.path.dirname(exe)
        base = os.path.splitext(os.path.basename(exe))[0]
        paths.append(os.path.join(folder, base + '.ini'))
        paths.append(os.path.join(folder, 'PotPlayerMini64.ini'))
        paths.append(os.path.join(folder, 'PotPlayer64.ini'))
    appdata = os.environ.get('APPDATA') or ''
    for name in ('PotPlayerMini64', 'PotPlayer64', 'PotPlayerMini', 'PotPlayer'):
        paths.append(os.path.join(appdata, name, f'{name}.ini'))
        paths.append(os.path.join(appdata, name, 'PotPlayerMini64.ini'))
    out = []
    seen = set()
    for p in paths:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        if os.path.isfile(ap):
            out.append(ap)
    return out


def _read_ini_section(path: str, section: str) -> Optional[Dict[str, str]]:
    try:
        raw = open(path, 'rb').read()
    except OSError:
        return None
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        text = raw.decode('utf-16')
    else:
        text = raw.decode('utf-8', errors='replace')
    pattern = re.compile(rf'^\[{re.escape(section)}\]\s*$', re.I | re.M)
    m = pattern.search(text)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r'^\[', text[start:], re.M)
    body = text[start: start + nxt.start() if nxt else len(text)]
    values: Dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(';') or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        name, val = line.split('=', 1)
        values[name.strip()] = val.strip()
    return values or None


def _write_ini_section(path: str, section: str, entries: Optional[List[str]]) -> bool:
    """Replace or remove [section]. Preserves encoding (UTF-16 if original was)."""
    try:
        raw = open(path, 'rb').read()
    except OSError:
        return False
    utf16 = raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff')
    text = raw.decode('utf-16') if utf16 else raw.decode('utf-8', errors='replace')
    pattern = re.compile(rf'^\[{re.escape(section)}\]\s*$', re.I | re.M)
    m = pattern.search(text)
    desired_block = None
    if entries:
        desired_block = f'[{section}]\n' + '\n'.join(f'{i}={e}' for i, e in enumerate(entries)) + '\n'
    if m:
        start = m.start()
        nxt = re.search(r'^\[', text[m.end():], re.M)
        end = m.end() + nxt.start() if nxt else len(text)
        current = text[start:end]
        if desired_block is None:
            if not current.strip():
                return True
            text = text[:start] + text[end:]
        else:
            if current == desired_block or current.strip() == desired_block.strip():
                return True
            text = text[:start] + desired_block + text[end:]
    else:
        if desired_block is None:
            return True
        if text and not text.endswith('\n'):
            text += '\n'
        text += desired_block
    data = text.encode('utf-16') if utf16 else text.encode('utf-8')
    tmp = path + '.huge-tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)
    return True


def _active_reg_bases() -> List[str]:
    found = []
    for base in _REG_BASES:
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
            winreg.CloseKey(k)
            found.append(base)
        except FileNotFoundError:
            continue
    return found


def verify_registry_format() -> dict:
    """Probe live ShortCutList values and confirm REG_SZ \"vk,mod,cmd,flag\" shape.

    If lists are empty/absent (pure defaults), performs a temporary write/read
    round-trip on a disposable index then removes it — never leaves residue.
    """
    report = {
        'ok': False,
        'bases': [],
        'samples': [],
        'value_type': 'REG_SZ',
        'data_format': 'vk,mod,cmd,flag',
    }
    bases = _active_reg_bases()
    report['bases'] = bases
    if not bases:
        report['error'] = 'No PotPlayer registry bases found'
        return report

    for base in bases:
        for list_name in _LIST_NAMES:
            path = base + '\\' + list_name
            try:
                live = _read_reg_list(base, list_name)
            except RuntimeError as exc:
                report['error'] = str(exc)
                return report
            if live:
                for name, value in list(live.items())[:5]:
                    parsed = parse_entry(value)
                    report['samples'].append({
                        'path': path,
                        'name': name,
                        'value': value,
                        'parsed': parsed,
                    })
                    if not parsed:
                        report['error'] = f'Unparseable entry {path}\\{name}={value!r}'
                        return report
                report['ok'] = True
                continue

            # Absent/empty — prove format with a disposable probe entry.
            probe = '32,0,0,0'
            try:
                k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
                try:
                    winreg.SetValueEx(k, '0', 0, winreg.REG_SZ, probe)
                    got, typ = winreg.QueryValueEx(k, '0')
                    if typ != winreg.REG_SZ or str(got) != probe or not parse_entry(str(got)):
                        report['error'] = f'Probe round-trip failed under {path}'
                        return report
                    report['samples'].append({
                        'path': path,
                        'name': '0',
                        'value': got,
                        'parsed': parse_entry(str(got)),
                        'probe': True,
                    })
                    report['ok'] = True
                finally:
                    try:
                        winreg.DeleteValue(k, '0')
                    except OSError:
                        pass
                    winreg.CloseKey(k)
                # Remove empty key so defaults stay active.
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
                except FileNotFoundError:
                    pass
            except OSError as exc:
                report['error'] = f'Cannot probe {path}: {exc}'
                return report
    return report


def snapshot_all(potplayer_exe: str = '') -> dict:
    """Capture current shortcut lists for backup."""
    reg = {}
    for base in _active_reg_bases():
        reg[base] = {}
        for list_name in _LIST_NAMES:
            reg[base][list_name] = _read_reg_list(base, list_name)
    ini = {}
    for path in discover_ini_paths(potplayer_exe):
        ini[path] = {}
        for list_name in _LIST_NAMES:
            ini[path][list_name] = _read_ini_section(path, list_name)
    return {'version': 1, 'registry': reg, 'ini': ini}


def _strip_noop_entries(values: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Drop cmd=0 rows so a backup never treats Huge no-ops as the user's prefs."""
    if values is None:
        return None
    cleaned: Dict[str, str] = {}
    for name, value in values.items():
        parsed = parse_entry(value)
        if parsed and parsed[2] == _NOOP_CMD:
            continue
        cleaned[str(name)] = str(value)
    return cleaned or None


def _sanitize_snapshot(data: dict) -> dict:
    reg = {}
    for base, lists in (data.get('registry') or {}).items():
        reg[base] = {
            list_name: _strip_noop_entries(values)
            for list_name, values in (lists or {}).items()
        }
    ini = {}
    for path, lists in (data.get('ini') or {}).items():
        ini[path] = {
            list_name: _strip_noop_entries(values)
            for list_name, values in (lists or {}).items()
        }
    return {'version': 1, 'registry': reg, 'ini': ini}


def _ensure_backup(potplayer_exe: str = '') -> dict:
    """Create backup once. Later launches never overwrite it."""
    path = _backup_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get('version') == 1:
                return data
        except Exception:
            pass
    data = _sanitize_snapshot(snapshot_all(potplayer_exe))
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
    return data


def _save_meta(meta: dict) -> None:
    path = _meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, indent=2)


def _load_meta() -> dict:
    path = _meta_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_entries(
    original: Optional[Dict[str, str]],
    live: Optional[Dict[str, str]],
    huge_keys: Iterable[Tuple[int, int]],
) -> List[str]:
    huge_set = {entry_key(vk, mod) for vk, mod in huge_keys}
    result: Dict[Tuple[int, int], str] = {}

    def absorb(src: Optional[Dict[str, str]]):
        if not src:
            return
        for _name, value in src.items():
            parsed = parse_entry(value)
            if not parsed:
                continue
            vk, pp_mod, cmd, flag = parsed
            key = entry_key(vk, pp_mod)
            if key in huge_set:
                continue
            result[key] = f'{vk},{pp_mod},{cmd},{flag}'

    absorb(original)
    if live is not None:
        original_keys = set()
        if original:
            for _n, value in original.items():
                parsed = parse_entry(value)
                if parsed:
                    original_keys.add(entry_key(parsed[0], parsed[1]))
        for _name, value in live.items():
            parsed = parse_entry(value)
            if not parsed:
                continue
            vk, pp_mod, cmd, flag = parsed
            key = entry_key(vk, pp_mod)
            if key in huge_set or key in result:
                continue
            if cmd == _NOOP_CMD and key not in original_keys:
                continue
            result[key] = f'{vk},{pp_mod},{cmd},{flag}'

    for vk, pp_mod in huge_set:
        result[(vk, pp_mod)] = f'{vk},{pp_mod},{_NOOP_CMD},0'

    return [result[k] for k in sorted(result.keys())]


def _merge_restore_entries(
    backup_values: Optional[Dict[str, str]],
    live_values: Optional[Dict[str, str]],
    last_written: Optional[List[str]],
    last_huge_keys: Iterable[str],
) -> List[str]:
    """Restore backup, but keep live entries that appeared after Huge's last write."""
    huge_keys = set()
    for item in last_huge_keys or []:
        try:
            vk_s, mod_s = str(item).split(',', 1)
            huge_keys.add(entry_key(int(vk_s), int(mod_s)))
        except Exception:
            continue
    last_set = _entry_set(last_written or [])
    result: Dict[Tuple[int, int], str] = {}

    for value in _ordered_entries(backup_values):
        parsed = parse_entry(value)
        if not parsed:
            continue
        vk, pp_mod, cmd, flag = parsed
        result[entry_key(vk, pp_mod)] = f'{vk},{pp_mod},{cmd},{flag}'

    for value in _ordered_entries(live_values):
        canon = canonicalize_entry(value)
        parsed = parse_entry(value)
        if not parsed or not canon:
            continue
        vk, pp_mod, cmd, flag = parsed
        key = entry_key(vk, pp_mod)
        if canon in last_set:
            # Part of Huge's last apply — drop on restore.
            continue
        if cmd == _NOOP_CMD and key in huge_keys:
            continue
        # User-added or user-changed after Huge's write — keep.
        result[key] = canon

    return [result[k] for k in sorted(result.keys())]


def _validate_live_lists() -> Optional[str]:
    """Return an error string if existing ShortCutList values are not REG_SZ vk,mod,cmd,flag."""
    for base in _active_reg_bases():
        for list_name in _LIST_NAMES:
            try:
                live = _read_reg_list(base, list_name)
            except RuntimeError as exc:
                return str(exc)
            if not live:
                continue
            for name, value in live.items():
                if not parse_entry(value):
                    return (
                        f'Unparseable PotPlayer shortcut at '
                        f'HKCU\\{base}\\{list_name}\\{name}={value!r}'
                    )
    return None


def apply_huge_conflicts(shortcuts: Dict[str, str], potplayer_exe: str = '') -> dict:
    """Apply no-op overrides for Huge's armed shortcuts. Idempotent."""
    with _lock:
        err = _validate_live_lists()
        if err:
            return {'ok': False, 'error': err}

        huge_keys = huge_binding_keys(shortcuts)
        backup = _ensure_backup(potplayer_exe)
        applied = []
        changed_any = False
        last_written: Dict[str, Dict[str, List[str]]] = {}

        for base in _active_reg_bases():
            last_written[base] = {}
            for list_name in _LIST_NAMES:
                original = (backup.get('registry') or {}).get(base, {}).get(list_name)
                live = _read_reg_list(base, list_name)
                if not huge_keys:
                    entries = _ordered_entries(original) if original else []
                else:
                    entries = _build_entries(original, live, huge_keys)
                changed = _write_reg_list_ordered(base, list_name, entries)
                changed_any = changed_any or changed
                last_written[base][list_name] = list(entries)
                applied.append({
                    'kind': 'registry',
                    'base': base,
                    'list': list_name,
                    'entries': len(entries),
                    'changed': changed,
                    'restored': not bool(huge_keys),
                })

        for path in discover_ini_paths(potplayer_exe):
            for list_name in _LIST_NAMES:
                original = (backup.get('ini') or {}).get(path, {}).get(list_name)
                live = _read_ini_section(path, list_name)
                if live is None and original is None and _active_reg_bases():
                    continue
                if not huge_keys:
                    entries = _ordered_entries(original) if original else []
                else:
                    entries = _build_entries(original, live, huge_keys)
                before = _ordered_entries(live)
                _write_ini_section(path, list_name, entries or None)
                changed = _entry_set(before) != _entry_set(entries)
                changed_any = changed_any or changed
                applied.append({
                    'kind': 'ini',
                    'path': path,
                    'list': list_name,
                    'entries': len(entries),
                    'changed': changed,
                })

        meta = {
            'huge_keys': [f'{vk},{mod}' for vk, mod in huge_keys],
            'last_written': last_written,
            'applied': applied,
            'changed': changed_any,
            'restored': False,
        }
        _save_meta(meta)
        return {
            'ok': True,
            'huge_keys': meta['huge_keys'],
            'applied': applied,
            'changed': changed_any,
            # Overrides load when PotPlayer next starts — Huge does not kill it.
            'restart_potplayer': True,
            'restart_required_note': (
                'PotPlayer reads shortcut lists at launch. '
                'If it is already open, restart it once when convenient — '
                'Huge will not close your movie automatically.'
            ),
            'backup': _backup_path(),
            'format': {
                'paths': [
                    rf'HKCU\{b}\{n}' for b in _active_reg_bases() for n in _LIST_NAMES
                ],
                'value_type': 'REG_SZ',
                'data': 'vk,mod,cmd,0',
            },
        }


def restore_original(potplayer_exe: str = '') -> dict:
    """Restore PotPlayer shortcuts using the safe merge policy (see module doc)."""
    with _lock:
        path = _backup_path()
        if not os.path.isfile(path):
            return {'ok': True, 'restored': False, 'reason': 'no_backup'}
        with open(path, 'r', encoding='utf-8') as fh:
            backup = json.load(fh)
        meta = _load_meta()
        last_written = meta.get('last_written') or {}
        last_huge_keys = meta.get('huge_keys') or []
        restored = []
        preserved = []

        for base, lists in (backup.get('registry') or {}).items():
            for list_name, values in (lists or {}).items():
                live = _read_reg_list(base, list_name)
                written = (last_written.get(base) or {}).get(list_name)
                entries = _merge_restore_entries(values, live, written, last_huge_keys)
                # If no last_written fingerprint, fall back to pure backup when
                # live only contains our known no-ops / matches backup+noops.
                if written is None and live:
                    live_set = _entry_set(_ordered_entries(live))
                    backup_set = _entry_set(_ordered_entries(values))
                    only_noops = all(
                        (parse_entry(e) or (0, 0, -1, 0))[2] == _NOOP_CMD
                        for e in live_set - backup_set
                    )
                    if only_noops:
                        entries = _ordered_entries(values)
                before = _entry_set(_ordered_entries(live))
                after = _entry_set(entries)
                kept = after - _entry_set(_ordered_entries(values))
                if kept:
                    preserved.append({
                        'kind': 'registry', 'base': base, 'list': list_name,
                        'entries': sorted(kept),
                    })
                _write_reg_list_ordered(base, list_name, entries)
                restored.append({
                    'kind': 'registry', 'base': base, 'list': list_name,
                    'changed': before != after,
                })

        for ini_path, lists in (backup.get('ini') or {}).items():
            if not os.path.isfile(ini_path):
                continue
            for list_name, values in (lists or {}).items():
                live = _read_ini_section(ini_path, list_name)
                entries = _merge_restore_entries(
                    values, live, None, last_huge_keys,
                )
                _write_ini_section(ini_path, list_name, entries or None)
                restored.append({'kind': 'ini', 'path': ini_path, 'list': list_name})

        _save_meta({
            'restored': True,
            'huge_keys': [],
            'last_written': {},
            'preserved_user_entries': preserved,
        })
        return {
            'ok': True,
            'restored': True,
            'items': restored,
            'preserved_user_entries': preserved,
            'restart_potplayer': True,
            'restart_required_note': (
                'Restored registry takes effect the next time PotPlayer starts. '
                'Huge does not close PotPlayer.'
            ),
        }


def is_supported_on_this_machine() -> dict:
    """Report whether we can identify PotPlayer shortcut storage here."""
    bases = _active_reg_bases()
    inis = discover_ini_paths()
    fmt = None
    if bases:
        try:
            fmt = verify_registry_format()
        except Exception as exc:
            fmt = {'ok': False, 'error': str(exc)}
    return {
        'ok': bool(bases) or bool(inis),
        'registry_bases': bases,
        'ini_files': inis,
        'format': 'MainShortCutList/PlayShortCutList REG_SZ \"vk,mod,cmd,0\" (cmd 0 = no-op)',
        'format_probe': fmt,
    }
