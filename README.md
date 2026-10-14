# Huge

Windows watch-party app for PotPlayer. Friends play the same local movie in sync over LAN or Tailscale.

**Current release: v2.0.5**

## For users (no Python needed)

Prefer the installer:

1. Download **`Huge-v2.0.5-Setup.exe`** from the [GitHub releases](https://github.com/vsnq0/Huge/releases)
2. Run it
3. Open **Huge** from the Desktop or Start Menu shortcut

Or use the portable zip:

1. Download **`Huge-v2.0.5-windows-portable.zip`** from the [GitHub releases](https://github.com/vsnq0/Huge/releases)
2. Extract the whole zip (you must get a `Huge` folder that contains both `Huge.exe` and `_internal`)
3. Run `Huge\Huge.exe`

If you already have **v2.0.3+**, open Huge → Settings → **Check for Updates** → **Update Now**. That check reads [the latest public release](https://github.com/vsnq0/Huge/releases/latest). You do not need to download the installer from GitHub manually.

You need:

- Windows 10/11
- PotPlayer 64-bit
- Microsoft Edge WebView2 (already on most Windows 11 PCs)
- Tailscale optional (for remote friends)

You do **not** need Python, Cursor, Node.js, or `Huge.cmd`.

### Important

**Never distribute or run a lone `Huge.exe` without its `_internal` folder.**  
That causes `Failed to load Python DLL … python313.dll` on PCs that do not have Python installed.

## For developers

See:

- [BUILD.md](BUILD.md) — how to build the ONEDIR app and installer
- [HOW_TO_UPDATE.md](HOW_TO_UPDATE.md) — how to change UI, features, bugs, and rebuild
- [CHANGELOG.md](CHANGELOG.md) — what changed

## Project layout

| Path | Purpose |
|------|---------|
| `desktop.py` | Windows shell (pywebview window + file dialogs) |
| `engine/session.py` | Lobby, PotPlayer control, sync, profile |
| `ui/` | React + Vite Figma UI |
| `ui/src/App.tsx` | Main UI |
| `ui/src/theme.css` | Styles |
| `release/Huge/` | Complete portable ONEDIR app (after build) |
| `release/Huge Setup.exe` | Installer (after build) |
| `release/Huge-v*-Setup.exe` | Versioned installer for GitHub |
| `release/Huge-v*-windows-portable.zip` | Versioned portable zip for GitHub |

Profile data (banner, avatar, name, PotPlayer path) is stored in:

```
%APPDATA%\HugeWatchParty\
```

That folder survives app updates.
