# Huge Watch Party

**Watch together with PotPlayer — each person plays their own local copy of the movie.**

Huge is a Windows watch-party app that coordinates playback between friends using PotPlayer 64-bit. Create a lobby, invite friends over your local network or Tailscale, load the same video on each computer, and control playback together.

<p>
  <a href="https://github.com/vsnq0/Huge/releases/latest"><strong>Download the latest release</strong></a>
  · <a href="https://github.com/vsnq0/Huge/tree/main">Browse the source code</a>
  · <a href="https://github.com/vsnq0/Huge/releases">All releases</a>
</p>

## What Huge does

- **Host or join a lobby** to watch with friends.
- **Synchronize playback controls** such as play, pause, seeking, and forward/backward skips.
- **Ready up and start together.** The host can start the movie after participants are ready; Huge is designed to reset loaded players to the beginning before starting.
- **Use PotPlayer directly or use Huge's controls** for supported playback actions.
- **Personalize your profile** with a name, avatar, and banner.
- **See lobby activity** in the Live Log.
- **Troubleshoot synchronization** by exporting diagnostic logs from Settings → Troubleshooting → Export Sync Logs.
- **Check for updates inside the app** through Settings → Check for Updates. When a newer stable release is available, Huge may also show an update banner.

> **Important:** Huge coordinates playback; it does not stream or transfer the movie file. Every participant needs their own local copy of the same video. Playback behavior also depends on the installed PotPlayer version and network conditions.

## Requirements

- Windows 10 or Windows 11.
- PotPlayer **64-bit** installed on each computer.
- A working network connection between participants. Use the same local network for nearby PCs, or Tailscale for friends connecting over the internet.
- Microsoft Edge WebView2 Runtime for the app interface. It is already present on many Windows installations; if Huge reports that it is missing, install the official WebView2 Runtime from Microsoft.
- Internet access for checking and downloading updates.

The standard Windows installer includes the app's packaged runtime. Most users do **not** need to install Python, Node.js, or Cursor.

## Quick start

### 1. Download and install Huge

1. Open the [latest Huge release](https://github.com/vsnq0/Huge/releases/latest).
2. Download `Huge-vX.Y.Z-Setup.exe` from the **Assets** section (the version number will change with future releases).
3. Run the installer and follow its instructions.
4. Open Huge from the Start menu or desktop shortcut.

Only download installers from this repository's official [Releases page](https://github.com/vsnq0/Huge/releases). If Windows displays a security warning, verify the download source and follow Windows' security guidance; do not disable security features just to run an unverified file.

### 2. Configure PotPlayer

1. Install PotPlayer 64-bit if it is not already installed.
2. Open Huge and find the **PotPlayer** section.
3. Choose **Select PotPlayer path** and select the correct 64-bit PotPlayer executable if Huge has not detected it automatically.

### 3. Connect with friends

1. If you are playing over the internet, make sure everyone has Tailscale installed, signed in, and connected to the same permitted network. On a local network, Tailscale may not be necessary.
2. The host opens Huge and selects **Create lobby**.
3. The host shares the lobby connection address shown by Huge.
4. Each friend opens Huge, chooses **Join lobby**, and enters the host's address.
5. Everyone should use the same Huge release while testing or watching together.

### 4. Load and start the movie

1. Each participant selects the **same local video file** on their own computer. No movie is sent through Huge.
2. Wait for PotPlayer to finish loading the video.
3. Everyone presses **Ready up**.
4. The host presses **Start Movie** when the required participants are ready.
5. Use Huge's playback controls or supported PotPlayer controls to play, pause, and seek together.

If the players disagree, stop and reconnect rather than repeatedly issuing commands. See [Troubleshooting](#troubleshooting) below.

## How Huge works

Huge combines a Windows desktop shell, a web-based user interface, a Python playback/networking engine, and PotPlayer control.

1. **The interface** displays the lobby, profiles, readiness, playback controls, Live Log, and update settings.
2. **The local engine** communicates with PotPlayer, observes playback state, and handles local user actions.
3. **The lobby connection** sends playback commands and state information between the host and joiners.
4. **Playback synchronization** orders actions and attempts to keep participants' playback states and positions aligned while avoiding duplicate commands and stale updates.
5. **Start Movie** coordinates the loaded players and is intended to reset them to the beginning before playback starts.
6. **The updater** checks the official GitHub Releases for a newer stable version and downloads the release through the app's update flow.

Because PotPlayer is an external media player and each computer has its own copy of the file, exact response times can vary with the PotPlayer build, media format, computer performance, and network conditions. Report reproducible issues instead of assuming all combinations behave identically.

## Updating Huge

1. Open Huge.
2. Go to **Settings → Check for Updates**, or use the update banner if one appears.
3. Review the offered version and release notes.
4. Choose **Update Now** and allow the installer to complete.

Updates are published as separate GitHub Releases. Previous releases remain available on the [Releases page](https://github.com/vsnq0/Huge/releases). User profile data is stored locally under `%APPDATA%\HugeWatchParty\` and is intended to survive ordinary app updates.

If an in-app update fails, note the error and use the official release page to identify the installer for your current supported version. Do not run update files from unofficial URLs.

## Troubleshooting

### PotPlayer is not detected

- Confirm that PotPlayer 64-bit is installed.
- Open Huge's PotPlayer section and select the correct executable path.
- If you recently moved PotPlayer, select the new path again.

### A friend cannot join the lobby

- Confirm both PCs are online and the host's lobby is still running.
- For remote connections, confirm Tailscale is running on both PCs and both users can reach the host through their permitted Tailscale network.
- Make sure the host shares the address shown by Huge, not an unrelated local address.
- Check Windows Firewall if the connection is blocked; only allow the required app/network access you understand and trust.

### Playback pauses, skips, or gets out of sync

- Confirm everyone is using the same Huge version.
- Confirm everyone selected the same video file and that each local copy plays correctly in PotPlayer.
- Try leaving and rejoining the lobby, then load the video and use Ready up → Start Movie again.
- If the issue remains, reproduce it and export logs on **each affected PC** using **Settings → Troubleshooting → Export Sync Logs**.
- Keep the test steps and time of the issue with the exported ZIP files. Review logs before sharing them publicly: they may contain usernames, local file paths, addresses, timestamps, or diagnostic details. Share them privately with the maintainer when possible.

### Profile picture looks wrong

Open the profile editor, select the image again, adjust its crop, and save. If the problem only appears for remote lobby members, export logs from the affected PCs and report which participant's avatar is missing.

### Windows warns about the installer

Always verify that the installer was downloaded from the official [Huge Releases](https://github.com/vsnq0/Huge/releases) page. Unsigned software may trigger Windows security warnings. Do not disable Smart App Control or other protections just to run an unverified download.

## Source code and development

The public application source lives in this repository root.

Start with:

- [`DEVELOPER_README.md`](DEVELOPER_README.md) — developer overview and setup.
- [`BUILD.md`](BUILD.md) — build and packaging instructions.
- [`HOW_TO_UPDATE.md`](HOW_TO_UPDATE.md) — guidance for maintaining and rebuilding the app.
- [`CHANGELOG.md`](CHANGELOG.md) — recorded changes.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — third-party acknowledgements and license information.

### High-level project layout

| Path | Purpose |
|---|---|
| `desktop.py` | Windows desktop shell and WebView integration |
| `engine/` | PotPlayer control, playback synchronization, lobby networking, updates |
| `ui/` | React/Vite interface source |
| `docs/` | Additional project documentation |
| `BUILD.md` | Build instructions |

Follow the source documentation for exact tool versions and commands. Do not commit release binaries, private profile data, exported logs, credentials, or local build output to the source repository.

## Releases and support

- **Latest download:** https://github.com/vsnq0/Huge/releases/latest
- **All releases:** https://github.com/vsnq0/Huge/releases
- **Source code:** https://github.com/vsnq0/Huge/tree/main
- **Report an issue:** https://github.com/vsnq0/Huge/issues

When reporting a bug, include the Huge version, Windows version, PotPlayer 64-bit version if known, whether the issue affects the host or a joiner, the steps to reproduce it, and diagnostic logs where appropriate.

## Third-party acknowledgements

Huge includes or adapts third-party work in parts of its synchronization implementation. Review [`source/THIRD_PARTY_NOTICES.md`](source/THIRD_PARTY_NOTICES.md) and the relevant license files for attribution and licensing details.
