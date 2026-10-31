"""Narrow domain-profile adapter for the existing source-walk lifecycle."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from eval import expanded_crew_walk_front_v1 as walk
from eval import expanded_road_segment_exercise_v1 as segment
from eval import expanded_road_staging_v1 as staging
from wildfire_source_walk_exercise_v1 import (SourceWalkExerciseV1, _document, _geometry_identity,
                                               SPEED_M_PER_MINUTE, _ALLOWED_CASE_INDEX)
import wildfire_source_walk_exercise_v1 as original
from .host import DomainProfileGroundIncidentV1

VERSION='wildfire-domain-source-walk-exercise-v1'
SCOPE='domain_synthetic_predeployed_walk_no_ground_work_no_evacuation'
_ROOT=Path(__file__).resolve().parents[2]
_CANDIDATE=object()
_eligible_fire=original._eligible_fire
_segment_safe=original._segment_safe


def _bridge_source():
    path=_ROOT/'envs/wildfire_source_domain_ground_v1/walk.py'
    if Path(__file__).resolve()!=path:raise RuntimeError('domain walk origin differs')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_supported(v2,v4,contract,masks):
    """Only original source-backed land, never estimated/mixed/unknown cells."""
    heights=v2['terrain_candidate']
    return {(x,y) for x,y in walk.supported_land(v2,v4)
        if contract.domain[y][x] and contract.surface[y][x]=='land' and contract.fire_support[y][x]
        and masks.source_mask[y][x] is True and masks.estimated_mask[y][x] is False
        and contract.fuel[y][x]==v2['cells'][y][x]['source_fuel_class']
        and heights[y][x]==contract.elevation[y][x]}


class DomainSourceWalkV1(original.SourceWalkExerciseV1):
    """Trusted wrapper for one approved, predeployed synthetic candidate.

    Only walk and retreat are modeled. There is no suppression, road return,
    evacuation, score or claim that the pre-handover route was traversable.
    """
    def __init__(self,host,*,candidate,expected_candidate_sha256,parent,v2,v4,
                 candidate_index,public_crew_id,expected_identity,opt_in=False):
        if opt_in is not True or type(host) is not DomainProfileGroundIncidentV1:
            raise ValueError('explicit opt-in and exact restricted host required')
        host._ensure()
        if host._minute!=0 or host._calls or host._status!='active':raise ValueError('active fresh handover required')
        _document(candidate,expected_candidate_sha256);_document(parent,walk.PARENT_PIN)
        _document(v2,staging.V2_PIN);_document(v4,staging.V4_PIN)
        if (candidate.get('version')!=walk.VERSION or candidate.get('status')!='private_synthetic_hypotheses_not_host_integrated'
                or candidate.get('pins')!={'parent':walk.PARENT_PIN,'source':hashlib.sha256(Path(walk.__file__).read_bytes()).hexdigest(),
                                           'water_v2':staging.V2_PIN,'mixed_v4':staging.V4_PIN}
                or type(candidate.get('cases')) is not list or len(candidate['cases'])!=12
                or type(parent.get('cases')) is not list or len(parent['cases'])!=12
                or type(public_crew_id) is not str or not public_crew_id
                or type(candidate_index) is not int or candidate_index not in (0,1)):
            raise ValueError('approved walk candidate contract differs')
        live=[(case,item) for case in candidate['cases'] for item in case['candidates']
              if item.get('status')=='synthetic_walk_front_hypothesis']
        if (len(live)!=2 or len({c['original_case_id'] for c,_ in live})!=1
                or any(a['original_case_id']!=b['original_case_id'] for a,b in zip(candidate['cases'],parent['cases']))
                or live[0][0]['original_case_id']!=parent['cases'][_ALLOWED_CASE_INDEX]['original_case_id']):
            raise ValueError('approved single-case walk scope differs')
        selected_case,proposal=live[candidate_index]
        if selected_case['original_case_id']!=host._case['id']:
            raise ValueError('walk hypothesis does not authorize this case')
        parents=[p for c in parent['cases'] if c['original_case_id']==host._case['id'] for p in c['variants']
                 if p['sha256']==proposal['parent_sha256']]
        if len(parents)!=1:raise ValueError('walk candidate parent differs')
        variant=parents[0]
        if (api._hash({k:v for k,v in variant.items() if k!='sha256'})!=variant['sha256']
                or variant.get('resource_kind')!='crew' or variant.get('work_authorization')!='none'
                or variant.get('pre_handover_route')!='unknown' or variant.get('original_case_sha256')!=host._case['sha256']
                or variant.get('fire_draft_sha256')!=host._firecase['sha256']
                or proposal.get('work_authorization')!='none' or proposal.get('return_walk')!='requires_revalidation'
                or proposal.get('road_return_to_base')!='unknown'):
            raise ValueError('walk candidate has unsupported scope')
        identity=dict(host._inputs.identity)
        if identity.get('water_v2')!=staging.V2_PIN or identity.get('overlay_v4')!=staging.V4_PIN:
            raise ValueError('source fire input and walk evidence differ')
        grid=v2['grid'];contract=host._inputs.contract
        if (grid['width']!=contract.width or grid['height']!=contract.height or grid['crs']!=contract.crs
                or tuple(grid['transform'])!=contract.transform):raise ValueError('walk grid differs')
        # Domain assumptions never grant walking access. Admit only the
        # intersection with independently source-supported land; estimated,
        # mixed and unknown cells remain outside the walk policy.
        heights=v2['terrain_candidate']
        supported=_source_supported(v2,v4,contract,host._domain_inputs)
        water=segment.load_water_sources();source=original._source_identity()
        wind=host.situation()['wind']
        if wind is None:raise ValueError('delivered handover wind required')
        rebuilt=walk.plan(variant['initial_position'],host._handover_fire,wind,supported,heights,grid,water)
        rebuilt['parent_sha256']=variant['sha256']
        if api._encoded(rebuilt)!=api._encoded(proposal):
            raise ValueError('walk hypothesis does not match this handover')
        budget=variant['resource_budget']
        if (type(budget) is not dict or any(type(budget.get(k)) is not int or budget[k]<=0
                 for k in ('duty_end_at','work_remaining','total_work_remaining'))
                or budget.get('water_litres')!=0 or budget.get('capacity_litres')!=0):
            raise ValueError('finite crew duty and work budgets required')
        self._host=host;self._candidate=copy.deepcopy(candidate);self._parent=copy.deepcopy(parent)
        self._candidate_sha=expected_candidate_sha256;self._grid=copy.deepcopy(grid);self._heights=copy.deepcopy(heights)
        self._supported=frozenset(supported);self._water=water;self._proposal=copy.deepcopy(proposal)
        self._initial=copy.deepcopy(variant['initial_position']);self._public_id=public_crew_id
        self._route=[self._initial]+[list(walk.center(tuple(c),grid)) for c in proposal['walk_cells']]+[proposal['site_position']]
        self._route=[p for i,p in enumerate(self._route) if not i or p!=self._route[i-1]]
        self._identity=dict(version=VERSION,scope=SCOPE,implementation=source,host=copy.deepcopy(host._identity),
            handover=api._hash(host.checkpoint()),candidate=expected_candidate_sha256,parent=walk.PARENT_PIN,
            variant=variant['sha256'],water=_geometry_identity(water),crew_id=public_crew_id,
            walk_configuration=self._configuration_sha256(),
            speed_m_per_minute=SPEED_M_PER_MINUTE,bridge_source=_bridge_source())
        if expected_identity is not _CANDIDATE and (type(expected_identity) is not dict or api._encoded(expected_identity)!=api._encoded(self._identity)):
            raise ValueError('independently approved walk integration identity differs')
        self._ground=dict(phase='staged_assumed',position=copy.deepcopy(self._initial),next_index=1,trail=[],
            duty_end_at=budget['duty_end_at'],work_remaining=budget['work_remaining'],
            total_work_remaining=budget['total_work_remaining'])
        self._calls=[];self._poisoned=False;self._seal=api._hash(host.checkpoint());self._ground_seal=api._hash(self._ground)
        self._restoring=False

    def _ensure(self):
        super()._ensure()
        if self._identity.get('bridge_source')!=_bridge_source():
            self._poisoned=True;raise RuntimeError('domain walk adapter changed')

    def situation(self):
        value=super().situation();value['version']=VERSION
        if value['status']!='censored':value['scope']=SCOPE
        return value

    def checkpoint(self):
        value=super().checkpoint()
        if not self._restoring:
            value['version']=VERSION;value['sha256']=api._hash({k:v for k,v in value.items() if k!='sha256'})
        return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or value.get('version')!=VERSION
                    or value.get('sha256')!=api._hash({k:v for k,v in value.items() if k!='sha256'})):
                raise ValueError('trusted domain walk checkpoint differs')
            base=copy.deepcopy(value);base['version']=original.VERSION
            base['sha256']=api._hash({k:v for k,v in base.items() if k!='sha256'})
            self._restoring=True
            try:super().restore(base,expected_checkpoint_sha256=api._hash(base))
            finally:self._restoring=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('domain walk restore differs')
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        value=super().transcript();value['version']=VERSION;return value

    def replay(self,transcript,*,expected_sha256,expected_final_checkpoint_sha256):
        if (type(transcript) is not dict or transcript.get('version')!=VERSION
                or api._hash(transcript)!=expected_sha256):raise ValueError('trusted domain walk transcript differs')
        base=copy.deepcopy(transcript);base['version']=original.VERSION
        return super().replay(base,expected_sha256=api._hash(base),
                              expected_final_checkpoint_sha256=expected_final_checkpoint_sha256)


def configuration_identity(host,**kwargs):
    """Candidate only; independent approval is required before any run."""
    if 'expected_identity' in kwargs:raise ValueError('candidate takes no expected identity')
    return copy.deepcopy(DomainSourceWalkV1(host,expected_identity=_CANDIDATE,**kwargs)._identity)
