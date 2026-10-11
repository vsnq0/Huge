"""Single-shot debug: pot_pause from joiner after start."""
import os, sys, time
sys.path.insert(0, '.')
import _test_multi_lobby as ml

work = os.path.join('test-results', 'pause-hold', 'debug')
os.makedirs(work, exist_ok=True)
host, clients = ml.fresh_peers(1, 39550, work)
try:
    ml.bootstrap_playing_lobby(host, clients)
    j = clients[0]
    print('pre', host.call({'cmd':'state'})['state']['playing'], j.call({'cmd':'state'})['state']['playing'])
    r = j.call({'cmd':'pot_pause'})
    print('pot_pause return', r.get('ok'), 'playing', r.get('state',{}).get('playing'))
    for i in range(20):
        time.sleep(0.25)
        hs = host.call({'cmd':'state'})['state']
        js = j.call({'cmd':'state'})['state']
        print(f'{i} host={hs["playing"]} joiner={js["playing"]} phase={js["phase"]}')
        if not hs['playing'] and not js['playing']:
            print('SYNCED')
            break
finally:
    ml.stop_peers(host, clients)
