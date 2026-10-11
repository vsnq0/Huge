# Huge — source code

Windows watch-party app for PotPlayer. Friends play the same local movie in sync over LAN or Tailscale.

**Source version in this tree: see `VERSION.txt` (currently 2.0.13).**

Binary releases (installer / portable zip) are published separately on the
[Huge-Download](https://github.com/vsnq0/Huge-Download) GitHub Releases page.
This folder is the application **source** for developers.

## Requirements (end users)

- Windows 10/11
- PotPlayer 64-bit (`PotPlayerMini64.exe`)
- Microsoft Edge WebView2 (included on most Windows 11 PCs)
- Tailscale optional (for friends outside your LAN)

End users do **not** need Python or Node.js.

## Developer setup

### Prerequisites

- Windows
- Python 3.13
- Node.js + npm (to build the React UI)
- .NET Framework 4.x `csc.exe` (usually already on Windows; used by the release script)

### Install dependencies

```powershell
cd source
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller
cd ui
npm install
cd ..
```

### Run from source (dev)

```powershell
cd ui
npm run build
cd ..
py -3 desktop.py
```

Or, after `npm run build`, use `Huge.cmd` if present.

### Build installer + portable zip

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _build_release.ps1
```

Outputs go under `release\` (not included in this source package). Details: [BUILD.md](BUILD.md).

### Automated lobby tests (FakePot)

```powershell
py -3 _test_multi_lobby.py --quick
py -3 _test_pause_hold.py
```

These use independent processes and FakePot; they do not require real PotPlayer windows.

## Layout

| Path | Purpose |
|------|---------|
| `desktop.py` | pywebview shell + file dialogs |
| `engine/` | Lobby networking, PotPlayer control, sync, updater |
| `ui/` | React + Vite UI (`ui/src`) |
| `BUILD.md` | Packaging / ONEDIR / installer notes |
| `CHANGELOG.md` | Release history |
| `THIRD_PARTY_NOTICES.md` | Third-party attribution (incl. Syncplay) |
| `syncplay-ref/LICENSE` | Apache 2.0 text for Syncplay-adapted methods |
| `_test_*.py`, `_huge_headless_peer.py` | Multi-lobby / sync regression harness |

## License / attribution

Huge application code is provided as in this repository. Portions of playback
authority logic are adapted from [Syncplay](https://github.com/Syncplay/syncplay)
(Apache License 2.0). See `THIRD_PARTY_NOTICES.md` and `syncplay-ref/LICENSE`.

## Notes

- Do not distribute a bare `Huge.exe` without its `_internal\` folder.
- Profile data (name, avatar, banner, PotPlayer path) lives under
  `%APPDATA%\HugeWatchParty\` and is never part of this source tree.
- Automatic updates check `https://github.com/vsnq0/Huge-Download/releases/latest`
  (see `engine/updater.py`).
