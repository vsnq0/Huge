# Huge v2.0.8 — Multiplayer Test Guide

**You do not need to code.** Install the test build, follow the checklist, and send the zip files from each PC.

This is a **test build**. Do not treat it as the final published release until the host says so.

---

## Before you start

1. Install **Huge-v2.0.8-Setup.exe** on every PC (host + joiners).
2. Use **Tailscale** so everyone can reach the host address.
3. Each person must have the **same movie file** on their own PC (MP4 or MKV). Huge does not stream the file.
4. Put PotPlayer Mini64 path in Huge settings if asked.

---

## How to send logs (every PC, after each important test)

1. In Huge, open the side panel and click **Export Sync Logs**.
2. A zip appears on that PC’s **Desktop**, named like:
   `Huge-SyncLog-<username>-YYYYMMDD-HHMMSS.zip`
3. Send that zip (Discord/email/Drive) and **say which test** it was (example: “Test A3 — START MOVIE, host+2 joiners”).

Every participant should export after the scenario — host and each joiner.

---

## Lobby setup (every session)

| Who | What to do |
|-----|------------|
| Host | Create lobby → copy Tailscale address |
| Joiners | Join with that address |
| Everyone | Select the same movie → wait until Huge says loaded / paused |
| Everyone | Press **Ready up** |
| Host | Press **START MOVIE** only when the checklist says so |

---

# Part A — Host + 1 joiner

Repeat the important START tests at least **10 times** in this mode.

### A1 — No autoplay before start
1. Both load the movie.
2. Do **not** press START MOVIE.
3. Wait **15 seconds**.
4. Pass if: both PotPlayers stay paused; no video advancing.
5. Export Sync Logs on both PCs. Label: `A1-no-autoplay`.

### A2 — START from zero (fresh)
1. Both Ready up.
2. Host presses START MOVIE.
3. Pass if: both jump to **00:00:00** and start playing from the beginning.
4. Export. Label: `A2-start-zero`.

### A3 — START from a non-zero position
1. After a previous start (or by seeking), leave one/both players paused somewhere mid-movie (e.g. 5 minutes).
2. Ready up again if needed; host presses START MOVIE.
3. Pass if: **both** reset to beginning and play from zero (not from the mid-movie spot).
4. Export. Label: `A3-start-from-mid`.

### A4 — Rapid Play/Pause (PotPlayer)
On the **joiner** PC, in PotPlayer:
1. Pause, then resume quickly (try ~100 ms, ~200 ms, ~500 ms).
2. Pass if: host’s PotPlayer ends in the **same** final state (playing or paused).
3. Repeat from the **host** PotPlayer.
4. Export both. Label: `A4-rapid-pp-pot`.

### A5 — Rapid Play/Pause (Huge button)
Same as A4 but use Huge’s Play/Pause button. Label: `A5-rapid-pp-huge`.

### A6 — Skips
From host and from joiner:
1. Single ±5s / ±30s in Huge (at least 20 rapid presses total).
2. Hold a PotPlayer skip key so it repeats.
3. Drag the PotPlayer timeline once.
4. Pass if: both players stay roughly in sync; no random rewinds; Live Log shows the real person.
5. Export. Label: `A6-skips`.

### A7 — Idle
Play for **2 minutes** without touching controls. Pass if: no spontaneous pause/rewind. Export: `A7-idle`.

### A8 — Reconnect
Joiner leaves and rejoins (or reconnects Tailscale). Re-test A5 briefly. Export: `A8-reconnect`.

---

# Part B — Host + 2 joiners

Repeat START tests at least **10 times** in this mode (20 total across A+B is the goal).

Same checklist as Part A, but with **two joiners**. Labels: `B1-…`, `B2-…`, etc.

Extra check on START MOVIE: **all three** players must reset to zero and play. If one fails, note their name and export **all three** logs.

---

# Part C — What to write when you send results

```
Config: host + 1  OR  host + 2
Test IDs: A2, A4, …
Pass / Fail for each
If Fail: what you saw (who stayed paused, wrong time, etc.)
Attached zips: list filenames
Movie format: MP4 / MKV
```

---

# What “done” means

Real multiplayer tests are only considered passed when these logs show:

1. No playback before START MOVIE.
2. Every START resets everyone to ~00:00:00 then plays.
3. Rapid pause/resume leaves everyone in the correct final state.
4. Skips stay ordered without false rewinds.
5. Host and joiners can both control playback.

The developer will analyze your zips, fix issues, send a new test build, and ask you to re-run the failed IDs only.
