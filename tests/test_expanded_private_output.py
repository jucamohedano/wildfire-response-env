"""Private diagnostics must not write inside this or any other Git worktree."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('expanded_private_output',ROOT/'envs/wildfire_expanded_incident_v1/private_output.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_other_git_worktree_is_rejected_before_writing(tmp_path):
    primary=tmp_path/'primary';primary.mkdir()
    subprocess.run(['git','init','-q',str(primary)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(primary),'-c','user.name=Test','-c','user.email=test@example.invalid',
                    'commit','--allow-empty','-qm','fixture'],check=True,capture_output=True)
    other=tmp_path/'other'
    subprocess.run(['git','-C',str(primary),'worktree','add','--detach',str(other)],check=True,capture_output=True)
    private=other/'private';private.mkdir(mode=0o700)
    output=private/'report.json'
    with pytest.raises(ValueError,match='git worktree'):
        MODULE.write_private_json(output,{'private':'fixture'})
    manifest=private/'manifest.json';manifest.write_text('{}');manifest.chmod(0o600)
    with pytest.raises(ValueError,match='git worktree'):
        MODULE.read_private_json(manifest)
    assert not output.exists()
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    common=['strict','water-v2','parent','catalog','road','coast','water','worldcover','osm-manifest',
            'osm-samples','strict-sha256','water-v2-sha256','parent-sha256','catalog-sha256','road-sha256',
            'scenario','crossing-proxy']
    for module,extra in [('run_exercise_v3',['overlay','overlay-sha256']),
                         ('run_exercise_v4',['overlay-v3','overlay-v4','dera-rivers',
                                             'overlay-v3-sha256','overlay-v4-sha256']),
                         ('run_weather_v4',['overlay-v3','overlay-v4','dera-rivers',
                                            'overlay-v3-sha256','overlay-v4-sha256','checkpoint-output'])]:
        args=[sys.executable,'-m','wildfire_expanded_incident_v1.'+module]
        for key in common+extra:args.extend(['--'+key,'unused'])
        args.extend(['--output',str(output),
                     '--opt-in-weather-fire' if module=='run_weather_v4' else '--opt-in-mixed-experiment'])
        result=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True)
        assert result.returncode!=0 and 'git worktree' in result.stderr
        assert not output.exists()


def test_output_requires_private_directory_and_fresh_file(tmp_path):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    output=private/'report.json'
    MODULE.write_private_json(output,{'safe':True})
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError,match='fresh'):
        MODULE.write_private_json(output,{'safe':False})
    public=tmp_path/'public';public.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='mode 0700'):
        MODULE.private_destination(public/'report.json')


def test_atomic_checkpoint_refuses_a_replaced_target(tmp_path):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    target=private/'checkpoint.json'
    token=MODULE.write_atomic_private_json(target,{'step':1})
    token=MODULE.write_atomic_private_json(target,{'step':2},token)
    assert __import__('json').loads(target.read_text())=={'step':2}
    replacement=private/'replacement';replacement.write_text('keep');replacement.chmod(0o600)
    replacement.replace(target)
    with pytest.raises(ValueError,match='identity changed'):
        MODULE.write_atomic_private_json(target,{'step':3},token)
    assert target.read_text()=='keep'


def test_private_json_rejects_links_and_duplicate_keys(tmp_path):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    manifest=private/'manifest.json';manifest.write_text('{"a":1}');manifest.chmod(0o600)
    assert MODULE.read_private_json(manifest)=={'a':1}
    link=private/'link';link.symlink_to(manifest)
    with pytest.raises(ValueError):MODULE.read_private_json(link)
    manifest.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError,match='duplicate'):MODULE.read_private_json(manifest)
