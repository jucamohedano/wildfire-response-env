"""Basin assumptions require positive, bounded, source-backed geometry."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_sensitivity_basin_screen():
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT/'eval'),str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    proc=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert proc.returncode==0,proc.stdout+proc.stderr


def run():
    import pytest
    from shapely.geometry import box
    from eval.mixed_frontier_sensitivity_v1 import check_basin
    cell=box(0,0,500,500); basin=box(10,10,30,30); tags={'landuse':'basin'}
    assert check_basin(cell,[(basin,tags)],[])==pytest.approx(.0016)
    for polygons,dera in [([],[]), ([(basin,{'natural':'water'})],[]),
                           ([(box(0,10,20,30),tags)],[]), ([(box(1,1,200,200),tags)],[]),
                           ([(basin,tags),(basin,tags)],[]), ([(basin,tags)],[box(20,20,25,25)])]:
        with pytest.raises(ValueError):check_basin(cell,polygons,dera)


if __name__=='__main__':run()
