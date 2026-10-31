"""Unscored synthetic incident event/communications slice, not a full host."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from eval import incident_weather_v1 as weather
from wildfire_expanded_guarded_v1 import host as host_module

VERSION='wildfire-source-event-timeline-v1'
SUPPORTED=frozenset(('wind','access_restriction','center_unavailable','destination_closed'))


def _source():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class EventTimelineV1:
    """Trusted-driver event slice. It never moves units or population or scores.

    Inputs are independently pinned synthetic scenario data; this component does
    not validate source-catalog lineage, post anchors, or map suitability.
    """
    def __init__(self, scenario, *, expected_scenario_sha256, grid, expected_grid_sha256, opt_in=False):
        if (opt_in is not True or type(grid) is not dict
            or set(grid)!={'width','height','crs','transform'} or grid['crs']!='EPSG:25830'
            or any(type(grid.get(k)) is not int or not 1<=grid[k]<=128 for k in ('width','height'))
            or type(grid.get('transform')) is not list or len(grid['transform'])!=6
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in grid['transform'])
            or grid['transform'][0]<=0 or grid['transform'][4]>=0
            or grid['transform'][1]!=0 or grid['transform'][3]!=0
            or type(expected_grid_sha256) is not str or api._hash(grid)!=expected_grid_sha256):
            raise ValueError('explicit opt-in and independently pinned scenario grid required')
        c=copy.deepcopy(scenario)
        if (type(c) is not dict or c.get('synthetic') is not True or c.get('historical_replay') is not False
                or type(c.get('id')) is not str or type(c.get('seed')) is not int
                or c.get('sha256')!=expected_scenario_sha256
                or api._hash({k:v for k,v in c.items() if k!='sha256'})!=expected_scenario_sha256
                or type(c.get('centers')) is not list or not c['centers']
                or type(c.get('events')) is not list or type(c.get('incoming_reports',[])) is not list
                or type(c.get('resources')) is not list or type(c.get('groups')) is not list
                or type(c.get('destinations')) is not list or type(c.get('profile')) is not dict):
            raise ValueError('invalid or unpinned synthetic event scenario')
        sector=c.get('sector_grid')
        if (type(sector) is not list or len(sector)!=2 or any(type(v) is not int for v in sector)
                or not 0<=sector[0]<grid['width'] or not 0<=sector[1]<grid['height']):
            raise ValueError('invalid sector grid')
        profile=weather.WeatherProfile.from_records(c['weather_schedule'])
        if (profile.records[0].received_at!=0 or list(profile.records[0].wind)!=c['wind']
            or any(r.minute>1440 or r.received_at-r.minute>60 for r in profile.records)):
            raise ValueError('invalid weather schedule')
        events=c['events']
        if (any(type(e) is not dict or type(e.get('minute')) is not int or not 1<=e['minute']<=1440
                or type(e.get('kind')) is not str for e in events)
                or [e['minute'] for e in events]!=sorted(e['minute'] for e in events)):
            raise ValueError('invalid event sequence')
        wind_changes=[(r.minute,list(r.wind)) for prev,r in zip(profile.records,profile.records[1:]) if r.wind!=prev.wind]
        if wind_changes!=[(e['minute'],e.get('wind')) for e in events if e['kind']=='wind']:
            raise ValueError('weather and wind events differ')
        for e in events:
            kind=e['kind']
            if kind=='wind' and set(e)!={'kind','minute','wind'}:
                raise ValueError('invalid wind event')
            if kind=='access_restriction' and (set(e)!={'kind','minute','resource_kind'} or e['resource_kind'] not in ('crew','engine')):
                raise ValueError('invalid access restriction')
            if kind=='center_unavailable' and (set(e)!={'kind','minute','center_id','until'}
                or e['center_id'] not in c['centers'] or type(e['until']) is not int or not e['minute']<e['until']<=1440):
                raise ValueError('invalid center outage')
            if kind=='destination_closed' and (set(e)!={'kind','minute','destination_id'}
                or e['destination_id'] not in [d['id'] for d in c['destinations']]):
                raise ValueError('invalid destination closure')
        for r in c.get('incoming_reports',[]):
            keys={'minute','source','role','channel','location','text','delay'}
            if (type(r) is not dict or set(r) not in (keys,keys|{'category'})
                or type(r['minute']) is not int or not 0<=r['minute']<=1440
                or type(r['delay']) is not int or not 0<=r['delay']<=60
                or any(type(r[k]) is not str or not r[k] for k in ('source','role','text'))
                or r['channel'] not in ('radio','phone','routine')
                or (r['location'] is not None and (type(r['location']) is not list or len(r['location'])!=2
                    or any(type(v) is not int for v in r['location'])
                    or not 0<=r['location'][0]<grid['width'] or not 0<=r['location'][1]<grid['height']))):
                raise ValueError('invalid incoming report')
        self._case=c;self._profile=profile;self._grid=copy.deepcopy(grid);self._grid_identity=expected_grid_sha256
        self._source=_source();self._host_sources=host_module._sources()
        self._identity=dict(version=VERSION,source=self._source,dependencies=self._host_sources,
                            scenario=expected_scenario_sha256,grid_identity=expected_grid_sha256)
        self._ops=Operations([],c['destinations'],c['groups'],c['profile'])
        self._bus=EventBus(c['seed'],c['centers'])
        self._minute=0;self._known_dest=copy.deepcopy(self._ops.destinations)
        self._known_wind=list(profile.records[0].wind);self._known_restrictions=set()
        self._restrictions=set();self._closed={};self._seen=0;self._calls=[]
        self._recording=True;self._poisoned=False
        if any(type(r) is not dict or type(r.get('kind')) is not str for r in c['resources']):
            raise ValueError('invalid resource roster')
        unsupported=sorted({r['kind'] for r in c['resources'] if r['kind'] not in ('crew','engine')})
        self._status='unsupported' if unsupported else 'active'
        self._reason=('aircraft_roster_not_implemented' if 'aircraft' in unsupported else
                      'unsupported_resource_kind:'+unsupported[0] if unsupported else None)
        self._weather_reports(0);self._incoming(0);self._bus.tick(0,{})
        self._sync()

    def _ensure(self):
        if self._poisoned:raise RuntimeError('event timeline indeterminate; restore checkpoint')
        if (self._source!=_source() or self._host_sources!=host_module._sources()
                or self._identity['scenario']!=api._hash({k:v for k,v in self._case.items() if k!='sha256'})
                or self._grid_identity!=api._hash(self._grid)):
            self._poisoned=True;raise RuntimeError('timeline identity changed')

    def _weather_reports(self,minute):
        for record in self._profile.records:
            if record.minute==minute:
                self._bus.emit('weather','weather_update',dict(wind=list(record.wind),
                    temperature_c=record.temperature_c,relative_humidity_pct=record.relative_humidity_pct,
                    rain_mm_per_hour=record.rain_mm_per_hour,text='Synthetic exercise weather.'),minute,
                    delay=record.received_at-record.minute)

    def _incoming(self,minute):
        for item in self._case.get('incoming_reports',[]):
            if item['minute']==minute:
                self._bus.emit(item['source'],'incoming_report',{'text':item['text'],'coordinate_frame':'original_scenario_grid'},
                    minute,role=item['role'],channel=item['channel'],location=copy.deepcopy(item['location']),delay=item['delay'])

    def _sync(self):
        for record in self._bus.reports[self._seen:]:
            if record['kind']=='weather_update':
                previous=getattr(self,'_wind_at',-1)
                if record['observed_at']>previous:
                    self._known_wind=copy.deepcopy(record['payload']['wind']);self._wind_at=record['observed_at']
            elif record['kind']=='access_restriction':self._known_restrictions.add(record['payload']['resource_kind'])
            elif record['kind']=='destination_threatened':
                d=record['payload']['destination'];self._known_dest[d['id']]=copy.deepcopy(d)
        self._seen=len(self._bus.reports)

    def situation(self):
        self._ensure()
        return dict(version=VERSION,status=self._status,minute=self._minute,reason=self._reason,
            mode='unscored_event_slice_no_fire_or_resource_operations',
            wind=copy.deepcopy(self._known_wind),destinations=copy.deepcopy(list(self._known_dest.values())),
            restrictions=sorted(self._known_restrictions),reports_cursor=len(self._bus.reports))

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure();return self._bus.get_reports(after_cursor=after_cursor,limit=limit)

    def private_excluded_cells(self,mode):
        self._ensure()
        if self._status!='active':raise RuntimeError('stopped timeline cannot authorize routing')
        if mode not in ('crew','engine'):raise ValueError('invalid restriction request')
        width,height=self._grid['width'],self._grid['height']
        cells=[]
        if mode in self._restrictions:
            x,y=self._case['sector_grid']
            cells=[[a,b] for b in range(height) for a in range(width) if math.dist((a,b),(x,y))<=5]
        return dict(grid_identity=self._grid_identity,cells=cells)

    def _tick(self):
        due=[e for e in self._case['events'] if e['minute']==self._minute+1]
        unsupported=[e for e in due if e['kind'] not in SUPPORTED]
        if unsupported:
            self._status='unsupported';self._reason='unsupported_scheduled_event:'+unsupported[0]['kind'];return
        self._minute+=1
        for e in due:
            if e['kind']=='access_restriction':
                self._restrictions.add(e['resource_kind'])
                self._bus.emit('road-patrol','access_restriction',dict(resource_kind=e['resource_kind'],
                    text='Synthetic central-sector vehicle restriction.'),self._minute,urgent=True)
            elif e['kind']=='center_unavailable':self._closed[e['center_id']]=e['until']
            elif e['kind']=='destination_closed':
                dest=self._ops.destinations[e['destination_id']];dest['status']='closed'
                if self._case['profile'].get('closure_affects_occupants'):
                    for group in self._ops.groups.values():
                        if group.get('destination_id')==dest['id'] and group['position']==dest['position']:
                            group['status']='blocked'
                            self._bus.emit(dest['id'],'relocation_required',dict(group_id=group['id'],destination_id=dest['id']),
                                           self._minute,urgent=True)
                self._bus.emit('destination','destination_threatened',dict(destination=copy.deepcopy(dest)),self._minute,urgent=True)
        self._weather_reports(self._minute);self._incoming(self._minute)
        views={center:({'available':False} if self._closed.get(center,0)>self._minute else
                       {'situation':{'observed_at':self._minute,'text':'Synthetic event timeline'}})
               for center in self._case['centers']}
        self._bus.tick(self._minute,views);self._sync()
        if self._minute==1440:self._status='end_unscored'

    def advance(self,minutes=1):
        self._ensure()
        if self._status!='active':raise RuntimeError('timeline cannot advance')
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        try:
            for _ in range(minutes):
                self._tick()
                if self._status!='active':break
            result=self.situation()
            if self._recording:self._calls.append(dict(operation='advance',args=[minutes],result=copy.deepcopy(result)))
            return result
        except BaseException:self._poisoned=True;raise

    def request_status(self,center_id):
        self._ensure()
        if self._status!='active':raise RuntimeError('timeline cannot advance')
        if center_id not in self._case['centers']:raise ValueError('unknown center')
        try:
            request=self._bus.request_status(center_id,'situation',self._minute);self._tick()
            result=dict(request_id=request,status='submitted') if self._status=='active' else self.situation()
            if self._recording:self._calls.append(dict(operation='request_status',args=[center_id],result=copy.deepcopy(result)))
            return result
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        self._ensure()
        return dict(version=VERSION,identity_sha256=api._hash(self._identity),calls=copy.deepcopy(self._calls))

    def replay(self,transcript,*,expected_sha256,expected_identity_sha256):
        self._ensure()
        if self._minute!=0 or self._calls or self._status!='active':raise ValueError('fresh active timeline required')
        if (type(transcript) is not dict or set(transcript)!={'version','identity_sha256','calls'}
            or transcript['version']!=VERSION or api._hash(transcript)!=expected_sha256
            or transcript['identity_sha256']!=expected_identity_sha256
            or expected_identity_sha256!=api._hash(self._identity) or type(transcript['calls']) is not list):
            raise ValueError('untrusted timeline transcript or identity')
        self._recording=False
        try:
            for entry in transcript['calls']:
                if (type(entry) is not dict or set(entry)!={'operation','args','result'}
                    or entry['operation'] not in ('advance','request_status') or type(entry['args']) is not list):
                    raise ValueError('invalid timeline transcript call')
                result=getattr(self,entry['operation'])(*entry['args'])
                if api._encoded(result)!=api._encoded(entry['result']):raise ValueError('timeline replay differs')
            self._calls=copy.deepcopy(transcript['calls'])
            return self.situation()
        except BaseException:self._poisoned=True;raise
        finally:self._recording=True

    def checkpoint(self):
        self._ensure()
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),minute=self._minute,status=self._status,
            reason=self._reason,operations=self._ops.checkpoint(),communications=self._bus.checkpoint(),
            known_dest=copy.deepcopy(self._known_dest),known_wind=copy.deepcopy(self._known_wind),
            wind_at=getattr(self,'_wind_at',-1),known_restrictions=sorted(self._known_restrictions),
            restrictions=sorted(self._restrictions),closed=copy.deepcopy(self._closed),seen=self._seen,
            calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value)
        return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        """Restore only against a separately trusted digest of the entire checkpoint."""
        try:
            v=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(v)!=expected_checkpoint_sha256):
                raise ValueError('independently trusted checkpoint digest differs')
            keys={'version','identity','minute','status','reason','operations','communications','known_dest',
                  'known_wind','wind_at','known_restrictions','restrictions','closed','seen','calls','sha256'}
            if (type(v) is not dict or set(v)!=keys or v['version']!=VERSION
                or api._encoded(v['identity'])!=api._encoded(self._identity) or _source()!=self._source
                or self._host_sources!=host_module._sources()
                or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                or type(v['minute']) is not int or not 0<=v['minute']<=1440
                or v['status'] not in ('active','unsupported','end_unscored')
                or (v['status']=='end_unscored')!=(v['minute']==1440)
                or type(v['calls']) is not list):raise ValueError('timeline checkpoint identity or clock differs')
            ops=Operations.restore(v['operations']);bus=EventBus.restore(v['communications'])
            original_groups={g['id']:g for g in self._case['groups']}
            original_dest={d['id']:d for d in self._case['destinations']}
            if (ops.profile!=self._case['profile'] or ops.resources or set(ops.groups)!=set(original_groups)
                or set(ops.destinations)!=set(original_dest) or set(v['known_dest'])!=set(original_dest)
                or any(ops.groups[k]['count']!=g['count'] or ops.groups[k]['position']!=g['position']
                       for k,g in original_groups.items())
                or bus.seed!=self._case['seed'] or bus._last_tick!=v['minute'] or v['seen']!=len(bus.reports)
                or type(v['wind_at']) is not int or not -1<=v['wind_at']<=v['minute']
                or type(v['closed']) is not dict or not set(v['closed'])<=set(self._case['centers'])
                or type(v['restrictions']) is not list or not set(v['restrictions'])<={'crew','engine'}
                or type(v['known_restrictions']) is not list or not set(v['known_restrictions'])<=set(v['restrictions'])):
                raise ValueError('timeline state or clock differs')
            self._ops,self._bus=ops,bus
            self._minute,self._status,self._reason=v['minute'],v['status'],v['reason']
            self._known_dest,self._known_wind,self._wind_at=v['known_dest'],v['known_wind'],v['wind_at']
            self._known_restrictions,self._restrictions=set(v['known_restrictions']),set(v['restrictions'])
            self._closed,self._seen,self._calls=v['closed'],v['seen'],v['calls']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('timeline restore differs')
        except BaseException:self._poisoned=True;raise
