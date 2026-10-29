import copy

from wildfire_core.catalog_lifecycle import registry
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from eval.incident_day_policies import POLICY_NAMES
from eval.validate_incident_day_runtime import (PublicFacade, cell_identity, commit,
    duration_statistics, expected_cells, validate_matrix, _continuation, _query_independence,
    REQUIRED_OPERATIONS, _checkpoint, load_progress, validator_digest, checksum, aggregate_findings)


def manifest():
    catalogs=[c for c in registry()['catalogs'] if c['status']=='active_development']
    return {'runtimes':{('es-ma-2021' if 'realmap' in c['id'] else 'fixture'):
        dict(inputs_sha256=c['inputs_sha256'],network_sha256=c['network_sha256'],scenario_ids=c['case_ids'])
        for c in catalogs}}


def report():
    expected=expected_cells(manifest())
    return dict(expected_cells=expected,runtime_findings=[],cells=[dict(c,status='complete',minute=1440,
        verification=dict(passed=True,exact_replay=True,independent_reward_agreement=True),
        resume_checks=[dict(passed=True)],query_independence=dict(passed=True),
        operation_coverage={name:dict(observed=1) for name in REQUIRED_OPERATIONS}) for c in expected])


def environment():
    p,n=fixture_inputs()
    env=IncidentDayEnv(p,n,expected_inputs_sha256=p['sha256'],expected_network_sha256=n['sha256'])
    env.reset()
    return env


def test_expected_domain_is_all_active_cases_cross_all_required_policies():
    expected=expected_cells(manifest())
    assert len(expected)==48 and len(set(map(cell_identity,expected)))==48
    assert {c['policy'] for c in expected}==set(POLICY_NAMES)
    bad=manifest();bad['runtimes'].pop('fixture')
    import pytest
    with pytest.raises(ValueError,match='Both active'):expected_cells(bad)
    with pytest.raises(ValueError,match='shrink'):expected_cells(manifest(),('passive',))
    aliased=manifest();aliased['runtimes']['alias']=copy.deepcopy(aliased['runtimes']['fixture'])
    aliased['runtimes'].pop('es-ma-2021')
    with pytest.raises(ValueError,match='Duplicate'):expected_cells(aliased)


def test_matrix_cannot_pass_missing_duplicate_incomplete_or_false_verified_cells():
    assert validate_matrix(report())=='complete'
    for mutate in [lambda r:r['cells'].pop(),lambda r:r['cells'].append(copy.deepcopy(r['cells'][0])),
                   lambda r:r['cells'][0].update(status='not_run'),
                   lambda r:r['cells'][0]['verification'].update(exact_replay=False),
                   lambda r:r['cells'][0].update(minute=1439),
                   lambda r:r['cells'][0].update(resume_checks=[]),
                   lambda r:r['cells'][0].update(query_independence={'passed':False}),
                   lambda r:r['cells'][0].update(findings=[{'category':'contradiction'}]),
                   lambda r:r['runtime_findings'].append({'category':'blocking'})]:
        value=report();mutate(value);assert validate_matrix(value)!='complete'
    value=report();value['expected_cells']=value['expected_cells'][:12]
    value['cells']=value['cells'][:12];assert validate_matrix(value)=='failed'
    value=report()
    for c in value['cells']:c['operation_coverage']['releases']['observed']=0
    assert validate_matrix(value)=='blocked'


def test_named_facade_records_all_clock_semantics_without_private_observations():
    env=environment();calls=[];public=PublicFacade(env,calls)
    public.get_situation();public.get_reports(limit=20);public.check_action({})
    result=commit(public,{'status_requests':[{'center_id':'center-0','subject':'situation'}]})
    assert result['observation']['minute']==1
    result=commit(public,{'advance_minutes':20})
    assert 1<result['observation']['minute']<=21
    result=commit(public,{'releases':['engine-0']})
    assert result['info']['error'] is None
    assert all('observation' not in call['result'] for call in calls)
    assert not hasattr(public,'_scenario') and not hasattr(public,'ops')
    assert calls[0]['before_minute']==calls[0]['after_minute']==0


def test_continuation_and_extra_queries_leave_live_episode_untouched():
    from eval.incident_day_policies import make_policy
    env=environment();policy=make_policy('dispatcher');before=env.checkpoint()
    prediction=_continuation(env,policy)
    assert prediction['action']
    assert env.checkpoint()==before
    assert _query_independence(env)['passed']
    assert env.checkpoint()==before


def test_duration_statistics_exclude_inherited_and_unfinished_spans():
    rows=[dict(operation='travel',minutes=3),dict(operation='travel',minutes=7),
          dict(operation='travel',minutes=100,inherited=True),dict(operation='travel',minutes=50,censored=True)]
    assert duration_statistics(rows)=={'travel':dict(count=2,min_minutes=3,median_minutes=5.0,max_minutes=7)}


def test_atomic_bundle_recovers_without_public_report_and_rejects_tampering(tmp_path):
    import gzip,json,pytest
    from envs.wildfire_response.incident_day.environment import implementation_digest
    from eval.incident_day_movement_checks import MovementObserver
    from eval.incident_day_policies import make_policy
    env=environment();expected=expected_cells(manifest())
    index=next(i for i,c in enumerate(expected) if c['case_id']==env._scenario['id'] and c['policy']=='passive')
    value=report();value.update(validator_sha256=validator_digest(),implementation_sha256=implementation_digest())
    for c in value['cells']:c['status']='not_run'
    (tmp_path/'private').mkdir()
    _checkpoint(tmp_path,value,index,env,make_policy('passive'),[],set(),[],{'passed':True},MovementObserver(env))
    (tmp_path/'report.json').write_text('crash left a truncated public rendering')
    restored,state=load_progress(tmp_path,expected,validator_digest())
    assert restored['cells']==value['cells'] and state['index']==index
    complete=report();complete.update(validator_sha256=validator_digest(),implementation_sha256=implementation_digest())
    _checkpoint(tmp_path,complete,index,env,make_policy('passive'),[],set(),[],{'passed':True},MovementObserver(environment()))
    loaded,_=load_progress(tmp_path,expected,validator_digest())
    assert loaded['status']=='complete'
    path=tmp_path/'private/checkpoint.json.gz';raw=json.loads(gzip.decompress(path.read_bytes()))
    raw['policy']['policy']='dispatcher';raw['sha256']=checksum({k:v for k,v in raw.items() if k!='sha256'})
    path.write_bytes(gzip.compress(json.dumps(raw).encode()))
    with pytest.raises(ValueError,match='identity'):load_progress(tmp_path,expected,validator_digest())


def test_public_findings_are_aggregates_without_event_details():
    private=[dict(code='fault',category='contradiction',minute=25,resource='crew-0',detail='private context'),
             dict(code='fault',category='contradiction',minute=30,resource='crew-1',detail='another')]
    assert aggregate_findings(private)==[dict(code='fault',category='contradiction',count=2)]
