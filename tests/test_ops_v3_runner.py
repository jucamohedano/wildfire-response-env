import copy
import gzip
import json
import pytest
from wildfire_core.incident_day import fixture_inputs
from wildfire_ops_v3 import IncidentDayEnvV3, observation_identity
from eval import evaluate_packaged_runtime as runner


@pytest.fixture
def tiny(monkeypatch):
    inputs, network=fixture_inputs()
    identifiers=['incident-day-dev-000']+[f'incident-day-evaluation-v1-{i:03}' for i in range(1,24)]
    entry=dict(inputs_sha256=inputs['sha256'],network_sha256=network['sha256'],scenario_ids=[],evaluation_scenario_ids=identifiers,allow_unapproved_data=False)
    catalog=dict(id=runner.CATALOG_ID,status='active_evaluation',case_ids=identifiers,inputs_sha256=inputs['sha256'],network_sha256=network['sha256'])
    manifest={'runtimes':{'fixture':entry}}
    monkeypatch.setattr(runner,'registry',lambda:{'catalogs':[catalog],'sha256':'fixture-lifecycle'})
    monkeypatch.setattr(runner,'verify_package',lambda path:copy.deepcopy(manifest))
    def make(*args,**kwargs):
        assert kwargs['observation_version']=='v3'
        return IncidentDayEnvV3(inputs,network,expected_inputs_sha256=inputs['sha256'],expected_network_sha256=network['sha256']),copy.deepcopy(entry)
    monkeypatch.setattr(runner,'make_environment',make)
    env,_=make(observation_version='v3');env.reset(identifiers[0])
    rows={('fixture',sid):dict(task_id=sid,runtime_id='fixture',episode_seed=env._scenario['seed'],scenario_sha256=env._scenario['sha256']) for sid in identifiers}
    monkeypatch.setattr(runner,'evaluation_rows',lambda *args:rows)


def saved(path):
    return json.loads(gzip.decompress((path/'private/checkpoint.json.gz').read_bytes()))


def test_v3_full_session_continuation_and_identity(tiny,tmp_path):
    a,b=tmp_path/'resumed',tmp_path/'direct'
    first=runner.run('fixture',a,runtime_id='fixture',max_commands=2,observation_version='v3')
    assert first['pins']['observation_sha256']==observation_identity()
    runner.run('fixture',a,runtime_id='fixture',max_commands=2,resume=True,observation_version='v3')
    runner.run('fixture',b,runtime_id='fixture',max_commands=4,observation_version='v3')
    x,y=saved(a),saved(b)
    for key in ('environment','policy','calls','movement_observer','resume_checks','query_independence'):
        assert x['live'][key]==y['live'][key]
    assert x['live']['query_independence']['passed']
    assert all(c['passed'] for c in x['live']['resume_checks'])
    assert runner.checkpoint_state(x['live']['environment'])['minute']>0
    with pytest.raises(ValueError,match='pins'):
        runner.run('fixture',a,runtime_id='fixture',max_commands=1,resume=True,observation_version='v2')
    with pytest.raises(ValueError,match='observation identity'):
        runner.run('fixture',tmp_path/'wrong',runtime_id='fixture',max_commands=1,observation_version='v3',expected_observation_sha256='0'*64)
