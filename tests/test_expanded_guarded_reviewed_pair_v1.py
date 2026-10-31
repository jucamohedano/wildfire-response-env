"""Real reviewed-pair host integration: no routing verifier test seam."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
GRAPH=Path('/tmp/expanded-post-anchors-v4/network.json')
DRAFT=Path('/tmp/expanded-post-anchors-v4/scenario-drafts.json')
SOURCE=Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')


@pytest.mark.skipif(not all(p.exists() for p in (GRAPH,DRAFT,SOURCE)),reason='private reviewed source pair unavailable')
def test_real_reviewed_pair_host_and_hold():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=240)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy,inspect,json
    from unittest.mock import patch
    import adapter_v1 as api
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval import expanded_guarded_routes_v1 as routes
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_guarded_v1 import GuardedIncidentV1,CATALOG_VERSION
    from wildfire_expanded_guarded_v1 import host as host_module
    from wildfire_expanded_guarded_v1.hold_v1 import GuardedHoldV1,EVIDENCE_VERSION

    source=load_candidate();graph=json.loads(GRAPH.read_bytes());anchored=json.loads(DRAFT.read_bytes())
    source_pin=digest(source);anchors=routes.check_anchored_draft(anchored,anchored['sha256'],graph['sha256'],graph)
    pos=anchors['center-0'][1];dest=anchors['center-1'][1]
    grid=dict(width=84,height=59,crs='EPSG:25830',transform=[500,0,290000,0,-500,4064500])
    def coord(position):return [int((position[0]-290000)//500),int((4064500-position[1])//500)]
    positions=[coord(pos),coord(dest)]
    fire=next([x,y] for x,y in [(42,29),(20,20),(60,40),(10,10)]
              if all(max(abs(x-p[0]),abs(y-p[1]))>4 for p in positions))
    document=dict(version='wildfire-extended-inputs-v1',grid=grid,
        domain_mask=[[True]*84 for _ in range(59)],surface=[['land']*84 for _ in range(59)],
        fuel=[[4]*84 for _ in range(59)],elevation=[[100.]*84 for _ in range(59)],
        access=[['unknown']*84 for _ in range(59)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
    # Synthetic fire fixture only. Road source and graph go through the real,
    # unpatched production reviewed-pair verifier.
    mixed=MixedInputsV2(contract,(),tuple(sorted(dict(version='wildfire-small-water-mixed-exercise-input-v4',
                                                       catalog='e'*64).items())),_VERIFIED)
    case=dict(id='reviewed-pair-synthetic',seed=7,initial_fire=[fire],wind=[0.,0.],centers=['center-0'],
        warmup_minutes=0,weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,
        relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)],events=[])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',
                source_catalog_sha256='e'*64,cases=[case]);drafts['sha256']=api._hash(drafts)
    evidence=dict(version=routes.ACCESS_VERSION,role='source_unknown',grid=grid,
                  surface=[['land']*84 for _ in range(59)],access=[['unknown']*84 for _ in range(59)])
    catalog=dict(version=CATALOG_VERSION,role='synthetic_source_adapted_not_historical',case_id=case['id'],
        inputs_sha256=contract.document_sha256,graph_sha256=graph['sha256'],
        resources=[dict(id='crew',kind='crew',position=pos,work_remaining=10,water_litres=0)],
        groups=[dict(id='cohort',count=3,position=pos,initial_exposed=False,handover_exposure=None)],
        destinations=[dict(id='safe',capacity=10,position=dest)])
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def build(*,src=source,src_pin=source_pin):
        return GuardedIncidentV1(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=case['id'],
            graph=graph,expected_graph_sha256=graph['sha256'],source=src,expected_source_sha256=src_pin,
            cell_evidence=evidence,expected_evidence_sha256=digest(evidence),allowed_edge_ids=[],
            catalog=catalog,expected_catalog_sha256=api._hash(catalog),containment_config=config,pressure_scale=1,opt_in=True)
    assert 'expected_reviewed_pair_sha256' not in inspect.signature(GuardedIncidentV1).parameters
    assert 'expected_reviewed_pair_sha256' not in inspect.signature(routes.GuardedRoadRoutes).parameters
    assert routes._verify_reviewed_pair is host_module._PAIR_VERIFIER
    host=build()
    anchor=routes.TRUSTED_PAIR_SHA256
    assert host._identity['reviewed_pair_sha256']==host._identity['routes']['reviewed_pair_sha256']==anchor
    assert host.route_options('crew',dest)['status']=='unmapped'
    assert not host.evacuation_options('cohort','safe')['options']
    checkpoint=json.loads(json.dumps(host.checkpoint()))
    expected_situation=host.advance()
    tape=host.transcript()
    after_tick=json.loads(json.dumps(host.checkpoint()))
    assert len(tape['calls'])==1 and tape['calls'][0]['operation']=='advance'
    host.restore(checkpoint)
    fake_tape=copy.deepcopy(tape);fake_tape['identity_sha256']='0'*64
    try:host.replay(fake_tape,expected_sha256=api._hash(fake_tape),expected_identity_sha256='0'*64)
    except ValueError:pass
    else:raise AssertionError('forged replay identity accepted')
    assert host.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'])==expected_situation
    assert api._encoded(after_tick)==api._encoded(host.checkpoint())
    host.restore(checkpoint)
    def rejected(fn):
        try:fn()
        except (ValueError,RuntimeError):return
        raise AssertionError('tampering accepted')
    # Rehashing a checkpoint cannot replace either copy of the independently pinned pair.
    for field in ('top','routes'):
        altered=copy.deepcopy(checkpoint)
        if field=='top':altered['identity']['reviewed_pair_sha256']='0'*64
        else:altered['identity']['routes']['reviewed_pair_sha256']='0'*64
        altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
        rejected(lambda:host.restore(altered));host.restore(checkpoint)
    # A changed loaded verifier or fixed default is rejected before replay,
    # checkpoint and restore, even if an impostor returns the same digest.
    original=routes._verify_reviewed_pair
    with patch.object(routes,'_verify_reviewed_pair',lambda *a,**k:anchor):
        rejected(build)
        rejected(lambda:host.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256']))
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    # Even a matching replacement in the host's captured reference is rejected
    # if the verifier was loaded from a different function origin.
    def impostor(*args,**kwargs): return anchor
    with patch.object(routes,'_verify_reviewed_pair',impostor), \
         patch.object(host_module,'_PAIR_VERIFIER',impostor):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    with patch.object(original,'__defaults__',('0'*64,)):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    with patch.object(routes,'_verify_pair_against_anchor',lambda *a,**k:anchor):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    changed_manifest=copy.deepcopy(routes.REVIEWED_PAIR_MANIFEST)
    changed_manifest['graph_sha256']='0'*64
    with patch.object(routes,'REVIEWED_PAIR_MANIFEST',changed_manifest), \
         patch.object(routes,'TRUSTED_PAIR_SHA256',digest(changed_manifest)):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    with patch.dict(host._routes.identity,{'reviewed_pair_sha256':'0'*64}):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    with patch.object(routes,'__file__','/tmp/unreviewed-guard.py'):
        rejected(host.situation);rejected(lambda:host.restore(checkpoint))
    host.restore(checkpoint)
    # A caller-rehashed source subset remains unreviewed, despite a matching graph.
    subset=dict(source);subset['arcs']=source['arcs'][:-1]
    rejected(lambda:build(src=subset,src_pin=digest(subset)))
    # Hold identity embeds the same host pair and rejects a forged checkpoint.
    sites=dict(version=EVIDENCE_VERSION,role='synthetic_fixture_only',inputs_sha256=contract.document_sha256,
               graph_sha256=graph['sha256'],sites=[])
    hold=GuardedHoldV1(host,sites,expected_work_sites_sha256=api._hash(sites),opt_in=True)
    assert hold._identity['host']['reviewed_pair_sha256']==anchor
    saved=json.loads(json.dumps(hold.checkpoint()))
    bad=copy.deepcopy(saved);bad['identity']['host']['reviewed_pair_sha256']='0'*64
    bad['sha256']=api._hash({k:v for k,v in bad.items() if k!='sha256'})
    rejected(lambda:hold.restore(bad));hold.restore(saved)
    hold_situation=hold.advance();hold_tape=hold.transcript()
    hold_after=json.loads(json.dumps(hold.checkpoint()))
    assert len(hold_tape['calls'])==1 and hold_tape['calls'][0]['operation']=='advance'
    hold.restore(saved)
    assert hold.replay(hold_tape,expected_sha256=api._hash(hold_tape),
                       expected_identity_sha256=hold_tape['identity_sha256'])==hold_situation
    assert api._encoded(hold_after)==api._encoded(hold.checkpoint())
    hold.restore(saved)
    with patch.object(routes,'_verify_reviewed_pair',lambda *a,**k:anchor):
        rejected(lambda:hold.replay(hold_tape,expected_sha256=api._hash(hold_tape),
                                    expected_identity_sha256=hold_tape['identity_sha256']))
        rejected(hold.situation);rejected(lambda:hold.restore(saved))
    hold.restore(saved)
    assert api._encoded(saved)==api._encoded(hold.checkpoint())
    assert not hold.route_options('crew',dest)['options']


if __name__=='__main__':main()
