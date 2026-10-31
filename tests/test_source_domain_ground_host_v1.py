"""Synthetic domain-profile source incident integration; no private GIS inputs."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_synthetic_domain_ground_host_integration():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'envs/wildfire_containment_score_v1/src'),str(ROOT)]))
    run=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert run.returncode==0,run.stdout+run.stderr


def main():
    import copy
    from dataclasses import replace
    from unittest.mock import patch
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import PROFILE
    from eval import domain_profile_weather_v2 as domain
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
    from wildfire_source_domain_ground_v1 import host as mod
    n=16;t=[500,0,0,0,-500,8000];grid=dict(width=n,height=n,crs='EPSG:25830',transform=t)
    pos=lambda x,y:[(x+.5)*500,8000-(y+.5)*500]
    pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')}
    doc=dict(version='wildfire-extended-inputs-v1',grid=grid,domain_mask=[[True]*n for _ in range(n)],
             surface=[['land']*n for _ in range(n)],fuel=[[4]*n for _ in range(n)],
             elevation=[[100.]*n for _ in range(n)],access=[['unknown']*n for _ in range(n)],source_pins=pins)
    doc['surface'][2][2]='unknown';doc['fuel'][2][2]=None;doc['fuel'][8][9]=None
    doc['surface'][7][7]='mixed'
    doc['surface'][6][6]='mixed';doc['elevation'][6][6]=None
    doc['surface'][14][1]='sea';doc['fuel'][14][1]=None;doc['elevation'][14][1]=None
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    base_id=dict(version='wildfire-small-water-mixed-exercise-input-v4',strict='s',water_v2='w',overlay_v3='3',
                 overlay_v4='4',dera_rivers='r',catalog='e'*64,parent='p',road='road',crossing_proxy=.9,
                 input_sha256=contract.document_sha256)
    base=MixedInputsV2(contract,((7,7,.8,.9),(6,6,.9,.9)),tuple(sorted(base_id.items())),_VERIFIED)
    cells=[]
    for y in range(n):
        for x in range(n):
            status=('unknown' if (x,y) in ((2,2),(6,6)) else 'mapped_sea' if (x,y)==(1,14) else 'existing_v4_mixed_source' if (x,y)==(7,7) else 'mapped_land_source')
            row=dict(column=x,row=y,status=status,source_fuel_class=doc['fuel'][y][x])
            if (x,y)==(9,8):row.update(status='new_land_fuel_estimated_hypothesis',surface_assumption='land',
                fuel_hypothesis=dict(status='estimated_hypothesis',nominal_class_assumption=4,
                                     scenarios={'nominal':{'assumed_fuel_class':4}}))
            cells.append(row)
    profile=dict(version='wildfire-domain-fuel-surface-sensitivity-profile-v3',status='private_synthetic_evidence_not_runtime_input',
                 grid=grid,cells=cells,source_pins=dict(strict='s',water_v2='w',exercise_v3='3',exercise_v4='4',dera_rivers='r'))
    profile['sha256']=api._hash(profile)
    estimated=domain.convert_profile(profile,base,expected_profile_sha256=profile['sha256'],opt_in=True)
    aircraft=dict(id='air',kind='aircraft',position=pos(4,4),base_position=pos(4,4),state='available',available_at=0,
        capacity_litres=1000,water_litres=1000,work_remaining=100,total_work_remaining=100,
        fuel_minutes=180,fuel_capacity_minutes=180,facility_id='base')
    op_profile=dict(copy.deepcopy(PROFILE),facilities={'base':dict(position=pos(4,4),allowed_kinds=['aircraft'],water_litres=10000,refuel=True)})
    original=dict(id='fixture',seed=7,synthetic=True,historical_replay=False,sector_grid=[0,0],start_utc='2021-06-01T12:00:00Z',
        resources=[aircraft],groups=[dict(id='people',count=10,position=pos(4,4),home=pos(4,4),status='unwarned',exposed=False,destination_id=None)],
        destinations=[dict(id='private-destination-id',name='private-destination-name',position=[1111.25,2222.75],
                           capacity=987654321,status='open',private_note='private-destination-marker')],
        centers=['center'],events=[dict(kind='destination_closed',minute=3,destination_id='private-destination-id'),
                                  dict(kind='aircraft_unavailable',minute=12,until=15)],profile=op_profile)
    original['sha256']=api._hash(original)
    parent={'grid':{'centre_lon_lat':[[[-4.,36.7]]]},'version':'synthetic-parent'};parent['sha256']=api._hash(parent)
    base_id['parent']=parent['sha256']
    # Rebuild the exact synthetic lineage before conversion, as a production
    # selector would bind the original base input independently.
    base=MixedInputsV2(contract,((7,7,.8,.9),(6,6,.9,.9)),tuple(sorted(base_id.items())),_VERIFIED)
    estimated=domain.convert_profile(profile,base,expected_profile_sha256=profile['sha256'],opt_in=True)
    assert estimated.base.contract.surface[6][6]=='unknown'
    assert all(row[:2]!=(6,6) for row in estimated.base.factors)
    case=dict(id='fire',seed=7,initial_fire=[[8,8]],warmup_minutes=0,wind=[0.,0.],events=[],
        weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],
        parent_scenario_sha256=original['sha256'],incoming_reports=[dict(minute=1,source='reporter',role='resident',channel='radio',
            location=[8,8],text='Synthetic report',delay=2)])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='e'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    selector=object.__new__(SourceCaseCatalogV1)
    selector._identity=dict(input_identity=base_id,parent=parent['sha256'],fire=drafts['sha256']);selector._fire=drafts
    selected=dict(original=original,expanded_fire=case)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    with patch.object(SourceCaseCatalogV1,'_ensure',lambda self:None),patch.object(SourceCaseCatalogV1,'private_case',lambda self,c:copy.deepcopy(selected)), \
         patch.object(domain,'PROFILE_PIN',profile['sha256']),patch.object(mod.original,'APPROVED_PROFILE_SHA256',profile['sha256']):
        pin=mod.input_identity_sha256(estimated)
        from eval import domain_profile_weather_v1 as old_domain
        old_input=old_domain.convert_profile(profile,base,expected_profile_sha256=profile['sha256'],opt_in=True)
        try:mod.DomainProfileGroundIncidentV1(old_input,selector,parent,case_id='fixture',containment_config=config,
            pressure_scale=1,expected_input_sha256=mod.input_identity_sha256(old_input),opt_in=True)
        except ValueError:pass
        else:raise AssertionError('v1 input accepted by v3 host')
        def factory(pin_arg=pin):return mod.DomainProfileGroundIncidentV1(estimated,selector,parent,case_id='fixture',
            containment_config=config,pressure_scale=1,expected_input_sha256=pin_arg,opt_in=True)
        try:factory('0'*64)
        except ValueError:pass
        else:raise AssertionError('wrong input pin accepted')
        with patch.object(mod.original,'APPROVED_CONVERTER_SHA256','0'*64):
            try:factory()
            except RuntimeError:pass
            else:raise AssertionError('unreviewed converter source accepted')
        bad_profile=dict(estimated.identity);bad_profile['profile_sha256']='0'*64
        altered=replace(estimated,identity=tuple(sorted(bad_profile.items())))
        try:mod.DomainProfileGroundIncidentV1(altered,selector,parent,case_id='fixture',containment_config=config,
            pressure_scale=1,expected_input_sha256=mod.input_identity_sha256(altered),opt_in=True)
        except ValueError:pass
        else:raise AssertionError('unapproved profile accepted')
        access=[list(row) for row in estimated.base.contract.access];access[0][0]='accessible'
        altered_contract=replace(estimated.base.contract,access=tuple(tuple(row) for row in access))
        altered=replace(estimated,base=replace(estimated.base,contract=altered_contract))
        try:mod.DomainProfileGroundIncidentV1(altered,selector,parent,case_id='fixture',containment_config=config,
            pressure_scale=1,expected_input_sha256=mod.input_identity_sha256(altered),opt_in=True)
        except ValueError:pass
        else:raise AssertionError('unreviewed access accepted')
        # A profile-unknown mixed cell with missing terrain must be rejected
        # before the physical fire constructor or warm-up can run.
        surface=[list(row) for row in estimated.base.contract.surface]
        elevation=[list(row) for row in estimated.base.contract.elevation]
        surface[7][7]='unknown';elevation[7][7]=None
        bad_contract=replace(estimated.base.contract,surface=tuple(tuple(row) for row in surface),
                             elevation=tuple(tuple(row) for row in elevation))
        bad=replace(estimated,base=replace(estimated.base,contract=bad_contract))
        with patch.object(mod.original.DomainProfileWeatherFireV2,'__init__',side_effect=AssertionError('fire constructed')):
            try:mod.DomainProfileGroundIncidentV1(bad,selector,parent,case_id='fixture',containment_config=config,
                pressure_scale=1,expected_input_sha256=mod.input_identity_sha256(bad),opt_in=True)
            except ValueError:pass
            else:raise AssertionError('unknown mixed cell reached fire construction')
        # Prior v2 checkpoints remain reproducible by v2 and cannot be silently
        # restored into the new privacy behavior/version.
        from wildfire_source_domain_v2 import host as legacy
        with patch.object(legacy,'APPROVED_PROFILE_SHA256',profile['sha256']):
            old_host=legacy.DomainProfileSourceIncidentV2(estimated,selector,parent,case_id='fixture',containment_config=config,
                pressure_scale=1,expected_input_sha256=legacy.input_identity_sha256(estimated),opt_in=True)
            old_checkpoint=old_host.checkpoint()
        rejected=factory()
        try:rejected.restore(old_checkpoint,expected_checkpoint_sha256=api._hash(old_checkpoint))
        except ValueError:pass
        else:raise AssertionError('legacy checkpoint silently migrated')
        a=factory();view=a.situation();assert 'estimated-fuel' in view['scope']
        assert 'score' not in view and 'population' not in view
        assert 'estimated_mask' not in str(view) and 'profile_sha256' not in str(view)
        before=a.checkpoint();rejections=[]
        for identifier in ('people','unknown'):
            try:a.protect(identifier,'shelter')
            except ValueError as error:rejections.append(str(error))
            else:raise AssertionError('private population oracle accepted')
        assert len(set(rejections))==1 and api._encoded(before)==api._encoded(a.checkpoint())
        # The separately versioned seam retains the exact estimated backend,
        # censor and shared clock while advancing an empty containment batch.
        from wildfire_source_domain_ground_v1.seam import EstimatedHoldFireV1
        seam_host=factory();seam=EstimatedHoldFireV1(seam_host._fire,lambda:True)
        seam_host._fire=seam_host._weather._fire=seam
        assignment=dict(resource_id='synthetic-crew',edge=[[8,8],[7,8]],protected_cell=[7,8],
                        kind='crew',ready=True,work_remaining=2,water_litres=0,water_cost=0)
        seam.stage([assignment]);seam_host.advance()
        assert seam.costs==[dict(resource_id='synthetic-crew',work_minutes=1,water_litres=0.0)]
        assert seam._fire._containment._lines and seam_host._minute==seam_host._weather._minute==1
        saved_seam=seam_host.checkpoint();seam_clone=factory()
        seam_clone.restore(saved_seam,expected_checkpoint_sha256=api._hash(saved_seam))
        assert api._encoded(seam_clone.checkpoint())==api._encoded(saved_seam)
        clone_seam=EstimatedHoldFireV1(seam_clone._fire,lambda:True)
        seam_clone._fire=seam_clone._weather._fire=clone_seam
        seam.stage([]);clone_seam.stage([])
        assert seam_host.advance()==seam_clone.advance()
        assert api._encoded(seam_host.checkpoint())==api._encoded(seam_clone.checkpoint())
        assert a._fire._is_land(9,8) and not a._fire._is_land(2,2) and not a._fire._is_land(1,14)
        assert a._fire._fire.cells[14][1]['fuel']==0
        estimated_ignition=factory();assert estimated_ignition._fire.change({'ignite':[[9,8]]})
        assert [9,8] in estimated_ignition._fire.burning()
        assert not any(r['kind']=='incoming_report' for r in a.reports(limit=100))
        a.clear_aircraft('air');target=a.situation()['known_fire'][0]
        opt=a.route_options('air',target)['options'][0];a.dispatch('air',target,opt['id'])
        assert not any(r['kind']=='dispatch_result' for r in a.reports(limit=100))
        a.advance()
        assert a._ops.destinations['private-destination-id']['status']=='closed'
        assert not any(r['kind']=='destination_threatened' for r in a.reports(limit=100))
        a.advance(3)
        assert a._drop_audit and a._ops.resources['air']['water_litres']==0
        reports=a.reports(limit=100)
        assert any(r['kind']=='incoming_report' for r in reports)
        closure=next(r for r in reports if r['kind']=='destination_threatened')
        assert closure['payload']=={'text':'A destination has closed.'} and closure['location'] is None
        public=str(a.situation())+str(reports)
        assert all(secret not in public for secret in ('private-destination-id','private-destination-name',
                   'private-destination-marker','1111.25','2222.75','987654321'))
        snap=a.checkpoint();b=factory();b.restore(snap,expected_checkpoint_sha256=api._hash(snap))
        assert a.advance(3)==b.advance(3) and api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
        from wildfire_source_restricted_scored_v1 import configuration_identity as restricted_identity
        try:restricted_identity(a,[],public_population=[])
        except ValueError:pass
        else:raise AssertionError('legacy scorer accepted the domain host')
        from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1
        try:MixedHoldFireV1(a._fire)
        except ValueError:pass
        else:raise AssertionError('legacy hold fire accepted the estimated backend')
        from wildfire_source_walk_exercise_v1 import SourceWalkExerciseV1
        try:SourceWalkExerciseV1(a,candidate={},expected_candidate_sha256='',parent={},v2={},v4={},
                                 candidate_index=0,public_crew_id='crew',expected_identity={},opt_in=True)
        except ValueError:pass
        else:raise AssertionError('legacy ground walk accepted the domain host')
        trusted=a.checkpoint()
        a._minute+=1
        try:a.situation()
        except RuntimeError:pass
        else:raise AssertionError('clock divergence accepted')
        a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        with patch.object(domain,'CHOICE','unreviewed-choice'):
            try:a.situation()
            except RuntimeError:pass
            else:raise AssertionError('changed profile policy accepted')
        try:a.situation()
        except RuntimeError:pass
        else:raise AssertionError('untrusted policy change did not poison host')
        a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        a._fire=object()
        try:a.situation()
        except RuntimeError:pass
        else:raise AssertionError('fire binding change accepted')
        a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        a._fire._fire.cells[7][7]['initial_fuel']+=1
        try:a.situation()
        except ValueError:pass
        else:raise AssertionError('changed effective mixed fuel accepted')
        a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        a._fire._estimated[(9,8)]=(4,.5,1.)
        try:a.situation()
        except ValueError:pass
        else:raise AssertionError('changed effective estimated factors accepted')
        a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        tape=a.transcript();fresh=factory()
        assert fresh.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'],
            expected_final_checkpoint_sha256=api._hash(a.checkpoint()))==a.situation()
        missing_terrain=factory();assert not missing_terrain._fire.change({'ignite':[[6,6]]})
        assert missing_terrain.advance()['status']=='censored'
        c=factory();assert not c._fire.change({'ignite':[[2,2]]})
        assert c.advance()['status']=='censored'
        try:c.reports()
        except api.CensoredError:pass
        else:raise AssertionError('censored reports leaked')
        # A matching fingerprint remains required on each boundary.
        object.__setattr__(estimated,'estimates',estimated.estimates+((1,1,4,.9,1.),))
        try:a.situation()
        except RuntimeError:pass
        else:raise AssertionError('changed estimated input accepted')

if __name__=='__main__':main()


def test_mixed_factor_cannot_survive_unknown_missing_terrain():
    """A stale v4 factor must never admit a profile-unknown terrain cell."""
    from types import SimpleNamespace
    import pytest
    from wildfire_source_domain_v3.host import _check_live_inputs
    contract=SimpleNamespace(width=1,height=1,surface=(('unknown',),),fuel=((4,),),elevation=((None,),))
    inputs=SimpleNamespace(base=SimpleNamespace(contract=contract,factors=((0,0,.9,.9),)),estimates=())
    backend=SimpleNamespace(_fire=SimpleNamespace(cells=[[{'terrain':'field','initial_fuel':1.0}]]))
    with pytest.raises(ValueError,match='invalid mixed fuel evidence'):
        _check_live_inputs(inputs,backend)
