"""Exercise the actual runner's process signals and atomic private checkpoints."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('stop_signal',[signal.SIGINT,signal.SIGTERM,signal.SIGKILL])
def test_runner_interruption_and_resume(tmp_path, stop_signal):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    dummy=private/'input.json';dummy.write_text('{"grid":{}}')
    # This isolated synthetic fixture stubs source acquisition only. It runs
    # the real weather/fire host, RNG, checkpoint and runner in the child.
    shim=ROOT/'tests/test_expanded_exercise_v3.py'
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    common=['strict','water-v2','overlay-v3','overlay-v4','parent','catalog']
    extra=['road','coast','water','worldcover','osm-manifest','osm-samples','dera-rivers','strict-sha256',
           'water-v2-sha256','overlay-v3-sha256','overlay-v4-sha256','parent-sha256','catalog-sha256','road-sha256']
    def command(report,checkpoint,wall,resume=None):
        args=[sys.executable,str(shim),'weather_worker']
        for field in common:args.extend(['--'+field,str(dummy)])
        for field in extra:args.extend(['--'+field,'unused'])
        args.extend(['--scenario','nominal','--crossing-proxy','0.9','--output',str(report),
                     '--checkpoint-output',str(checkpoint),'--wall-seconds',str(wall),
                     '--checkpoint-every','1','--opt-in-weather-fire'])
        if resume:args.extend(['--resume',str(resume)])
        return args
    report=private/'interrupted.json';checkpoint=private/'periodic.json'
    child=subprocess.Popen(command(report,checkpoint,30),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        until=time.monotonic()+15
        while time.monotonic()<until:
            if child.poll() is not None:
                out,err=child.communicate();raise AssertionError('worker exited early: '+out+err)
            if checkpoint.exists():
                state=json.loads(checkpoint.read_text())
                if any((item['checkpoint'] or {}).get('warmup_done',0)
                       +(item['checkpoint'] or {}).get('minute',0)>=2 for item in state['cases']):break
            time.sleep(.02)
        else:raise AssertionError('no periodic checkpoint')
        os.kill(child.pid,stop_signal)
        out,err=child.communicate(timeout=15)
        if stop_signal==signal.SIGKILL:
            assert child.returncode==-signal.SIGKILL
            assert not report.exists()
        else:
            assert child.returncode==0,err
            assert out.strip()=='Private diagnostic finished.'
            result=json.loads(report.read_text())
            assert result['stop_signal']==stop_signal
            assert all(row['status']=='interrupted' for row in result['cases'])
        saved=json.loads(checkpoint.read_text())
        assert checkpoint.stat().st_mode & 0o777 == 0o600
        report2=private/'resumed.json';checkpoint2=private/'resumed-checkpoint.json'
        result=subprocess.run(command(report2,checkpoint2,.001,resume=checkpoint),cwd=ROOT,env=env,
                              capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        assert result.stdout.strip()=='Private diagnostic finished.'
        restored=json.loads(checkpoint2.read_text())
        assert restored['cases']==saved['cases']
    finally:
        if child.poll() is None:
            child.kill();child.wait()
