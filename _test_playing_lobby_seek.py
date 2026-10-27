"""One playing seek across a FakePot localhost host and joiner.

This is not a separate-physical-PC test. Position checks allow playback to
continue after the seek and for the position report to lag the command.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from _test_multi_lobby import bootstrap_playing_lobby, fresh_peers, stop_peers  # noqa: E402


def rows(peer, reason, since=0.0):
    trace = peer.call({'cmd': 'trace', 'n': 120}).get('trace') or []
    return [
        r for r in trace
        if r.get('reason') == reason and float(r.get('wall_s') or 0) >= since
    ]


def sets_since(peer, since):
    cmds = peer.call({'cmd': 'pot_cmds'}).get('cmds') or []
    return [
        c for c in cmds
        if c.get('op') == 'set_current' and float(c.get('t') or 0) >= since
    ]


def main():
    fails = []
    work = tempfile.mkdtemp(prefix='huge_playing_seek_')
    host, clients = fresh_peers(1, 39620, work)
    joiner = clients[0]
    try:
        bootstrap_playing_lobby(host, clients)
        time.sleep(0.4)
        mark = time.time()
        before = {
            p.name: int(p.call({'cmd': 'state'})['state']['pot_position'])
            for p in (host, joiner)
        }
        print('FAKEPOT_LOCALHOST before', before)
        host.call({'cmd': 'skip', 'delta': 5000})
        sent = None
        deadline = time.time() + 3
        while time.time() < deadline:
            found = rows(host, 'skip_send', mark - 0.05)
            if found:
                sent = found[-1]
                break
            time.sleep(0.02)
        if not sent:
            fails.append('host did not log skip_send')
            print('FAIL no skip_send')
        else:
            cmd_id = str(sent.get('cmd_id') or '')
            target = int(sent.get('target'))
            sends = [r for r in rows(host, 'skip_send', mark - 0.05) if str(r.get('cmd_id') or '') == cmd_id]
            dones = [
                r for r in rows(host, 'skip_local_done', mark - 0.05)
                if int(r.get('target') or -1) == target
            ]
            print(
                f"HOST skip_send cmd_id={cmd_id} target={target} "
                f"sends={len(sends)} local_done={len(dones)} playing={sent.get('playing')}"
            )
            if len(sends) != 1 or len(dones) != 1:
                fails.append(f'host logical seek count sends={len(sends)} dones={len(dones)}')
            # Playback keeps moving. Compare the reported position with the
            # commanded target plus the time since that peer applied the seek.
            time.sleep(0.35)
            observations = []
            for peer in (host, joiner):
                applied = sets_since(peer, mark - 0.05)
                state = peer.call({'cmd': 'state'})['state']
                pos = int(state['pot_position'])
                now = time.time()
                same_target = [c for c in applied if int(c.get('value')) == target]
                other = [c for c in applied if int(c.get('value')) != target]
                applied_at = float(same_target[-1]['t']) if same_target else now
                expected = target + int(round((now - applied_at) * 1000))
                error = pos - expected
                observations.append({
                    'name': peer.name,
                    'pos': pos,
                    'playing': state['playing'],
                    'sets': [(c.get('value'), round(c.get('t') - mark, 3)) for c in applied],
                    'error_vs_target_plus_playback_ms': error,
                })
                print(
                    f"OBS {peer.name} pos={pos} playing={state['playing']} "
                    f"sets={observations[-1]['sets']} "
                    f"error_vs_target_plus_playback_ms={error}"
                )
                if not state['playing']:
                    fails.append(f'{peer.name} left playback')
                if len(same_target) != 1 or other:
                    fails.append(f'{peer.name} seek commands {observations[-1]["sets"]} wanted only {target}')
                if abs(error) > 400:
                    fails.append(f'{peer.name} playback-adjusted error {error}')
            # After the command window, the monitor must not send another seek.
            time.sleep(2.6)
            for peer in (host, joiner):
                applied = sets_since(peer, mark - 0.05)
                values = [int(c.get('value')) for c in applied]
                print(f'MONITOR {peer.name} set_current_values={values}')
                if values != [target]:
                    fails.append(f'{peer.name} extra seek after command {values}')

        # A failed send retries the same published target and does not broadcast again.
        retry_mark = time.time()
        host.call({'cmd': 'fail_next_sets', 'n': 2})
        host.call({'cmd': 'skip', 'delta': 5000})
        time.sleep(1.2)
        retry_sends = rows(host, 'skip_send', retry_mark - 0.05)
        retry_ids = {str(r.get('cmd_id') or '') for r in retry_sends}
        retry_targets = {int(r.get('target')) for r in retry_sends if r.get('target') is not None}
        retry_sets = sets_since(host, retry_mark - 0.05)
        print(
            f'RETRY sends={len(retry_sends)} cmd_ids={retry_ids} targets={retry_targets} '
            f'set_current={[c.get("value") for c in retry_sets]}'
        )
        if len(retry_sends) != 1 or len(retry_ids) != 1:
            fails.append(f'retry created extra logical seeks {len(retry_sends)} {retry_ids}')
        if len(retry_targets) != 1:
            fails.append(f'retry changed target {retry_targets}')
        wanted = next(iter(retry_targets)) if retry_targets else None
        if wanted is None or [int(c.get('value')) for c in retry_sets] != [wanted]:
            fails.append(f'retry set_current {[c.get("value") for c in retry_sets]} wanted {[wanted]}')
        joiner_sets = [int(c.get('value')) for c in sets_since(joiner, retry_mark - 0.05)]
        print(f'RETRY joiner set_current={joiner_sets}')
        if joiner_sets != [wanted]:
            fails.append(f'retry joiner targets {joiner_sets} wanted {[wanted]}')
    finally:
        stop_peers(host, clients)
    print('FAILS', len(fails))
    for item in fails:
        print('FAIL', item)
    print('LABEL FakePot localhost host+joiner. Not a physical multi-PC result.')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
