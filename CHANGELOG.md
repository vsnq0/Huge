# Changelog

## 2.0.28 — PotPlayer shortcut priority + no duplicate seeks

### Summary

- Assigned Huge shortcuts (Space / Left / Right or custom) are intercepted while PotPlayer is focused and routed through Huge’s synchronized Play/Pause and seek paths — no Alt+Tab required.
- Windows key auto-repeat no longer enqueues multiple seeks/Play-Pause commands: one physical key-down → one lobby action; repeats are still consumed so PotPlayer never sees them.
- Monitor settle after a Huge-owned seek no longer republishes a second PotPlayer seek command or duplicate Live Log line.
- PotPlayer window detection is more reliable when the title is only the media filename (class + process image).
- Unassigned PotPlayer keys and typing in other apps remain unchanged; cleared bindings stop being intercepted immediately.

### Testing

- `_test_hotkey_hook_safety.py` (hold/repeat single-fire), `_test_seek_loop.py` (no monitor republish + attribution)
- `_test_play_pause_sync_fix.py`, `_test_keyboard_shortcuts.py`, `_test_shortcut_rebind.py`
- Live PotPlayer suppress / typing tests when available

## 2.0.27 — Seek sync publish-first + activity log source

### Summary

- Huge skip buttons (±5s / ±30s), Left/Right while Huge is focused, and Left/Right intercepted when PotPlayer is focused all use the same queued synchronized seek path.
- Each user seek action publishes one lobby `skip_rel` command **before** the local PotPlayer `SET_CURRENT`, so peers no longer wait on the initiator’s player round-trip.
- Local apply retries after a successful publish do not broadcast again (one action → one seek event).
- Rapid consecutive seeks keep order via the existing skip queue; incremental deltas are unchanged (not Play/Pause state toggles).
- Activity log keeps the original input source: `(Huge)` for UI / Huge-focused shortcuts, `(PotPlayer)` for PotPlayer-focused intercepts and genuine PotPlayer seeks (e.g. `vsnq skipped forward 5s (Huge).`).

### Testing

- `_test_seek_loop.py` (publish-before-apply, rapid order, button + hotkey, activity log attribution)
- `_test_play_pause_sync_fix.py`, `_test_keyboard_shortcuts.py`, `_test_hotkey_hook_safety.py`

## 2.0.26 — Fix shortcut recording and stale Space binding

### Summary

- Left Arrow / Right Arrow can be recorded in Settings and display as **Left Arrow** / **Right Arrow**.
- Escape cancels recording without changing the previous binding; arrow keys no longer scroll the settings page while recording.
- Reassigning Play/Pause (e.g. Space → F8) updates the native hook immediately; Space no longer fires Play/Pause. Clear stays cleared; Reset restores defaults intentionally.
- Fixed silent default restoration that could revive Space after an invalid or partial save.

## 2.0.25 — Actually intercept Space / Left / Right before PotPlayer

### Summary

- Default shortcuts are **Space** (Play/Pause), **Left Arrow** (−5s), and **Right Arrow** (+5s).
- The low-level keyboard hook suppresses those keys before PotPlayer can run its native Play/Pause or seek actions, then routes exactly one command through Huge's existing sync path.
- Fixes v2.0.24, which displayed/migrated seek shortcuts to `<` / `>` so Left/Right were never consumed and PotPlayer kept seeking natively.
- Scope detection now also matches PotPlayer by process id and window class; stuck Shift/Ctrl/Alt state is reconciled on the refresh timer so Space is not missed.
- All other PotPlayer shortcuts and typing in other apps remain untouched.

### Testing

- Automated shortcut, hook-safety, Play/Pause sync, and seek regressions.
- Live typing + real PotPlayer suppression tests (native action prevented with noop Huge handler).

## 2.0.24 — Reserve Space / < / > for Huge when PotPlayer is focused

### Summary

- Default playback shortcuts are now **Space** (Play/Pause), **`<`** (seek −5s), and **`>`** (seek +5s).
- When PotPlayer is the active window, Huge consumes only those configured playback keys and routes them through the existing synchronized Play/Pause and seek paths — PotPlayer does not also handle the same keypress.
- Every other PotPlayer shortcut (volume, fullscreen, subtitles, etc.) and normal typing in other apps remain untouched.
- Keeps the v2.0.23 pointer-size-safe hook fix and v2.0.22 publish-first Play/Pause latency fix.

### Details

- `<` / `>` map to Shift+OEM_COMMA / Shift+OEM_PERIOD; bare comma/period are not treated as seek shortcuts.
- Profiles still on the old Left/Right defaults are migrated to `<` / `>` once; custom bindings are preserved.
- Settings → Keyboard Shortcuts still supports record / clear / reset with conflict warnings.

### Testing

- Automated shortcut, hook-safety, Play/Pause sync, and seek regressions passed.
- Live Windows typing test with the real hook passed.
- Real PotPlayer interception and live two-PC sync were not fully re-verified in this build.

## 2.0.23 — Fix keyboard hook blocking all typing

### Summary

- Fixes the v2.0.22 regression where the low-level keyboard hook could freeze or swallow normal typing while Huge was running.
- Keeps customizable Play/Pause / Seek shortcuts and the publish-first Play/Pause latency fix.
- Seeking, install ACL fix, and v2.0.21 sync behavior unchanged.

### Root cause

- The `WH_KEYBOARD_LL` callback called `CallNextHookEx` with a 32-bit `LPARAM` prototype on 64-bit Windows. Passing a 64-bit pointer raised `OverflowError` inside the hook.
- When that failed, the callback did not reliably pass the key through, so Windows stopped delivering keystrokes (keyboard felt disabled).
- The callback also did heavy work (`EnumWindows` / app logic) on the hot path; that is removed.

### Fix

- Use pointer-sized `WPARAM`/`LPARAM`/`LRESULT` prototypes for the hook APIs.
- On any hook error, pass the key through (never swallow unmatched keys).
- Only consume a key when it matches a registered shortcut in Huge/PotPlayer scope.
- Queue shortcut actions on a worker thread; never run playback logic inside the LL callback.
- Cache Huge/PotPlayer HWNDs on a timer (no `EnumWindows` inside the hook).
- Unhook immediately on shutdown.

### Testing

- Automated hook-safety, shortcut, Play/Pause, and seek regressions passed.
- Live Windows typing test with the real hook + Edit control passed (letters/digits type; Space consumed only when scoped; typing works after stop).
- Live two-PC sync timing still not re-verified.

## 2.0.22 — Local Play/Pause latency + custom keyboard shortcuts

### Summary

- Fixes asymmetric Play/Pause delay when **you** initiate playback changes (Huge UI or hotkeys).
- Adds Settings → **Keyboard Shortcuts** (Discord-style): Play/Pause, Seek Backward (−5s), Seek Forward (+5s).
- Shortcuts work while Huge or PotPlayer is focused (native Windows hook). They do not fire in other apps and are ignored while typing in Huge.
- Preserves v2.0.21 Play/Pause feedback suppression, coordinated START MOVIE timing, custom-install ACL fix, and seeking.

### Play/Pause latency (root cause)

- Local initiator path previously called `_apply_play_state` (blocking `pot_status` + confirmation sleeps of ~50–130ms+) **before** broadcasting.
- Remote / PotPlayer-initiated paths published the desired state without that pre-network wait — so friend-initiated sync felt faster.
- Fix: decide desired state from authoritative room intent, **publish first**, then apply PotPlayer without confirmation sleeps on the initiator path. Feedback suppression unchanged.

### Keyboard shortcuts

- Record / clear / reset per action; supports single keys and modifier combos (`Alt+0`, `Ctrl+Shift+1`, etc.).
- Saved in `profile.json` (survives restart and updates).
- Actions use the same synchronized Play/Pause and seek queues as the on-screen buttons.
- Hook consumes the key when handled so PotPlayer does not also process the same press (no duplicate sync events).

### Packaging

- Local build only for this tree: `Huge-v2.0.22-Setup.exe` / portable ZIP + SHA-256 (not published to GitHub in this step).

## 2.0.21 — Play/Pause sync update for clients already on v2.0.20

### Summary

- New updater-visible release for users who already installed **v2.0.20**.
- Same Play/Pause synchronization fix as the corrected v2.0.20 source (commit 4893964): suppress PotPlayer feedback republishes, atomic desired-state intent, relative START MOVIE `go_delay_ms`.
- Keeps the custom-installation ACL probe-first fix from v2.0.20.
- Does **not** include broken v2.0.19 uninstall work.
- Seeking (±5s / ±30s) remains unchanged.

### Why a new version

- An earlier v2.0.20 GitHub release was refreshed in place. Clients already running 2.0.20 need a higher version (`2.0.21`) for the in-app updater to offer the download.

### Packaging

- `Huge-v2.0.21-Setup.exe` / portable ZIP + SHA-256
- Updater: `https://api.github.com/repos/vsnq0/Huge/releases/latest`

## 2.0.20 — Custom-install ACL fix + Play/Pause sync

### Summary

- Official update over working **v2.0.18**.
- Does **not** include the broken v2.0.19 uninstall work.
- Fixes Setup aborting with “could not grant your account access / Attempted to perform an unauthorized operation” when the destination is already writable.
- Fixes Play/Pause desync where one Huge or PotPlayer action could publish twice (Huge + PotPlayer feedback), and improves coordinated START MOVIE timing.

### Installer

- Probe create/write/delete in the chosen folder first; clean up the probe file.
- Continue when inherited permissions already allow installation (no unnecessary ACL rewrite).
- Attempt a narrowly scoped Modify ACE on the install folder only when the write probe fails.
- ACL API failures are fatal only if writes still fail afterward.
- Clear error when the destination remains unwritable after probe + optional ACL retry.

### Play/Pause synchronization

- Represent Play/Pause as an explicit desired state (`playing=true/false`); receivers never blind-toggle.
- Suppress PotPlayer monitor feedback that matches the last authoritative broadcast (one user action → one lobby event).
- Keep dual-threshold player/global pause flags aligned when applying intent so the monitor cannot race a half-updated state.
- Preserve genuine PotPlayer keyboard/mouse toggles, including rapid pause→play within the echo window.
- Reject stale / out-of-order `play_seq` updates so a late message cannot reverse the latest state.
- Coordinated START MOVIE go uses a shared relative `go_delay_ms` (clock-skew safe) instead of relying only on absolute host wall time.
- Playback traces include high-resolution `wall=` / `mono=` timestamps, cmd/lobby ids, and apply/suppress decisions for two-PC correlation.
- Seeking (±5s / ±30s) is unchanged.

### Packaging

- `Huge-v2.0.20-Setup.exe` / portable ZIP + SHA-256
- Updater continues to use `https://api.github.com/repos/vsnq0/Huge/releases/latest`

## 2.0.18 — Elevate Setup when destination needs admin (A:\HUGE errno 13)

### Root cause (friend PC)

- Settings write-probe created `A:\Huge` but could not create a file inside it (`PermissionError` errno 13).
- 2.0.17 treated that as a hard failure and never launched Setup.
- Creating the folder does not mean the unelevated app can install files; the official Setup can request UAC, prepare the folder, and grant the **installing user** Modify on that directory only.

### Fix

- Preflight returns `needs_elevation` on access-denied instead of blocking Confirm.
- Relocation launches Setup with ShellExecute `runas` + `/usersid=<current SID>` + `/DIR=` / `/cleanup-old=` / `/relaunch=`.
- Setup grants Modify (inherit) to that SID on the install folder only — never Everyone, never the whole drive.
- Registry/uninstall records write to the original user’s hive when elevated under another admin.
- UAC cancel / Setup failure leaves the old install, shortcuts, registry, and `%APPDATA%\HugeWatchParty` intact.
- Authoritative write probe runs inside elevated Setup after ACL prepare.

### Setup ACL follow-up (same version rebuild)

- Do **not** abort install when `GetAccessControl`/`SetAccessControl` throws “Attempted to perform an unauthorized operation” if a real create/write/delete probe in the destination already succeeds (common on some drives/filesystems where DACLs are not fully manageable).
- Probe first; attempt scoped ACL only when the probe fails; ACL failure is fatal only when writes still fail afterward.

### Packaging

- `Huge-v2.0.18-Setup.exe` / portable ZIP + SHA-256
- Do not publish without approval

## 2.0.17 — Fix relocate write-probe on unusual drives (e.g. A:\HUGE)

### Root cause

- Settings → Change installation location validates the destination **in Huge (Python) before Setup launches**.
- 2.0.14–2.0.16 used a leading-dot probe (`.huge_write_probe_*`) and treated probe **delete** failure as a write failure.
- On some volumes (often removable / letter A: / policy-restricted), creating that probe or deleting it fails even when the folder itself is creatable — producing:
  `Cannot write to: … Choose a writable folder or run Setup as a user with permission for that drive.`
- The installer was **never launched**; old install, shortcuts, registry, and `%APPDATA%\HugeWatchParty` were already left intact.

### Fix

- Create the destination folder first, then probe **inside** it (never the drive root alone).
- Use `HugeWriteProbe_*.tmp` (no leading dot); ignore delete failures after a successful write.
- Surface Windows error / drive type (`GetDriveTypeW`) in the UI message.
- Append diagnostics to `%APPDATA%\HugeWatchParty\install_location.log` (path tested, operation, error, whether Setup was launched).
- Setup C# validator aligned with the same rules.

### Packaging

- `Huge-v2.0.17-Setup.exe` / portable ZIP + SHA-256
- Do not publish without approval

## 2.0.16 — Fix failed update / “embedded python interpreter”

### Root cause

- Setup cleared an install folder, then `ZipFile.ExtractToDirectory` could fail if any file stayed locked.
- Verification only checked for `Huge.exe` + `python313.dll`, so a half-deleted tree (exe + 2 DLLs) was treated as success.
- Setup still rewrote Desktop/Start Menu shortcuts to that broken folder — including `%TEMP%` paths used by automated `/DIR=` tests.
- Result: shortcut launched an incomplete onedir → **Failed to start embedded python interpreter!**

### Fix

- Prepare install dir must finish fully empty (retry + fail if locked leftovers remain).
- After extract, require a complete `_internal` tree (not just `python313.dll`).
- Do not register shortcuts under `%TEMP%`; support `/noshortcuts` for automated tests.
- `/update` prefers the registered non-TEMP install over a TEMP relaunch/`/DIR=` target.
- Extract failures no longer rewrite shortcuts or claim success.

### Packaging

- `Huge-v2.0.16-Setup.exe` / portable ZIP + SHA-256
- Do not publish without approval

## 2.0.15 — Animated GIFs, movie title fonts, Live Log readability

### Animated GIFs

- Banner and avatar pickers accept animated GIFs and preserve animation through crop/save.
- Crop editor previews GIF animation; crop geometry is applied to every frame.
- Local display uses `data:image/gif` URLs; disk files `avatar_final.gif` / `banner.gif`.
- Lobby multiplayer avatars transfer compact animated GIF thumbs (not local paths); static avatars still use JPEG thumbs.
- Limits: 4 MB file, 2048px edge, 120 frames, lobby thumb ≤ ~120 KB / 48 frames.

### Appearance

- Settings → **Appearance → Movie Title**: font family + size slider with live preview.
- Preferences stored in `%APPDATA%\HugeWatchParty\profile.json` (`title_font`, `title_font_size`).

### Live Log

- Panel ~25% taller; slightly larger event text.
- Bold usernames and bold playback actions / skip amounts via structured `parts` (no HTML injection).

### Packaging

- `Huge-v2.0.15-Setup.exe` / portable ZIP + SHA-256 (Pillow bundled)
- Do not publish without approval

## 2.0.14 — Custom installation location

### Installer

- Setup shows an **Installation location** step with Browse (default remains `%LOCALAPPDATA%\Programs\Huge`).
- Validates writable path, available drive, and free space before extract.
- Records install path in `HKCU\Software\vsnq\Huge\InstallLocation` and an Apps & Features uninstall entry.
- Supports `/DIR=`, `/cleanup-old=` for silent update/migration; `/update` + `/relaunch=` still work with spaces.

### In-app Settings

- Gear → **Installation**: shows the real install path and **Change installation location**.
- Confirm → download official Setup → reinstall at the new folder → recreate shortcuts → relaunch → remove old Huge tree only after verify.
- Profile data under `%APPDATA%\HugeWatchParty` is not moved.

### Updater

- Update handoff always passes `/DIR=<current install>` so custom D: (etc.) installs are not overwritten onto the default C: path.
- Public release API unchanged: `https://api.github.com/repos/vsnq0/Huge/releases/latest`.

### Packaging

- `Huge-v2.0.14-Setup.exe` / portable ZIP + SHA-256
- Do not publish without approval

## 2.0.13 — Joiner Pause→Play + lobby avatars

### Root cause (joiner Play dropped)

- Guest Pause/Play proposals are ordered by `sent_at`.
- When the host **relayed** a guest Pause, `_publish_play_state` stamped `_last_play_sent_at` with the **host wall clock**.
- The joiner’s already-queued Play still carried an earlier guest `sent_at`, so `_accept_play_cmd` rejected it as `drop_stale_guest_play_ts`.
- Result: joiner resumed locally, host (and others) stayed paused.

### Fix (playback)

- Track `_last_guest_play_sent_at` separately from host-relay timestamps; never compare guest proposals to host publish time.
- Clear Syncplay ignore-fly on clients only for **newer** host `play_seq` relays (late Pause cannot undo a newer Play).
- Fix `_log_playback(..., reason=)` kwarg clash that could crash command rejection logging.

### Root cause (joiner avatar black circle)

- Hello/`users` truncated avatar base64 at **120000** characters mid-string, corrupting PNG/JPEG data.
- Host kept a full local `avatar_b64`; joiners’ wire payloads became invalid → React rendered an empty dark circle.

### Fix (avatars)

- Stop mid-truncating; sanitize remote avatars (decode + PNG/JPEG magic).
- Send a small lobby JPEG thumb via `_avatar_for_wire()` / `_make_lobby_avatar_b64` (not a local file path).
- Support `type: avatar` updates; broadcast on join; JPEG `data:` URLs in React.

### Testing (FakePot + real TCP)

- Guest Pause→Play: 60/60 host+1/+2 (gaps 0–200ms); median round-trip ~232ms
- Toggle burst 30/30; pause-hold 30/30; joiner skips 100× host+1/+2
- Avatar sanitize + lobby wire sync
- Real PotPlayer multi-instance: not claimed

### Packaging

- `Huge-v2.0.13-Setup.exe` / portable ZIP + SHA-256
- Do not publish without approval

## 2.0.12 — Syncplay-based playback authority

### Syncplay methods adapted (Apache 2.0)

- `engine/sync_authority.py`: `IgnoringOnTheFly`, dual-threshold pause/seek detection, latency-adjusted position, soft hard-seek gate — adapted from Syncplay `client.py` / `protocols.py` / `constants.py` (see `THIRD_PARTY_NOTICES.md`, `syncplay-ref/LICENSE`).
- Wired into `engine/session.py` monitor + play/pause publish/receive paths for PotPlayer Mini64 (status-aware `10014`, not Syncplay’s player backends).

### Root causes fixed

- **START MOVIE** left `_global_paused` / `_player_paused` stuck True, so dual-threshold never saw a real Pause; a false “Play” publish armed ignore-fly and forced Play over PotPlayer Pause.
- `ignoringOnTheFly` was incorrectly **forcing** PotPlayer back to the last expect on local samples (Syncplay only ignores *remote* stale state).
- Host ignore window rejected **guest Pause** proposals right after a local Play (race with `play` → joiner `pause`).
- Seek echo guards matched *any* recent target, so −30s back to a prior stacked position was dropped as an “echo”.
- Interleaved guest Huge skips used lagging absolute bases and could rewind the host.

### Fix

- Align global/player paused + position on successful `_run_start_go`; clear ignore-fly after start.
- Remove force-play ignore-fly protect on host/monitor; clear ignore when sample matches intent.
- Guest proposals and host-sequenced relays always apply; they clear the local ignore window.
- Skip publishing no-op pause changes that already match `_last_broadcast_playing`.
- Echo guards compare only the *latest* seek target; host applies Huge relative skips as `host_pos + delta`.

### Testing (FakePot + real TCP; not claimed as real PotPlayer multi-instance)

- Pause-hold: 30/30 host+1/+2 (Huge + PotPlayer-origin), 10s hold
- Joiner-pause-after-host-play race: 20/20
- Full `_test_multi_lobby.py`: 52/52 (START MOVIE, play/pause, rapid, skips, scrub, idle 30s, disconnect)
- Mixed ±5/±30 skips ×50 + interleaved ×20 per lobby config (spread ~70–100ms)
- Authority unit tests + stale play-command race

### Packaging

- `Huge-v2.0.12-Setup.exe`
- `Huge-v2.0.12-windows-portable.zip`
- Do not publish without approval

## 2.0.11 — Pause must stay paused (no auto-resume)

### Root cause

- A `_last_broadcast_playing` “sync” guard treated every PotPlayer state that disagreed with the last broadcast as lag and **forced that broadcast back** — so a deliberate Pause while the lobby still thought PLAYING immediately re-applied Play.
- `_mark_remote_play_echo` / `_publish_play_state` also set `_last_pot_state` optimistically ahead of PotPlayer, which created false Play transitions from lagging samples.

### Fix

- Stop inventing `_last_pot_state` on publish/echo; only update it from confirmed `_apply_play_state` or observed PotPlayer status.
- Track `_echo_play_confirmed`: until the commanded state is confirmed, opposite samples are lag (re-apply expect, do not publish). After confirmation, opposite samples are genuine user Play/Pause.
- Status-aware `10014` toggles unchanged — still inspect PotPlayer before toggling.

### Testing

- `_test_pause_hold.py`: 30× host+1/+2 Huge and PotPlayer-origin pauses, 10s hold, explicit resume — FakePot + real TCP
- Full `_test_multi_lobby.py` regression after the fix
- Real PotPlayer Mini64 multi-instance: not claimed in automated suite

### Packaging

- `Huge-v2.0.11-Setup.exe`
- `Huge-v2.0.11-windows-portable.zip`
- Do not publish without approval

## 2.0.10 — Dashboard UI, Live Log push, avatars, update banner

### UI

- **Export Sync Logs** moved from the sidebar to **Settings → Troubleshooting** (ZIP export unchanged)
- Top-of-app **update banner** when a newer stable GitHub release is available; **Later** dismisses until a newer version appears
- **Settings** (gear) holds Updates + Troubleshooting; sidebar no longer shows export or update controls

### Fixes

- Profile avatars: save **square** crops and clip with CSS (fixes “top half only” with pre-masked circular PNGs + `object-fit: cover`)
- Live Log: Python **pushes** state to React via `engine.subscribe` → `evaluate_js` (no 400 ms-only delay)
- Live Log: chronological order, stick-to-bottom scroll, dedupe within 750 ms, stable entry ids
- Play/pause log lines use actor field only; **(Huge)** / **(PotPlayer)** suffixes where origin is known
- Guest pause sync race: monitor no longer treats lagging PLAYING samples as new local Play after an authoritative Pause (`_last_broadcast_playing` guard on host and clients)

### Packaging

- `Huge-v2.0.10-Setup.exe`
- `Huge-v2.0.10-windows-portable.zip`
- Do not publish without approval

## 2.0.9 — Local multiplayer sync hardening (FakePot harness)

### Fixes

- Guest play/pause proposals no longer invent host `lobby_seq`/`play_seq` (joiners were stuck paused)
- `room_state` no longer forces PLAYING after start (raced guest-originated pauses)
- Start-grace ignores only PotPlayer pause echoes — Huge-button pauses from guests still sync
- Guest commands, monitor play transitions, and local play/pause serialize on the UI queue
- Host relays guest play/pause authoritatively to all peers; matching remote echoes are ignored
- `PlaybackCommand.from_wire` no longer treats start-generation `seq` as `lobby_seq`

### Testing

- Automated multi-process lobby suite (`_test_multi_lobby.py`) over real TCP on `127.0.0.1:24816` with independent FakePot per peer
- host+1 and host+2: START zero, play/pause from every client, rapid toggles, skips, scrub, 5‑minute idle, disconnect — **52/52** on `--long`
- Real PotPlayer Mini64 multi-instance: **not** independently targetable via Huge’s single-hwnd Win32 path (processes collapse / same hwnd)

### Packaging

- `Huge-v2.0.9-Setup.exe`
- `Huge-v2.0.9-windows-portable.zip`
- Do not publish without approval

## 2.0.8 — Playback command bus (multiplayer test build)

### Architecture

- New `engine/playback.py` **PlaybackCommandBus**: one ordered lobby sequence, cmd_id dedupe, suppress policy that never shortens seek protect
- Play/pause/seek/start flow through the bus; programmatic applies are not rebroadcast as user actions
- START MOVIE is two-phase: **prepare** (pause + seek `00:00:00` + ack barrier) → **go** (coordinated play)
- Host does not mark a successful start if joiners fail the zero-reset barrier
- **Export Sync Logs** button saves a Desktop zip for multiplayer debugging
- Friend guide: `docs/MULTIPLAYER_TEST_GUIDE.md`

### Packaging

- `Huge-v2.0.8-Setup.exe`
- `Huge-v2.0.8-windows-portable.zip`
- Test build — do not publish until multiplayer logs confirm pass

## 2.0.7 — Reliable play/pause sync + START MOVIE from absolute zero

### Fixes

- **Rapid pause/resume desync:** A 900ms suppress window updated `_last_pot_state` without broadcasting, so a quick PotPlayer resume after pause was swallowed and the other player stayed paused
- Play/pause now uses a monotonic `play_seq` so older PAUSED/PLAYING cannot override a newer command
- Remote play/pause is not echoed back to the originating guest; echo guard only ignores the matching state, not the opposite toggle
- Guest Huge Play/Pause sends the intended `set_playing` state (no host-side blind toggle)
- **START MOVIE:** every start forces pause → seek absolute `00:00:00` → verify → play on host and clients
- Client `start_ack` barrier; host reports waiting/failure instead of pretending everyone started
- Skips and snapshot positions cannot interrupt PHASE_STARTING
- Live Log records real start / play / pause actions with sequence tracing in `playback_trace.log`

### Packaging

- `Huge-v2.0.7-Setup.exe`
- `Huge-v2.0.7-windows-portable.zip`

## 2.0.6 — Multiplayer seek loop fix

### Fixes

- **Critical:** With two users in a lobby, natural playback / PotPlayer keyframe settle was misread as guest seeks, rewinding the movie every ~2s and spamming Live Log (`fawaz seeked to …`)
- Ordinary position progress is matched against wall-clock playback and never becomes a seek command
- Only intentional ±5s/±30s skips and real timeline scrubs create seek events
- Remote/join sync seeks are programmatic; receiving a remote seek cannot echo back as a new local seek
- Host drops guest “detected” seeks that bounce a position Huge just broadcast
- Expected-position timeout adopts the live PotPlayer position (fixes last-pos-ahead → false rewind)

### Packaging

- `Huge-v2.0.6-Setup.exe`
- `Huge-v2.0.6-windows-portable.zip`

## 2.0.5 — Select Movie load fix + authoritative START MOVIE

### Fixes

- **Select Movie regression:** `/add` only appended to the playlist and left PotPlayer empty (`00:00:00 / 00:00:00`). Restored `/current` so the file actually loads
- After load, pause immediately with Play/Pause toggle `10014` (separate Play/Pause IDs do nothing on PotPlayer Mini64), then seek to `00:00`
- Huge only marks the movie loaded when PotPlayer reports a real duration while paused
- START MOVIE: sequenced pause → seek 0 → play; stale PAUSED snapshots cannot undo it
- Playback phases + command sequence numbers; trace log at `%APPDATA%\HugeWatchParty\playback_trace.log`
- Updates panel layout matches other dashboard cards

### Packaging

- `Huge-v2.0.5-Setup.exe`
- `Huge-v2.0.5-windows-portable.zip`

## 2.0.4 — Movie start/stop race fix

### Fixes

- Selecting a movie no longer briefly starts shared playback for the whole lobby
- START MOVIE no longer plays for a moment and then pauses both users
- Root cause: PotPlayer autoplay / lagging status was broadcast as play then pause; room_state echoes could force PAUSED right after start
- Play/pause is not networked until START MOVIE; snapshots use commanded status; short grace ignores stale pause detections after start

### Updates

- Clearer update failure messages and safe retry (install left unchanged on failure)
- Same automatic updater as v2.0.3 — publish this release on Huge-Download and existing 2.0.3 installs can Update Now

### Packaging

- `Huge-v2.0.4-Setup.exe`
- `Huge-v2.0.4-windows-portable.zip`

## 2.0.3 — Typing, crop editors, automatic updates

### Fixes

- Username typing is instantaneous (local input + debounced profile save; no full UI refresh per keystroke)
- Banner crop editor: accurate selection outline, zoom-around-center, drag, and saved crop matching the preview
- Profile picture crop editor: accurate circular selection, zoom/drag, and saved avatar matching the preview
- Crop editor no longer darkens the selected image area (original colors preserved)
- Join Lobby `parse_host` binding fix

### Automatic updates

- Checks `https://github.com/vsnq0/Huge-Download/releases/latest` in the background on startup
- Settings panel: Check for Updates, Update Now / Later, download progress, cancel
- Downloads only the official Setup asset over HTTPS from the public Huge-Download repo
- Verifies GitHub asset SHA-256 digest when present; never claims verification if missing
- Installer supports `/update` for silent upgrade + relaunch; profile data under `%APPDATA%\HugeWatchParty` is preserved

### Packaging

- `Huge-v2.0.3-Setup.exe`
- `Huge-v2.0.3-windows-portable.zip`
- Complete ONEDIR runtime (no Python install required)

## 2.0.2 — Movie load stability + reliable skips

### App fixes (included in this package)

- PotPlayer no longer freezes during movie selection (monitor Win32 traffic paused while loading)
- Selected movie stays paused until Start Movie
- Reliable serialized skip queue for Huge ±5s / ±30s buttons so rapid skips are not lost

### Packaging

- `Huge-v2.0.2-Setup.exe` — complete ONEDIR installer (`Huge.exe` + `_internal\` including `python313.dll`)
- `Huge-v2.0.2-windows-portable.zip` — complete portable folder
- Official Huge logo on EXE, installer, Desktop/Start Menu shortcuts, and taskbar
- Previous local `release\` output is archived under `release-archive\` (does not overwrite older GitHub tags)

## 2.0.1 — Packaging fix (clean Windows PCs)

### Packaging only (no app/UI/PotPlayer/Tailscale/sync changes)

- Reliable PyInstaller **ONEDIR** bundle: `Huge.exe` + complete `_internal\` (includes `python313.dll` and VC runtime DLLs)
- Installer embeds and installs the **full** application folder (never a lone EXE)
- Setup refuses to finish if `_internal\python313.dll` is missing after extract
- Versioned release artifacts:
  - `Huge-v2.0.1-Setup.exe`
  - `Huge-v2.0.1-windows-portable.zip` (complete folder)
- Previous `release\` output is archived under `release-archive\` so v2.0.0 is not overwritten locally

### Why friends saw “Failed to load Python DLL … python313.dll”

On a developer PC with Python installed, Windows can still find `python313.dll` even if `_internal` is missing. On a clean PC there is no fallback — the complete onedir folder (or Setup) is required.

## 2.0.0 — Final Windows package

### Packaging

- Standalone `Huge.exe` (no Python / Node / Cursor required for end users)
- `Huge Setup.exe` installer
- Desktop and Start Menu shortcuts named **Huge**
- Official Huge logo used for EXE, installer, and shortcuts
- No console / CMD window

### App (already working in this release)

- Figma UI over the Huge watch-party engine
- PotPlayer control and lobby sync
- Tailscale / LAN lobby address support
- Discord-style banner and profile picture editors (original colors, crop/zoom)
- Live Log shows each username once and exact skip actions (±5s / ±30s)
- Profile persistence under `%APPDATA%\HugeWatchParty\`

### Developer docs

- `README.md`
- `BUILD.md`
- `HOW_TO_UPDATE.md`
- `.gitignore`
