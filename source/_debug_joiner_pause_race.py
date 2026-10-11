"""Stress: host play then immediate joiner Huge-pause (ignore_fly race)."""
import os, sys, time
sys.path.insert(0, '.')
import _test_multi_lobby as ml

work = os.path.join('test-results', 'pause-hold', 'race')
os.makedirs(work, exist_ok=True)
fails = 0
for i in range(20):
    host, clients = ml.fresh_peers(1, 41000 + i * 3, os.path.join(work, f'r{i}'))
    try:
        ml.bootstrap_playing_lobby(host, clients)
        j = clients[0]
        host.call({'cmd': 'play'})
        ml.wait_all([host, j], lambda sts: all(s['playing'] for s in sts), timeout=5)
        host.call({'cmd': 'play'})  # re-arm ignore like play_pause scenario
        t0 = time.time()
        j.call({'cmd': 'pause'})
        ok, sts = ml.wait_all([host, j], lambda s: all(not x['playing'] for x in s), timeout=5)
        lat = (time.time() - t0) * 1000
        if not ok:
            fails += 1
            print(f'FAIL {i} lat={lat:.0f} states={[(s["name"], s["playing"]) for s in sts]}')
        else:
            print(f'OK {i} lat={lat:.0f}ms')
    finally:
        ml.stop_peers(host, clients)
        time.sleep(0.2)
print(f'DONE fails={fails}/20')
raise SystemExit(1 if fails else 0)
