# Third-party notices

## Syncplay (Apache License 2.0)

Portions of Huge's playback authority logic in `engine/sync_authority.py` (and
call sites in `engine/session.py`) are **adapted** from
[Syncplay](https://github.com/Syncplay/syncplay)
(`syncplay/client.py`, `syncplay/protocols.py`, `syncplay/constants.py`).

Copyright notices from the Syncplay project apply to those adapted ideas and
any derived fragments. See `syncplay-ref/LICENSE` (Apache License 2.0) for the
full license text. A local reference checkout may live under `syncplay-ref/`.

Huge does **not** embed the Syncplay application, UI, or player backends.
Only the validated synchronization *methods* (ignoring-on-the-fly counters,
dual-threshold seek detection, pause-change detection, and latency-adjusted
positions) were adapted for PotPlayer Mini64 and Huge's host-authoritative lobby.

Modifications for Huge: millisecond positions, host-authoritative lobby
sequencing, and PotPlayer Mini64 status-aware play/pause (Win32 `10014`).
