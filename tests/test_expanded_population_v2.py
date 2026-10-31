"""Synthetic v2 handover exposure and continuation tests."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_handover_exposure():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'), str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'), str(ROOT/'envs/wildfire_containment_v1/src'),
        str(ROOT/'envs/wildfire_containment_score_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable,__file__], cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import json
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
    from wildfire_containment_score_v1.scoring import recompute
    from wildfire_expanded_incident_v1.host import CATALOG_VERSION as HOST_CATALOG
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_expanded_full_v1.hold import WeatherMixedHoldV1
    from wildfire_expanded_population_v2 import ExpandedPopulationV2, CATALOG_VERSION, VERSION

    base, network = fixture_inputs()
    for edge in network['edges']: edge['allowed_modes'] = ['crew','engine']
    network['sha256'] = api._hash({k:v for k,v in network.items() if k!='sha256'})
    n = base['grid']['width']
    document = dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)], surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']), elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    document['surface'][16][17] = 'mixed'; document['access'][16][17] = 'unknown'
    point = lambda key: next(node['position'] for node in network['nodes'] if node['id']==key)
    center = lambda x,y: [base['grid']['transform'][2]+(x+.5)*500,
                          base['grid']['transform'][5]-(y+.5)*500]
    case = dict(id='synthetic-population-v2',seed=7,initial_fire=[[16,16]],wind=[0.,0.],centers=['center-0'],
        warmup_minutes=2,weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
            relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                source_catalog_sha256='e'*64,cases=[case])
    drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(groups, *, expected_override=None, opt_in=True, doc=document):
        inputs=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
        # Synthetic test-only stand-in, not validated source evidence.
        mixed=MixedInputsV2(inputs,((17,16,.8,.5),),tuple(sorted(dict(
            version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64).items())),_VERIFIED)
        sc=dict(id=case['id'],seed=case['seed'],initial_fire=case['initial_fire'],wind=case['wind'],
            center='center-0',resources=[dict(id='crew-0',kind='crew',position=point('2:2'),
                                              work_remaining=240,water_litres=0)])
        catalog=dict(version=HOST_CATALOG,role='synthetic_fixture_only',inputs_sha256=inputs.document_sha256,
                     network_sha256=network['sha256'],cases=[sc])
        host=WeatherMixedHoldV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            network=network,expected_network_sha256=network['sha256'],catalog=catalog,
            expected_catalog_sha256=api._hash(catalog),containment_config=config,pressure_scale=1,opt_in=True)
        pop=dict(version=CATALOG_VERSION,role='synthetic_fixture_only',case_id=case['id'],
                 inputs_sha256=inputs.document_sha256,network_sha256=network['sha256'],
                 groups=copy.deepcopy(groups),destinations=[dict(id='safe',capacity=20,position=point('8:4'))])
        return ExpandedPopulationV2(host,pop,expected_catalog_sha256=expected_override or api._hash(pop),opt_in=opt_in)
    groups=[dict(id='inherited',count=4,position=center(16,16),initial_exposed=True,handover_exposure=True),
            dict(id='new',count=3,position=center(15,15),initial_exposed=False,handover_exposure=False),
            dict(id='prior',count=2,position=point('4:4'),initial_exposed=True,handover_exposure=None)]
    a=factory(groups)
    obs=a.situation()
    assert obs['version']==VERSION
    public={g['id']:g for g in obs['population']}
    assert public['inherited']['exposed'] is True and public['new']['exposed'] is False
    assert public['inherited']['exposure_observed_at']==0 and public['inherited']['observed_at']==0
    assert public['new']['exposure_observed_at']==0 and public['new']['observed_at']==0
    assert public['prior']['exposed'] is None and public['prior']['exposure_observed_at'] is None
    assert a.private_handover_exposure()=={'inherited':4,'new':0,'prior':2}
    assert a._inner._known_at=={'inherited':0,'new':0,'prior':0}
    assert a._inner._exposed_at=={'inherited':0,'new':None,'prior':None}
    assert 'dose' not in json.dumps(obs) and 'weather_schedule' not in json.dumps(obs)
    group_record=lambda o:{k:dict(count=g['count'],exposed=g['count'] if g['exposed'] else 0,
                                  status=g['status'],protection_pending=False) for k,g in o.groups.items()}
    audit=[dict(minute=0,footprint=[16*n+16],burnable=[],cell_area_hectares=25,
                new_footprint=[],population=group_record(a._inner._ops))]
    counter=OutcomeCounter({16*n+16}, set(), a._inner._ops.groups, 25)
    assert counter.initial_exposure==6
    for minute in range(1,18):
        a.advance()
        assert not a._inner._ops.groups['new']['exposed']
        audit.append(dict(minute=minute,new_footprint=[],population=group_record(a._inner._ops)))
    checkpoint=json.loads(json.dumps(a.checkpoint()))
    # A fresh instance replaying the same actions reaches the exact checkpoint.
    fresh=factory(groups)
    fresh.advance(17)
    assert api._encoded(fresh.checkpoint())==api._encoded(checkpoint)
    b=factory(groups); b.restore(checkpoint)
    assert {g['id']:g for g in b.situation()['population']}['prior']['exposed'] is None
    assert b.private_handover_exposure()['prior']==2
    assert a.advance()==b.advance()
    audit.append(dict(minute=18,new_footprint=[],population=group_record(a._inner._ops)))
    assert a._inner._ops.groups['new']['exposed'] is True
    assert not {g['id']:g for g in a.situation()['population']}['new']['exposed']
    assert {g['id']:g for g in a.situation()['population']}['new']['exposure_observed_at']==0
    independently=recompute(audit,scoring_mask=[],terminal=False,abandoned=False,censored=False)
    outcome=counter.result(a._inner._ops.groups,18,False,False,False,a._inner._ops.orders)
    assert independently['inherited_exposed_population']==outcome['inherited_exposed_population']==6
    assert independently['new_exposed_population']==outcome['new_exposed_population']==3
    assert independently['score_eligible'] is False  # not a scored full-day result
    for _ in range(6): assert a.advance()==b.advance()
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    assert a._inner._exposed_at=={'inherited':0,'new':18,'prior':None}
    assert {g['id']:g for g in a.situation()['population']}['new']['exposed'] is True
    assert {g['id']:g for g in a.situation()['population']}['new']['exposure_observed_at']==18
    assert a.private_handover_exposure()=={'inherited':4,'new':0,'prior':2}
    # Different hidden inherited exposure with the same undisclosed handover
    # must produce identical actor calls until a group report is delivered.
    hidden_true=[dict(id='private',count=2,position=point('4:4'),
                      initial_exposed=True,handover_exposure=None)]
    hidden_false=copy.deepcopy(hidden_true);hidden_false[0]['initial_exposed']=False
    left=factory(hidden_true);right=factory(hidden_false)
    assert left.private_handover_exposure()=={'private':2}
    assert right.private_handover_exposure()=={'private':0}
    assert left.situation()==right.situation() and left.reports()==right.reports()
    assert left.situation()['population'][0]['exposed'] is None
    assert left.protect('private','shelter')==right.protect('private','shelter')
    assert left.situation()==right.situation() and left.reports()==right.reports()
    for _ in range(8):
        left.advance();right.advance()
        left_reports=[r for r in left.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id')=='private']
        right_reports=[r for r in right.reports(limit=200) if r.get('payload',{}).get('group',{}).get('id')=='private']
        if left_reports or right_reports: break
        assert left.situation()==right.situation() and left.reports()==right.reports()
    assert left_reports and right_reports
    assert left.situation()['population'][0]['exposed'] is True
    assert right.situation()['population'][0]['exposed'] is False
    assert left.situation()['population'][0]['exposure_observed_at'] == left_reports[0]['observed_at']
    assert right.situation()['population'][0]['exposure_observed_at'] == right_reports[0]['observed_at']
    inconsistent=copy.deepcopy(hidden_true);inconsistent[0]['handover_exposure']=False
    def rejects(fn):
        try: fn()
        except ValueError: return
        raise AssertionError('invalid input accepted')
    rejects(lambda: factory(inconsistent))
    inconsistent_other=copy.deepcopy(hidden_false);inconsistent_other[0]['handover_exposure']=True
    rejects(lambda: factory(inconsistent_other))
    bad_known=copy.deepcopy(hidden_true);bad_known[0]['handover_exposure']=1
    rejects(lambda: factory(bad_known))
    missing_report=copy.deepcopy(hidden_true);del missing_report[0]['handover_exposure']
    rejects(lambda: factory(missing_report))
    bad=copy.deepcopy(groups);bad[0]['initial_exposed']=False;bad[0]['handover_exposure']=False
    rejects(lambda: factory(bad))  # actual burning cell at handover, no exposure declared
    missing=copy.deepcopy(groups);del missing[0]['initial_exposed']
    rejects(lambda: factory(missing))
    not_bool=copy.deepcopy(groups);not_bool[0]['initial_exposed']=1
    rejects(lambda: factory(not_bool))
    rejects(lambda: factory(groups,expected_override='0'*64))
    rejects(lambda: factory(groups,opt_in=False))
    # Unsupported ignition neighborhood is censored before handover; no
    # actor-facing population or outcome is exposed, and no hazard is inferred.
    cold_doc=copy.deepcopy(document)
    cold_doc['surface'][16][15]='unknown';cold_doc['access'][16][15]='unknown'
    cold_groups=copy.deepcopy(groups);cold_groups[0]['initial_exposed']=False;cold_groups[0]['handover_exposure']=None
    cold=factory(cold_groups,doc=cold_doc)
    assert cold.situation()['status']=='censored' and 'population' not in cold.situation()
    try: cold.reports()
    except api.CensoredError: pass
    else: raise AssertionError('censored handover exposed reports')
    cold_saved=json.loads(json.dumps(cold.checkpoint()))
    cold_restored=factory(cold_groups,doc=cold_doc); cold_restored.restore(cold_saved)
    assert api._encoded(cold_saved)==api._encoded(cold_restored.checkpoint())
    tampered=copy.deepcopy(checkpoint);tampered['population']['population']['groups']['inherited']['exposed']=False
    # Even rehashing both wrappers cannot erase independently pinned handover exposure.
    p=tampered['population'];p['sha256']=api._hash({k:v for k,v in p.items() if k!='sha256'})
    tampered['sha256']=api._hash({k:v for k,v in tampered.items() if k!='sha256'})
    try: b.restore(tampered)
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('initial exposure erased by checkpoint')
    b.restore(checkpoint)
    assert api._encoded(b.checkpoint())==api._encoded(checkpoint)


if __name__=='__main__': main()
