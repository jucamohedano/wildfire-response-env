"""The screening CLI writes results privately and prints no result counts."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_screen_stdout_contains_only_generic_completion(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    source = private / 'input.json'
    source.write_text(json.dumps({'grid': {}, 'sha256': 'p', 'inputs_sha256': 'unused', 'network_sha256': 'n'}))
    manifest = private / 'manifest.json'
    manifest.write_text('{}')
    manifest.chmod(0o600)
    output = private / 'report.json'
    script = '''
from wildfire_expanded_incident_v1 import screen_mixed_v2 as screen
screen._checked_document = lambda value, expected, version: value
screen.build_drafts = lambda *args: {'sha256':'synthetic', 'cases':[{'id':'synthetic', 'seed':1,
    'wind':[0,0], 'initial_fire':[[2,2]]}]}
screen.prepare_mixed_inputs = lambda *args, **kwargs: object()
class Fire:
    def __init__(self, *args, **kwargs): self.reasons=('unknown_fire_state',)
    def change(self, command): return False
screen.InteriorMixedFireV2 = Fire
screen.main()
'''
    common = ('strict','water-v2','audit','hypotheses','parent','catalog')
    pins = ('strict-sha256','water-v2-sha256','audit-sha256','hypotheses-sha256',
            'parent-sha256','catalog-sha256','road-sha256')
    args = [sys.executable, '-c', script]
    for name in common:
        args.extend(['--' + name, str(source)])
    for name in pins:
        args.extend(['--' + name, 'unused'])
    args.extend(['--road', 'unused', '--audit-trust-manifest', str(manifest),
                 '--output', str(output), '--opt-in-mixed-experiment'])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join((
        str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT))))
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'Private screening finished.'
    assert output.stat().st_mode & 0o777 == 0o600
    report = json.loads(output.read_text())
    assert all(report['scenarios'][tier][0]['status'] == 'censored' for tier in ('low', 'nominal', 'high'))
