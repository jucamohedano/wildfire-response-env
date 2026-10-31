"""Opt-in synthetic walk-front lifecycle. This is not operational access."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from eval import expanded_crew_walk_front_v1 as walk
from eval import expanded_road_segment_exercise_v1 as segment
from eval import expanded_road_staging_v1 as staging
from eval import incident_weather_v1 as weather
from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1

VERSION='wildfire-source-walk-exercise-v2'
SCOPE='synthetic_predeployed_walk_no_ground_work_no_evacuation'
SPEED_M_PER_MINUTE=60.0
_ALLOWED_CASE_INDEX=4
_ROOT=Path(__file__).resolve().parents[2]
_WALK_PLAN=walk.plan
_LOAD_WATER=segment.load_water_sources
_BOUND_WALK=(walk.plan,walk.hazards_and_front,walk.water_clear,walk.supported_land,walk.cell_of,walk.center)
_CANDIDATE=object()


def _source_identity():
    files=[Path(__file__),Path(walk.__file__),Path(segment.__file__),Path(staging.__file__)]
    expected=[_ROOT/'envs/wildfire_source_walk_exercise_v1/__init__.py',
              _ROOT/'eval/expanded_crew_walk_front_v1.py',_ROOT/'eval/expanded_road_segment_exercise_v1.py',
              _ROOT/'eval/expanded_road_staging_v1.py']
    if (any(a.resolve()!=b.resolve() for a,b in zip(files,expected)) or (walk.plan,walk.hazards_and_front,walk.water_clear,walk.supported_land,walk.cell_of,walk.center)!=_BOUND_WALK
            or walk.plan is not _WALK_PLAN or segment.load_water_sources is not _LOAD_WATER
            or (walk.MAX_WALK_M,walk.MAX_GRADE,walk.WATER_BUFFER_M,walk.FRONT_STANDOFF_M)!=(2000.,.30,5.,10.)):
        raise RuntimeError('walk evidence implementation binding differs')
    return api._hash({str(p.relative_to(_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def _document(document,expected):
    if (type(document) is not dict or type(expected) is not str or len(expected)!=64
            or document.get('sha256')!=expected
            or api._hash({k:v for k,v in document.items() if k!='sha256'})!=expected):
        raise ValueError('independently approved document identity differs')


def _geometry_identity(water):
    return api._hash(dict(osm=sorted(g.wkb_hex for g in water['osm'].geometries),
        dera=sorted(g.wkb_hex for g in water['dera'].geometries),
        land=water['land'].wkb_hex,bounds=water['bounds'].wkb_hex))


def _eligible_fire(fire,wind,supported):
    if not fire:return set(),{}
    return walk.hazards_and_front(fire,wind,supported)


def _segment_safe(a,b,*,grid,heights,supported,hazards,water):
    first,last=walk.cell_of(a,grid),walk.cell_of(b,grid)
    if (first not in supported or last not in supported or first in hazards or last in hazards
            or abs(first[0]-last[0])+abs(first[1]-last[1])>1):return False
    if first!=last:
        h1,h2=heights[first[1]][first[0]],heights[last[1]][last[0]]
        if any(type(h) not in (int,float) or not math.isfinite(h) for h in (h1,h2)):
            return False
        if abs(h1-h2)/math.dist(walk.center(first,grid),walk.center(last,grid))>walk.MAX_GRADE:return False
    return walk.water_clear(a,b,water)


class SourceWalkExerciseV1:
    """Trusted wrapper for one approved, predeployed synthetic candidate.

    Only walk and retreat are modeled. There is no suppression, road return,
    evacuation, score or claim that the pre-handover route was traversable.
    """
    def __init__(self,host,*,candidate,expected_candidate_sha256,parent,v2,v4,
                 candidate_index,public_crew_id,expected_identity,opt_in=False):
        if opt_in is not True or type(host) is not RestrictedSourceIncidentV1:
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
        supported=walk.supported_land(v2,v4);heights=v2['terrain_candidate']
        for x,y in supported:
            if (contract.surface[y][x]!='land' or not contract.fire_support[y][x]
                    or heights[y][x]!=contract.elevation[y][x]):
                raise ValueError('walk and fire terrain differ')
        water=segment.load_water_sources();source=_source_identity()
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
            speed_m_per_minute=SPEED_M_PER_MINUTE)
        if expected_identity is not _CANDIDATE and (type(expected_identity) is not dict or api._encoded(expected_identity)!=api._encoded(self._identity)):
            raise ValueError('independently approved walk integration identity differs')
        self._ground=dict(phase='staged_assumed',position=copy.deepcopy(self._initial),next_index=1,trail=[],
            duty_end_at=budget['duty_end_at'],work_remaining=budget['work_remaining'],
            total_work_remaining=budget['total_work_remaining'])
        self._calls=[];self._poisoned=False;self._seal=api._hash(host.checkpoint());self._ground_seal=api._hash(self._ground)

    def _configuration_sha256(self):
        return api._hash(dict(grid=self._grid,heights=self._heights,
            supported=[list(p) for p in sorted(self._supported)],proposal=self._proposal,
            initial=self._initial,route=self._route))

    def _ensure(self):
        if self._poisoned:raise RuntimeError('walk host indeterminate')
        try:
            self._host._ensure()
            if (self._host._weather._fire is not self._host._fire
                    or _source_identity()!=self._identity['implementation'] or _geometry_identity(self._water)!=self._identity['water']
                    or api._hash(self._host.checkpoint())!=self._seal
                    or api._hash(self._ground)!=self._ground_seal
                    or self._configuration_sha256()!=self._identity['walk_configuration']
                    or self._candidate.get('sha256')!=self._candidate_sha
                    or api._hash({k:v for k,v in self._candidate.items() if k!='sha256'})!=self._candidate_sha):
                raise RuntimeError('walk identity or host state changed')
        except BaseException:self._poisoned=True;raise

    def _conditions(self,minute):
        wind=weather.conditions(self._host._weather._profile,minute)['wind']
        fire=self._host._fire.burning()
        return _eligible_fire(fire,wind,self._supported)

    def _safe(self,a,b,hazards):
        return _segment_safe(a,b,grid=self._grid,heights=self._heights,
                             supported=self._supported,hazards=hazards,water=self._water)

    def _front_valid(self,front):
        return tuple(self._proposal['front_cell']) in front.get(tuple(self._proposal['site_cell']),())

    def _ground_tick(self,ground,minute,hazards,front,command):
        g=copy.deepcopy(ground);before=g['phase']
        if command=='dispatch' and g['phase']=='staged_assumed':g['phase']='walking'
        elif command=='retreat' and g['phase'] in ('walking','front_no_work'):g['phase']='retreating'
        if g['phase']=='staged_assumed' and not self._safe(g['position'],g['position'],hazards):
            g['phase']='needs_assistance'
        if g['phase'] not in ('walking','front_no_work','retreating'):return g,before!=g['phase']
        if (minute>=g['duty_end_at'] or g['work_remaining']<=0 or g['total_work_remaining']<=0
                or not self._safe(g['position'],g['position'],hazards)):
            g['phase']='needs_assistance';return g,True
        if g['phase'] in ('walking','front_no_work') and not self._front_valid(front):g['phase']='retreating'
        if g['phase']=='walking':
            remaining=[g['position']]+self._route[g['next_index']:]
            if any(not self._safe(a,b,hazards) for a,b in zip(remaining,remaining[1:])):
                g['phase']='retreating' if g['trail'] else 'needs_assistance'
        target=None
        if g['phase']=='walking':
            if g['next_index']>=len(self._route):g['phase']='front_no_work'
            else:target=self._route[g['next_index']]
        if g['phase']=='retreating':
            if not g['trail']:g['phase']='needs_assistance'
            else:target=g['trail'][-1]
        if target is not None:
            distance=math.dist(g['position'],target)
            fraction=min(1.,SPEED_M_PER_MINUTE/distance) if distance else 1.
            nxt=[g['position'][i]+(target[i]-g['position'][i])*fraction for i in range(2)]
            if not self._safe(g['position'],nxt,hazards):
                if g['phase']=='walking' and g['trail']:g['phase']='retreating'
                else:g['phase']='needs_assistance'
            else:
                if g['phase']=='walking':g['trail'].append(copy.deepcopy(g['position']))
                g['position']=nxt
                if fraction==1.:
                    if g['phase']=='walking':
                        g['next_index']+=1
                        if g['next_index']==len(self._route):g['phase']='front_no_work'
                    else:
                        g['trail'].pop()
                        if not g['trail']:g['phase']='needs_assistance'
        g['work_remaining']-=1;g['total_work_remaining']-=1
        return g,before!=g['phase']

    def situation(self):
        self._ensure();view=self._host.situation()
        if view['status']=='censored':return view
        phase='staged_assumed';latest=None
        for record in self._host._bus.reports:
            if record['kind']=='synthetic_ground_status' and record['payload'].get('crew_id')==self._public_id:
                # Observation time takes precedence over arrival order; event
                # ID breaks same-minute ties independently of delivery order.
                key=(record['observed_at'],record['id'])
                if latest is None or key>latest:
                    latest=key;phase=record['payload']['phase']
        view.update(version=VERSION,scope=SCOPE,ground=[{'id':self._public_id,'phase':phase}],
                    limitations=['synthetic predeployment','no suppression','no verified road return','no evacuation','unscored'])
        return view

    def reports(self,*,after_cursor=0,limit=20):
        self._ensure();return self._host.reports(after_cursor=after_cursor,limit=limit)

    def _run(self,name,args,command=None):
        self._ensure();prior_calls=len(self._calls)
        try:
            minute=self._host._minute+1
            # The private phase affects the queued result only. Acceptance is
            # never disclosed synchronously to the actor.
            accepted=(self._ground['phase']=='staged_assumed' if command=='dispatch' else
                      self._ground['phase'] in ('walking','front_no_work') if command=='retreat' else None)
            hazards,front=self._conditions(minute)
            ground,changed=self._ground_tick(self._ground,minute,hazards,front,command)
            if name=='advance':result=self._host.advance(1)
            elif name=='clear_aircraft':result=self._host.clear_aircraft(*args)
            elif name=='release':result=self._host.release(*args)
            elif name=='request_status':result=self._host.request_status(*args)
            elif name=='dispatch_aircraft':
                options=self._host.route_options(*args)['options']
                if not options:raise ValueError('no delivered fire target')
                result=self._host.dispatch(*args,options[0]['id'])
            else:raise ValueError('unsupported exercise command')
            if self._host._minute==minute:
                self._ground=ground
                if self._host._status!='censored':
                    post_hazards,post_front=self._conditions(minute)
                    if not self._safe(ground['position'],ground['position'],post_hazards):
                        ground['phase']='needs_assistance';changed=True
                    elif ground['phase'] in ('walking','front_no_work') and not self._front_valid(post_front):
                        ground['phase']='retreating';changed=True
                    if changed:self._host._bus.emit('operations','synthetic_ground_status',
                        {'crew_id':self._public_id,'phase':ground['phase']},minute,role='responder')
                    if command is not None:self._host._bus.emit('operations','synthetic_ground_command_result',
                        {'crew_id':self._public_id,'command':command,'accepted':accepted},minute,role='responder')
            self._calls.append(dict(name=name,args=copy.deepcopy(args),command=command,result=copy.deepcopy(result)))
            self._seal=api._hash(self._host.checkpoint());self._ground_seal=api._hash(self._ground)
            return self.situation() if name=='advance' else result
        except BaseException as error:
            try:
                unchanged=(isinstance(error,Exception) and api._hash(self._host.checkpoint())==self._seal
                           and api._hash(self._ground)==self._ground_seal and len(self._calls)==prior_calls)
            except BaseException:unchanged=False
            if not unchanged:self._poisoned=True
            raise

    def advance(self,minutes=1):
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            value=self._run('advance',[])
            if self._host._status!='active':break
        return value

    def _require_commandable(self,crew_id,allowed):
        self._ensure()
        view=self.situation()
        if (crew_id!=self._public_id or view.get('status')!='active' or 'ground' not in view
                or view['ground'][0]['phase'] not in allowed):
            raise ValueError('crew status unavailable or awaiting report')

    def dispatch_crew(self,crew_id):
        self._require_commandable(crew_id,('staged_assumed',))
        return self._run('advance',[],command='dispatch')

    def retreat(self,crew_id):
        self._require_commandable(crew_id,('walking','front_no_work'))
        return self._run('advance',[],command='retreat')

    def clear_aircraft(self,resource_id):return self._run('clear_aircraft',[resource_id])
    def dispatch_aircraft(self,resource_id,target):return self._run('dispatch_aircraft',[resource_id,target])
    def release(self,resource_id):return self._run('release',[resource_id])
    def request_status(self,center_id):return self._run('request_status',[center_id])

    def checkpoint(self):
        self._ensure();value=dict(version=VERSION,identity=copy.deepcopy(self._identity),host=self._host.checkpoint(),
                                  ground=copy.deepcopy(self._ground),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value);return value


    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or set(value)!={'version','identity','host','ground','calls','sha256'}
                    or value['version']!=VERSION or value['identity']!=self._identity
                    or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                    or _source_identity()!=self._identity['implementation']
                    or _geometry_identity(self._water)!=self._identity['water']):
                raise ValueError('trusted walk checkpoint differs')
            g=value['ground']
            if (type(g) is not dict or set(g)!={'phase','position','next_index','trail','duty_end_at','work_remaining','total_work_remaining'}
                    or g['phase'] not in ('staged_assumed','walking','front_no_work','retreating','needs_assistance')
                    or type(g['next_index']) is not int or not 1<=g['next_index']<=len(self._route)
                    or any(type(g[k]) is not int or g[k]<0 for k in ('duty_end_at','work_remaining','total_work_remaining'))
                    or type(g['trail']) is not list or type(value['calls']) is not list):
                raise ValueError('invalid walk checkpoint state')
            for p in [g['position']]+g['trail']:
                if (type(p) is not list or len(p)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in p)):
                    raise ValueError('invalid walk checkpoint position')
            self._host.restore(value['host'],expected_checkpoint_sha256=api._hash(value['host']))
            self._ground,self._calls=g,value['calls'];self._seal=api._hash(self._host.checkpoint())
            self._ground_seal=api._hash(g);self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('walk continuation differs')
        except BaseException:self._poisoned=True;raise

    def replay(self,transcript,*,expected_sha256,expected_final_checkpoint_sha256):
        self._ensure()
        if self._host._minute!=0 or self._calls:raise ValueError('fresh walk handover required')
        if (type(transcript) is not dict or set(transcript)!={'version','identity','calls'}
                or transcript['version']!=VERSION or transcript['identity']!=self._identity
                or api._hash(transcript)!=expected_sha256 or type(transcript['calls']) is not list):
            raise ValueError('trusted walk transcript differs')
        try:
            for call in transcript['calls']:
                if (type(call) is not dict or set(call)!={'name','args','command','result'}
                        or call['name'] not in ('advance','clear_aircraft','dispatch_aircraft','release','request_status')
                        or type(call['args']) is not list or call['command'] not in (None,'dispatch','retreat')
                        or (call['command'] is not None and call['name']!='advance')):
                    raise ValueError('invalid walk replay call')
                self._run(call['name'],call['args'],call['command'])
                if api._encoded(self._calls[-1])!=api._encoded(call):raise ValueError('walk replay response differs')
            if api._hash(self.checkpoint())!=expected_final_checkpoint_sha256:raise ValueError('walk replay checkpoint differs')
            return self.situation()
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        self._ensure();return dict(version=VERSION,identity=copy.deepcopy(self._identity),calls=copy.deepcopy(self._calls))


def configuration_identity(host,**kwargs):
    """Produce an unapproved candidate identity for independent review."""
    if 'expected_identity' in kwargs:raise ValueError('candidate takes no expected identity')
    return copy.deepcopy(SourceWalkExerciseV1(host,expected_identity=_CANDIDATE,**kwargs)._identity)
