"""The PotPlayer card no longer renders a keyframe warning."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ['HUGE_FAKE_POT'] = '1'
os.environ['HUGE_DISABLE_HOTKEYS'] = '1'

import engine.session as session  # noqa: E402
from engine.fake_pot import install_fake_pot  # noqa: E402

install_fake_pot(session)
from engine.session import HugeEngine  # noqa: E402


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def main():
    passed = ok('notice helper removed', not hasattr(session, 'pot_keyframe_notice'))
    passed &= ok('registry reader removed', not hasattr(session, 'read_play_use_keyframe'))
    eng = HugeEngine()
    ui = eng.ui_state()
    passed &= ok('state has no notice', 'pot_keyframe_notice' not in ui and 'pot_keyframe_status' not in ui)
    passed &= ok('path still present', 'potplayer_path' in ui and 'pot_ready' in ui)
    panel = open(os.path.join(ROOT, 'ui', 'src', 'App.tsx'), encoding='utf-8').read()
    passed &= ok('panel has no warning', 'pot-keyframe-notice' not in panel and 'pot_keyframe_notice' not in panel)
    passed &= ok('path and button remain', 'Select PotPlayer path' in panel and 'pot-path-block' in panel)
    try:
        eng.close()
    except Exception:
        pass
    print('RESULT', 'ok' if passed else 'failed')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
