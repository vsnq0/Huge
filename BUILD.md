# Building Huge

## What you get

After a successful build (version from `VERSION.txt`):

- `release\Huge\` — complete PyInstaller **ONEDIR** folder (`Huge.exe` + `_internal\`)
- `release\Huge Setup.exe` — installer that installs that **entire** folder
- `release\Huge-vX.Y.Z-Setup.exe` — same installer, versioned filename for GitHub
- `release\Huge-vX.Y.Z-windows-portable.zip` — zipped complete `Huge\` folder for GitHub

Installed layout:

```
%LOCALAPPDATA%\Programs\Huge\
  Huge.exe
  _internal\
    python313.dll
    … runtime DLLs and app assets …
```

## One-command build

From the `huge` folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _build_release.ps1
```

This will:

1. Archive any previous `release\` into `release-archive\` (does not delete older GitHub tags)
2. Build the UI (`ui` → `ui/dist`)
3. Package Python + UI into `dist\Huge` with PyInstaller ONEDIR
4. Verify `_internal\python313.dll` exists
5. Stage `release\Huge` and create the portable zip
6. Compile Setup.exe with the **full** onedir payload embedded
7. Verify the embedded payload contains `_internal\python313.dll`

## Requirements to build (developers only)

- Windows
- Python 3.13 with:
  - `pywebview`
  - `pyinstaller`
- Node.js + npm (only to rebuild the UI)
- .NET Framework 4.x `csc.exe` (comes with Windows)

Install Python deps:

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller
```

## GitHub release upload set

Publish releases on the public repository [vsnq0/Huge](https://github.com/vsnq0/Huge). The in-app updater reads only:

`https://api.github.com/repos/vsnq0/Huge/releases/latest`

For tag **vX.Y.Z**, upload **only**:

1. `Huge-vX.Y.Z-Setup.exe`
2. `Huge-vX.Y.Z-windows-portable.zip`

Do **not** upload a bare `Huge.exe`.  
Do **not** overwrite an older release (e.g. keep v2.0.0; publish v2.0.1 as a new release).

## Notes

- `console=False` (no black CMD window).
- WebView2 must be available on the target PC (normal on Windows 11).
- Keep `Huge.exe` next to `_internal\` forever.
- End users should prefer the Setup.exe.
- Testing only on a PC that already has Python installed can hide a broken package — always verify the zip/Setup contains `_internal\python313.dll`.
