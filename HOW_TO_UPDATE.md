# How to update Huge

Simple guide for changing the app and making a new installer.

## 1. Change the UI (look and layout)

UI files live here:

- `ui/src/App.tsx` — screens, buttons, Live Log, modals
- `ui/src/theme.css` — colors, sizes, layout
- `ui/src/ImageEditor.tsx` — banner / profile picture editor

After editing:

```powershell
cd ui
npm run build
cd ..
```

Then rebuild the Windows app (section 5).

Preview while coding (optional):

```powershell
cd ui
npm run dev
```

That preview is only for UI work. The real app still runs through `desktop.py` / `Huge.exe`.

## 2. Add or remove features

Most behavior is in:

- `engine/session.py` — lobby, sync, PotPlayer, logs, profile save/load
- `desktop.py` — window, file pickers, JS bridge methods

Examples:

- New button in the UI → add it in `App.tsx`, then add a matching method on `Api` in `desktop.py`, then implement it in `engine/session.py`
- Change skip / pause / ready text → edit the `append_log(...)` calls in `engine/session.py`
- Change saved profile fields → `_save_profile` / `_load_profile` in `engine/session.py`

Do not delete working PotPlayer or Tailscale code unless you mean to remove that feature.

## 3. Fix bugs

1. Reproduce the bug in the running app
2. Fix the smallest place that causes it (usually `engine/session.py` or `ui/src/App.tsx`)
3. Test again
4. Rebuild (section 5)

Useful local run (developers):

```powershell
py -3 desktop.py
```

## 4. Rebuild the UI only

```powershell
cd ui
npm install
npm run build
cd ..
```

## 5. Build a new Huge.exe and installer

From the `huge` folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _build_release.ps1
```

Outputs (keep the whole folder together):

- `release\Huge\` — complete ONEDIR app (`Huge.exe` + `_internal\`)
- `release\Huge Setup.exe` / `release\Huge-v*-Setup.exe`
- `release\Huge-v*-windows-portable.zip`

Never ship `Huge.exe` alone. Clean PCs need `_internal\python313.dll`.

## 6. Install the new build on this PC

1. Quit Huge if it is open
2. Run `release\Huge Setup.exe`
3. Choose **Yes** if asked to launch
4. Or open the **Huge** shortcut on the Desktop

## 7. Keep your settings

Your name, PotPlayer path, banner, and profile picture stay in:

```
%APPDATA%\HugeWatchParty\
```

Reinstalling Huge does not wipe that folder.

## 8. Backup before big changes

Copy the whole `huge` folder, or zip it, before risky edits.

The packaging script does not replace that habit — make a backup when the app is in a good state.

## 9. Where published updates come from

Installers for users are published on the public repository [vsnq0/Huge](https://github.com/vsnq0/Huge). The automatic updater checks:

`https://api.github.com/repos/vsnq0/Huge/releases/latest`

See [BUILD.md](BUILD.md) for which files to upload. Do not point the updater at a private source repository.
