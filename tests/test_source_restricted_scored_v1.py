"""Full synthetic restricted score, replay and public observation contract."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_full_synthetic_day():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),
        str(ROOT/'envs/wildfire_containment_score_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=900)
    assert result.returncode==0,result.stdout+result.stderr



def test_real_wrapper_runs_under_restricted_orchestrator():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',SOURCE_QUALIFY_ONLY='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),
        str(ROOT/'envs/wildfire_containment_score_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy
    from unittest.mock import patch
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import PROFILE
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
    from wildfire_source_restricted_v1 import RestrictedSourceIncidentV1
    from wildfire_source_restricted_scored_v1 import RestrictedSourceScoredV1,configuration_identity,verify_outcome
    n=8;g=dict(width=n,height=n,crs='EPSG:25830',transform=[500,0,297000,0,-500,4054500]);t=g['transform']
    pos=[t[2]+4.5*t[0],t[5]+4.5*t[4]]
    parent={'grid':{'centre_lon_lat':[[[-4.,36.7]]]},'version':'synthetic-parent'};parent['sha256']=api._hash(parent)
    unit=dict(id='air',kind='aircraft',position=pos,base_position=pos,state='available',available_at=0,
              capacity_litres=1000,water_litres=1000,work_remaining=100,total_work_remaining=100,
              fuel_minutes=180,fuel_capacity_minutes=180,facility_id='base')
    profile=dict(copy.deepcopy(PROFILE),facilities={'base':dict(position=pos,allowed_kinds=['aircraft'],water_litres=10000,refuel=True)})
    original=dict(id='fixture',seed=3,synthetic=True,historical_replay=False,sector_grid=[0,0],
        start_utc='2021-06-01T12:00:00Z',resources=[unit],groups=[dict(id='group',count=10,position=pos,home=pos,
        status='unwarned',exposed=False,destination_id=None)],destinations=[],centers=['center'],events=[],profile=profile)
    original['sha256']=api._hash(original)
    record=dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)
    case=dict(id='fire',seed=3,initial_fire=[],wind=[0.,0.],warmup_minutes=0,events=[],weather_schedule=[record],
              parent_scenario_sha256=original['sha256'],incoming_reports=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='a'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    doc=dict(version='wildfire-extended-inputs-v1',grid={k:copy.deepcopy(g[k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)],surface=[['land']*n for _ in range(n)],fuel=[[4]*n for _ in range(n)],
        elevation=[[100.]*n for _ in range(n)],access=[['unknown']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    ident=dict(version='wildfire-small-water-mixed-exercise-input-v4',catalog='a'*64,input_sha256=contract.document_sha256)
    mixed=MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    selector=object.__new__(SourceCaseCatalogV1);selector._identity=dict(input_identity=ident,parent=parent['sha256'],fire=drafts['sha256']);selector._fire=drafts
    selected=dict(original=original,expanded_fire=case)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    with patch.object(SourceCaseCatalogV1,'_ensure',lambda self:None),patch.object(SourceCaseCatalogV1,'private_case',lambda self,c:copy.deepcopy(selected)):
        def host():return RestrictedSourceIncidentV1(mixed,selector,parent,case_id='fixture',containment_config=config,pressure_scale=1,opt_in=True)
        candidate=host();mask=[0,1,2]
        public=[dict(id='group',count=10,position=pos,status='unwarned',exposed=None,observed_at=0)]
        identity=configuration_identity(candidate,mask,public_population=public)
        try:RestrictedSourceScoredV1(candidate,scoring_mask=mask,expected_identity=identity,public_population=[],opt_in=True)
        except ValueError:pass
        else:raise AssertionError('unapproved roster accepted')
        hidden=copy.deepcopy(public);hidden[0]['exposed']=True
        try:configuration_identity(candidate,mask,public_population=hidden)
        except ValueError:pass
        else:raise AssertionError('private exposure disclosed')
        env=RestrictedSourceScoredV1(candidate,scoring_mask=mask,expected_identity=identity,public_population=public,opt_in=True)
        obs=env.situation();assert obs['population'][0]['exposed'] is None and obs['population'][0]['observed_at']==0
        before=env.checkpoint()
        for method,args in ((env.advance,(0,)),(env.protect,('not-public','shelter')),
                            (env.protect,('group','evacuate','unknown'))):
            try:method(*args)
            except ValueError:pass
            else:raise AssertionError('invalid action accepted')
            assert api._encoded(env.checkpoint())==api._encoded(before)
        stop=host();stop._case['events']=[dict(minute=1,kind='private-future-event')]
        stop._identity['original']=api._hash({k:v for k,v in stop._case.items() if k!='sha256'})
        observation=stop.advance(1)
        assert observation['reason']=='unsupported_scheduled_event'
        assert 'private-future-event' not in str(observation)
        assert stop._unsupported_kind=='private-future-event'
        assert 'score' not in obs and 'no ground' in obs['scope']
        assert verify_outcome(env,fresh_host=host,expected_identity=identity,scoring_mask=mask)['status']=='unfinished'
        if os.environ.get('SOURCE_QUALIFY_ONLY'):
            import tempfile
            from eval.qualify_source_restricted_day_v1 import qualify, BlockedCase
            ids=[f'case-{i}' for i in range(12)]
            def factory(identifier):
                if identifier!=ids[0]:raise BlockedCase()
                return RestrictedSourceScoredV1(host(),scoring_mask=mask,expected_identity=identity,
                                                public_population=public,opt_in=True)
            class Policy:
                def act(self,actor,observation):actor.advance(1)
            with tempfile.TemporaryDirectory() as directory:
                report=qualify(ids,checkpoint=directory+'/state',run_id='synthetic',make_environment=factory,
                    make_policy=lambda _:Policy(),verify_outcome=lambda *args:None,max_decisions=1)
                assert report['scope']=='restricted_no_ground_no_evac'
                assert report['cases'][0]['reason']=='decision_limit' and report['cases'][0]['minute']==1
                assert all(row['status']=='blocked' for row in report['cases'][1:])
            return
        env.advance(1)
        saved=env.checkpoint()
        clone=RestrictedSourceScoredV1(host(),scoring_mask=mask,expected_identity=identity,public_population=public,opt_in=True)
        clone.restore(saved,expected_checkpoint_sha256=api._hash(saved))
        assert api._encoded(clone.checkpoint())==api._encoded(saved)
        assert clone.advance(1)==env.advance(1)
        assert clone.protect('group','shelter')=={'status':'submitted'}
        env.advance(58)
        for _ in range(23):env.advance(60)
        assert env.situation()['minute']==1440 and env.situation()['status']=='end_unscored'
        outcome=verify_outcome(env,fresh_host=host,expected_identity=identity,scoring_mask=mask)
        assert outcome['status']=='restricted_full_day' and outcome['scope']=='restricted_no_ground_no_evac' and outcome['verified'] and outcome['score']==1.0
        assert verify_outcome(env,fresh_host=host,expected_identity=identity,scoring_mask=[0])['status']=='verification_failed'

if __name__=='__main__':main()


def test_80_20_accounting_excludes_inherited_harm():
    from wildfire_containment_score_v1.scoring import recompute
    from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
    import pytest
    groups={'a':dict(count=8,exposed=False,status='unwarned'),
            'b':dict(count=2,exposed=False,status='unwarned'),
            'inherited':dict(count=4,exposed=True,status='unwarned')}
    def pop():return {k:dict(count=g['count'],exposed=g['count'] if g['exposed'] else 0,
                             status=g['status'],protection_pending=False) for k,g in groups.items()}
    records=[dict(minute=0,footprint=[9],burnable=[0,1,9],cell_area_hectares=25,
                  new_footprint=[],population=pop())]
    counter=OutcomeCounter({9},{0,1,9},groups,25)
    groups['b']['exposed']=True;counter.update({0})
    for minute in range(1,1441):
        records.append(dict(minute=minute,new_footprint=[0] if minute==1 else [],population=pop()))
    outcome=recompute(records,scoring_mask=[0,1,9],terminal=True,abandoned=False,censored=False)
    assert outcome['graded_score']==pytest.approx(.74)
    assert counter.result(groups,1440,True,False,False)['graded_score']==pytest.approx(.74)
