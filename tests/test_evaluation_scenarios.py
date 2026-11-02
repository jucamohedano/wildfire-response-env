import copy
import json
import os
from collections import Counter
from pathlib import Path
import pytest
from wildfire_core.incident_day import IncidentDayEnv
from wildfire_core.mobility_routing import RoadNetwork
from wildfire_core.catalog_lifecycle import registry, select_day_case_id
from envs.wildfire_response.incident_day.scenarios import EVALUATION_CASE_IDS, REAL_MAP_DEVELOPMENT_CASE_IDS, build_scenario
from envs.wildfire_response.incident_day.evaluation_scenarios import GENERATION_SEED
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN

@pytest.fixture(scope='module')
def source():
    root=Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME','/var/tmp/wildfire-packaged-completion-data-v10'))/'runtime/es-ma-2021'
    if not (root/'inputs.json').exists(): pytest.skip('Pinned prepared source inputs required')
    inputs=json.loads((root/'inputs.json').read_text()); document=json.loads((root/'network.json').read_text())
    return inputs, document, RoadNetwork(document,NETWORK_PIN)


def test_catalog_is_eval_only_and_distinct():
    cat=next(c for c in registry()['catalogs'] if c['id']=='incident_day_evaluation_v1')
    assert cat['status']=='active_evaluation' and cat['split']=='evaluation'
    assert cat['generation_seed']==GENERATION_SEED==20261102
    assert len(cat['case_ids'])==24 and len(set(cat['case_ids']))==24
    assert not set(cat['case_ids'])&set(REAL_MAP_DEVELOPMENT_CASE_IDS)
    for sid in EVALUATION_CASE_IDS:
        assert select_day_case_id(sid,PACK_PIN,NETWORK_PIN)==sid
        with pytest.raises(ValueError): select_day_case_id(sid,'0'*64,'1'*64)


def test_all_configurations_replay_and_preserve_parent(source):
    inputs,doc,net=source
    original=copy.deepcopy(inputs); hashes=set(); families=Counter(); configs={}
    for sid in EVALUATION_CASE_IDS:
        case=build_scenario(inputs,net,sid)
        assert case==build_scenario(inputs,net,sid)
        meta=case['evaluation']; parent=build_scenario(inputs,net,meta['parent_id'])
        assert case['parent_scenario_sha256']==parent['sha256']
        assert case['events']==parent['events'] and case['initial_fire']==parent['initial_fire']
        assert case['seed']==parent['seed'] and not meta['training_eligible']
        assert case['profile']['version']==parent['profile']['version']
        assert sum(g['count'] for g in case['groups'])<=min(d['capacity'] for d in case['destinations'])
        for u in case['resources']:
            assert 0<=u['water_litres']<=u['capacity_litres']
        hashes.add(case['sha256']);families[case['family']]+=1
        configs.setdefault(case['family'],set()).add(meta['configuration']['name'])
    assert len(hashes)==24 and len(families)==6 and set(families.values())=={4}
    assert all(len(v)==4 for v in configs.values())
    assert inputs==original


def test_actor_never_receives_generation_metadata(source):
    inputs,doc,_=source
    env=IncidentDayEnv(inputs,doc,expected_inputs_sha256=PACK_PIN,expected_network_sha256=NETWORK_PIN,allow_unapproved_data=True)
    obs,_=env.reset(EVALUATION_CASE_IDS[0])
    text=json.dumps(obs)
    for key in ('generation_seed','configuration_group','parent_scenario_sha256','water_fraction','future_events'):
        assert key not in text
