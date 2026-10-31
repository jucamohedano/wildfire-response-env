"""Unscored synthetic shared-lifecycle exercise; never accepts source cases."""
import copy
import hashlib
import math
from datetime import datetime, timedelta
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations, _same_position
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day import clock as clock_module
from envs.wildfire_response.incident_day.clock import solar_daylight
from eval import incident_weather_v1 as weather
from wildfire_core.mobility_routing import RoadNetwork, make_journey, make_leg, journey_position
from wildfire_containment_v1.kernel import _edge, _point
from wildfire_expanded_full_v1 import hold_fire as hold_module
from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1, source_sha256 as hold_sha
from wildfire_expanded_guarded_v1 import host as pins
from wildfire_expanded_incident_v1.weather_fire_v1 import WeatherMixedFireDiagnosticV1

VERSION = 'wildfire-shared-synthetic-fixture-v1'
PROFILE = dict(mobilization_minutes=1, ground_setup_minutes=5, aircraft_setup_minutes=2,
    duty_minutes=240, rest_minutes=60, engine_litres_per_minute=500, revalidate_setup_safety=True,
    order_delivery_minutes=2, order_ack_minutes=2, order_execution_minutes=2,
    air_refill_minutes=15, ground_refill_minutes=10, aircraft_max_wind=25)


def _sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _view(unit):
    keys = ('id', 'kind', 'position', 'base_position', 'state', 'water_litres',
            'capacity_litres', 'work_remaining', 'available_at')
    return {k: copy.deepcopy(unit[k]) for k in keys}


def _group(group):
    return {k: copy.deepcopy(group[k]) for k in ('id', 'count', 'position', 'status', 'exposed')}


class SharedSyntheticIncidentV1:
    """Owns one weather/fire model, Operations and EventBus. Private driver only."""
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id, fixture,
                 expected_fixture_sha256, expected_road_sha256, expected_work_sites_sha256,
                 containment_config, pressure_scale, opt_in=False):
        if opt_in is not True or type(fixture) is not dict or api._hash(fixture) != expected_fixture_sha256:
            raise ValueError('explicit opt-in and independent synthetic fixture pin required')
        f = copy.deepcopy(fixture)
        required = {'version','role','case_id','inputs_sha256','road','road_sha256','allowed_edges',
                    'work_sites','resources','groups','destinations','centers','events','incoming_reports',
                    'start_utc','longitude','latitude','flight_speed_kph'}
        if (set(f) != required or f['version'] != VERSION or f['role'] != 'synthetic_fixture_only'
                or f['case_id'] != case_id or f['inputs_sha256'] != inputs.contract.document_sha256):
            raise ValueError('only independently pinned synthetic fixture data is supported')
        if (type(expected_road_sha256) is not str or f['road_sha256']!=expected_road_sha256
                or type(expected_work_sites_sha256) is not str or api._hash(f['work_sites'])!=expected_work_sites_sha256
                or type(f['road']) is not dict or f['road'].get('sha256') != f['road_sha256']
                or api._hash({k:v for k,v in f['road'].items() if k != 'sha256'}) != f['road_sha256']):
            raise ValueError('synthetic road or work-site evidence pin differs')
        # Reject unsupported communication delays before constructing or
        # warming the physical fire. The first weather observation must have
        # arrived at handover; later reports use EventBus's 0..60-minute bound.
        if type(drafts) is not dict or type(drafts.get('cases')) is not list:
            raise ValueError('fixture weather draft required')
        matching=[c for c in drafts['cases'] if type(c) is dict and c.get('id')==case_id]
        if len(matching)!=1:raise ValueError('exactly one fixture weather draft required')
        profile=weather.WeatherProfile.from_records(matching[0].get('weather_schedule'))
        if (profile.records[0].received_at!=0
                or any(not 0<=r.received_at-r.minute<=60 for r in profile.records)):
            raise ValueError('handover weather must be received at minute zero and report delays must be at most 60 minutes')
        root=Path(__file__).resolve().parents[2]
        if (Path(clock_module.__file__).resolve() != (root/'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/clock.py').resolve()
                or solar_daylight is not clock_module.solar_daylight):raise RuntimeError('clock origin or binding differs')
        self._clock = hashlib.sha256(Path(clock_module.__file__).read_bytes()).hexdigest()
        self._source = _sha(); self._deps = pins._sources(); self._hold_source = hold_sha()
        self._fixture = f; self._fixture_sha = expected_fixture_sha256; self._inputs = inputs
        self._weather = WeatherMixedFireDiagnosticV1(inputs, drafts, expected_drafts_sha256=expected_drafts_sha256,
            case_id=case_id, containment_config=containment_config, pressure_scale=pressure_scale,
            opt_in_weather_fire=True)
        if self._weather._case.get('role')!='synthetic_fixture_only':
            raise ValueError('source drafts are unsupported; an explicitly synthetic fixture draft is required')
        while self._weather._status == 'active' and self._weather._weather_kind == 'warm': self._weather.step()
        if self._weather._warmup_done == 0:
            self._weather._fire._fire.wind = tuple(weather.conditions(self._weather._profile, 0)['wind'])
        self._weather._fire = self._fire = MixedHoldFireV1(self._weather._fire)
        network = RoadNetwork(f['road'], f['road_sha256'])
        self._network = network
        allowed = f['allowed_edges']
        if (type(allowed) is not list or len(allowed) != len(set(allowed))
                or any(k not in network.edges for k in allowed)):
            raise ValueError('invalid independently pinned synthetic edge whitelist')
        self._allowed = frozenset(allowed)
        c = inputs.contract
        grid = dict(width=c.width, height=c.height, crs=c.crs, transform=list(c.transform))
        self._excluded = frozenset((x,y) for y in range(c.height) for x in range(c.width)
            if not (c.domain[y][x] and c.surface[y][x] == 'land' and c.access[y][x] == 'accessible'
                    and c.fire_support[y][x]))
        self._router = HazardRouter(network, {'grid': grid}, [list(row) for row in c.elevation],
            walk_mask=[[0]*c.width for _ in range(c.height)], allowed_edge_ids=allowed)
        self._nodes = {}
        for node, position in network.nodes.items(): self._nodes.setdefault(tuple(position), []).append(node)
        for eid in allowed:
            e = network.edges[eid]
            if not e.get('allowed_modes') or any(m not in ('engine','crew') for m in e['allowed_modes']):
                raise ValueError('synthetic road must explicitly declare vehicle modes')
            if not self._router._safe_line(e['coordinates'], self._excluded, 'engine'):
                raise ValueError('synthetic road crosses unsupported cell')
        self._sites = {}
        if type(f['work_sites']) is not list: raise ValueError('work site declarations required')
        for site in f['work_sites']:
            if (type(site) is not dict or set(site) != {'id','node','edge','protected_cell','kinds'}
                    or type(site['id']) is not str or not site['id'] or site['id'] in self._sites
                    or not site['kinds'] or any(k not in ('crew','engine') for k in site['kinds'])):
                raise ValueError('invalid synthetic work site')
            self._node(site['node']); edge = _edge(site['edge']); protected = _point(site['protected_cell'])
            if (protected not in edge or self._router._cell(site['node']) not in edge
                    or any(not self._router._inside(p) or p in self._excluded for p in edge)):
                raise ValueError('work site requires independently supported fixture cells')
            self._sites[site['id']] = copy.deepcopy(site)
        self._start = datetime.fromisoformat(f['start_utc'].replace('Z','+00:00'))
        if (self._start.utcoffset() is None or self._start.utcoffset().total_seconds() != 0
                or any(type(f[k]) not in (int,float) or not math.isfinite(f[k]) for k in ('longitude','latitude','flight_speed_kph'))
                or not -180 <= f['longitude'] <= 180 or not -90 <= f['latitude'] <= 90
                or not 0 < f['flight_speed_kph'] <= 500):
            raise ValueError('invalid synthetic flight parameters')
        if type(f['centers']) is not list or not f['centers']: raise ValueError('fixture centers required')
        resources = copy.deepcopy(f['resources']); groups = copy.deepcopy(f['groups']); destinations = copy.deepcopy(f['destinations'])
        for u in resources:
            if (type(u) is not dict or type(u.get('id')) is not str or not u['id']
                    or u.get('kind') not in ('crew','engine','aircraft') or u.get('state') != 'available'
                    or type(u.get('work_remaining')) is not int or u['work_remaining']<=0
                    or type(u.get('water_litres')) is not int or type(u.get('capacity_litres')) is not int
                    or not 0<=u['water_litres']<=u['capacity_litres']
                    or (u['kind']=='crew' and u['capacity_litres']!=0)
                    or (u['kind'] in ('engine','aircraft') and u['water_litres']<=0)
                    or (u.get('total_work_remaining') is not None and (type(u['total_work_remaining']) is not int or u['total_work_remaining']<=0))):
                raise ValueError('unsupported fixture resource or finite budget')
            if u['kind']=='aircraft' and (type(u.get('fuel_minutes')) not in (int,float)
                    or type(u.get('fuel_capacity_minutes')) not in (int,float)
                    or not math.isfinite(u['fuel_minutes']) or not math.isfinite(u['fuel_capacity_minutes'])
                    or not 0<=u['fuel_minutes']<=u['fuel_capacity_minutes'] or u['fuel_capacity_minutes']<=0):
                raise ValueError('finite synthetic aircraft fuel required')
            pins.mobility_module.point(u['position']);pins.mobility_module.point(u['base_position'])
            if u['kind'] != 'aircraft': self._node(u['position']); self._node(u['base_position'])
            if u['position'] != u['base_position']: raise ValueError('resource handover must be at base')
        self._initial_exposure = {}
        self._handover_exposure = {}
        for g in groups:
            if (type(g) is not dict or type(g.get('id')) is not str or not g['id']
                    or type(g.get('count')) is not int or g['count']<=0
                    or g.get('status') not in ('at_home','arrived')):raise ValueError('invalid fixture group')
            self._node(g['position']); self._node(g['home'])
            initial = g.pop('initial_exposed'); known = g.pop('handover_exposure')
            if type(initial) is not bool or (known is not None and (type(known) is not bool or known != initial)):
                raise ValueError('invalid inherited and reported exposure')
            g['exposed'] = initial; self._initial_exposure[g['id']] = initial; self._handover_exposure[g['id']] = known
        for d in destinations:
            if (type(d) is not dict or type(d.get('id')) is not str or not d['id']
                    or type(d.get('capacity')) is not int or d['capacity']<0
                    or d.get('status') not in ('open','closed')):raise ValueError('invalid fixture destination')
            self._node(d['position'])
        by_destination={d['id']:d for d in destinations}
        for g in groups:
            destination=g.get('destination_id')
            if (g['status']=='at_home' and (g['position']!=g['home'] or destination is not None)
                    or g['status']=='arrived' and (destination not in by_destination
                        or g['position']!=by_destination[destination]['position'])):
                raise ValueError('fixture population handover position or destination differs')
        if self._weather._status == 'active':
            burning = {tuple(x) for x in self._fire.burning()}
            if any(self._router._cell(g['position']) in burning and not g['exposed'] for g in groups):
                raise ValueError('fixture must declare inherited exposure in burning handover cell')
        profile = copy.deepcopy(PROFILE)
        # Facilities are explicit fixture data; every resource carries its own
        # synthetic finite refill stock at its declared base.
        profile['facilities'] = {}
        for u in resources:
            facility = u.get('facility_id')
            if type(facility) is not str or facility in profile['facilities']: raise ValueError('unique fixture facility required')
            stock = u.pop('fixture_facility_water')
            if type(stock) is not int or stock < 0: raise ValueError('invalid fixture water stock')
            profile['facilities'][facility] = dict(position=copy.deepcopy(u['base_position']),
                allowed_kinds=[u['kind']], water_litres=stock, refuel=u['kind']=='aircraft')
        self._ops = Operations(resources, destinations, groups, profile, return_planner=self._return)
        self._bus = EventBus(self._weather._case['seed'], f['centers'])
        self._validate_timeline()
        self._known = {k:_view(v) for k,v in self._ops.resources.items()}
        self._known_at = {k:0 for k in self._known}
        self._groups = {k:dict(_group(v),exposed=self._handover_exposure[k]) for k,v in self._ops.groups.items()}
        self._group_at = {k:0 for k in self._groups}; self._exposed_at = {k:(0 if v is True else None) for k,v in self._handover_exposure.items()}
        self._destinations = copy.deepcopy(self._ops.destinations); self._seen = 0
        # Public knowledge is populated only by delivered weather reports.
        self._known_wind = None; self._wind_at = -1
        self._minute = 0; self._closed = {}; self._restricted = set(); self._air_until = 0; self._dose = {k:0 for k in self._groups}
        self._missions = {}; self._calls = []; self._recording = True; self._poisoned = False
        self._status = 'censored' if self._weather._status == 'censored' else 'active'
        initial = sorted(self._fire.burning()) if self._status == 'active' else []
        self._handover_fire = copy.deepcopy(initial[:max(1,len(initial)//2)])
        self._identity = dict(version=VERSION, implementation=self._source, dependencies=self._deps,
            clock=self._clock, hold_fire=self._hold_source, fixture=expected_fixture_sha256,
            road=expected_road_sha256,work_sites=expected_work_sites_sha256,fire=copy.deepcopy(self._weather._identity))
        if self._status == 'active':
            self._bus.emit('handover','handover', {'cells':self._handover_fire,
                'text':'Synthetic fixture exercise; observations may be stale.'},0,role='center',delay=0)
            self._guard_bases(self._ops,lambda source,kind,payload,**kw:self._bus.emit(source,kind,payload,0,**kw))
            self._weather_reports(0); self._incoming(0); self._bus.tick(0,{}); self._sync()

    def _node(self, position):
        if type(position) is not list or len(position) != 2 or len(self._nodes.get(tuple(position),())) != 1:
            raise ValueError('exact unique synthetic road node required')
        return self._nodes[tuple(position)][0]

    def _validate_timeline(self):
        events = self._fixture['events']; reports = self._fixture['incoming_reports']
        if type(events) is not list or type(reports) is not list: raise ValueError('fixture timeline required')
        for e in events:
            if type(e) is not dict or type(e.get('minute')) is not int or not 1 <= e['minute'] <= 1440:
                raise ValueError('invalid fixture event')
            k=e.get('kind')
            if k == 'center_unavailable':
                if set(e)!={'kind','minute','center_id','until'} or e['center_id'] not in self._fixture['centers'] or type(e['until']) is not int or not e['minute']<e['until']<=1440: raise ValueError('invalid center event')
            elif k == 'aircraft_unavailable':
                if set(e)!={'kind','minute','until'} or type(e['until']) is not int or not e['minute']<e['until']<=1440: raise ValueError('invalid aircraft event')
            elif k == 'destination_closed':
                if set(e)!={'kind','minute','destination_id'} or e['destination_id'] not in self._ops.destinations: raise ValueError('invalid destination event')
            elif k == 'access_restriction':
                if set(e)!={'kind','minute','cells'} or type(e['cells']) is not list: raise ValueError('invalid restriction event')
                for p in e['cells']:
                    if type(p) is not list or len(p)!=2 or any(type(v) is not int for v in p) or not self._router._inside(tuple(p)): raise ValueError('invalid restriction cell')
            else: raise ValueError('unsupported scheduled event: '+str(k))
        for r in reports:
            if (type(r) is not dict or set(r)!={'minute','source','role','channel','text','delay'}
                or type(r['minute']) is not int or not 0<=r['minute']<=1440 or type(r['delay']) is not int
                or not 0<=r['delay']<=60 or r['channel'] not in ('radio','phone','routine')
                or any(type(r[k]) is not str or not r[k] for k in ('source','role','text'))): raise ValueError('invalid fixture incoming report')
        for e in self._weather._case['events']:
            if e['kind'] != 'wind': raise ValueError('unsupported fire-draft scheduled event')
        if self._weather._case.get('incoming_reports'): raise ValueError('fixture reports must be separately pinned')

    def _ensure(self):
        if self._poisoned: raise RuntimeError('joint state indeterminate; restore trusted checkpoint')
        if (_sha()!=self._source or pins._sources()!=self._deps or hold_sha()!=self._hold_source
                or Operations is not pins.operations_module.Operations or EventBus is not pins.events_module.EventBus
                or MixedHoldFireV1 is not hold_module.MixedHoldFireV1 or hold_sha is not hold_module.source_sha256
                or HazardRouter is not pins.routing_module.HazardRouter or RoadNetwork is not pins.mobility_module.RoadNetwork
                or make_journey is not pins.mobility_module.make_journey or make_leg is not pins.mobility_module.make_leg
                or journey_position is not pins.mobility_module.journey_position
                or Path(clock_module.__file__).resolve() != (Path(__file__).resolve().parents[2]/'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/clock.py').resolve()
                or solar_daylight is not clock_module.solar_daylight
                or hashlib.sha256(Path(clock_module.__file__).read_bytes()).hexdigest()!=self._clock
                or api._hash(self._fixture)!=self._fixture_sha
                or self._weather._fire is not self._fire or self._weather._identity!=self._identity['fire']
                or self._weather._minute!=self._minute
                or self._ops.last_minute!=(self._minute if self._minute else None)):
            self._poisoned=True; raise RuntimeError('joint fixture or implementation identity changed')
        self._weather._ensure()

    def _active(self):
        self._ensure()
        if self._status!='active': raise RuntimeError('fixture cannot advance')

    def situation(self):
        self._ensure()
        if self._status=='censored':return dict(version=VERSION,status='censored',minute=self._minute)
        return dict(version=VERSION, mode='synthetic_fixture_unscored', status=self._status, minute=self._minute,
            assumption='Synthetic declared roads and work sites; no source access or historical claim.',
            resources=copy.deepcopy(list(self._known.values())), population=copy.deepcopy(list(self._groups.values())),
            destinations=copy.deepcopy(list(self._destinations.values())), wind=copy.deepcopy(self._known_wind),
            known_fire=copy.deepcopy(self._handover_fire),fire_observed_at=0,
            reports_cursor=len(self._bus.reports))

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        if self._status=='censored': raise api.CensoredError('reports suppressed after censor')
        return self._bus.get_reports(after_cursor=after_cursor, limit=limit)

    def _conditions(self, minute): return weather.conditions(self._weather._profile,minute)

    def _safe(self, position, kind):
        if kind == 'aircraft': return True
        cell=self._router._cell(position)
        return self._router._safe_cell(cell, self._router._hazards(self._fire.burning(),self._conditions(self._minute)['wind']) | self._excluded | self._restricted, 'engine')

    def _road(self, origin, destination, kind):
        try: start=self._node(origin); target=self._node(destination)
        except ValueError: return None
        if start==target: return None
        hazards=self._router._hazards(self._fire.burning(),self._conditions(self._minute)['wind']) | self._excluded | self._restricted
        if not self._router._safe_cell(self._router._cell(origin),hazards,'engine'): return None
        paths,_=self._router._roads([(start,[])],hazards,{target},kind)
        legs=paths.get(target)
        if not legs: return None
        return make_journey(legs,origin,destination,'Synthetic declared fixture road only')

    def _flight(self, origin, destination):
        if math.dist(origin,destination)<.01: return make_journey([],origin,destination,'Synthetic aircraft already at base')
        return make_journey([make_leg('flight',[origin,destination],self._fixture['flight_speed_kph'])],origin,destination,'Synthetic flight')

    def _return(self, unit, minute):
        if unit['kind']=='aircraft': return self._flight(unit['position'],unit['base_position'])
        return self._road(unit['position'],unit['base_position'],unit['kind'])

    def _valid_road(self, journey, kind):
        try:
            cursor=journey['origin']; self._node(cursor); legs=[]
            for leg in journey['legs']:
                edge=self._network.edges[leg['edge_id']]
                if edge['id'] not in self._allowed or kind not in edge['allowed_modes'] or edge['coordinates'][0]!=cursor:
                    return False
                legs.append(make_leg('road',edge['coordinates'],edge['speed_kph'],edge['id'],edge['target']))
                cursor=edge['coordinates'][-1]
            if not legs or cursor!=journey['destination']:return False
            self._node(cursor); canonical=make_journey(legs,journey['origin'],cursor,'')
            return (journey['legs']==canonical['legs'] and journey['duration_minutes']==canonical['duration_minutes']
                    and journey['distance_m']==canonical['distance_m'])
        except (KeyError,TypeError,ValueError):return False

    def _movement(self, ops, journey, minute, kind):
        if kind=='aircraft': return True
        mode='engine' if kind=='group' else kind
        if not self._valid_road(journey,mode):return False
        matches=[]
        for u in ops.resources.values():
            if u.get('journey')==journey: matches.append(u['position'])
        for g in ops.groups.values():
            for order_id in (g.get('order_id'),g.get('pending_order_id')):
                order=ops.orders.get(order_id)
                if order and order.get('journey')==journey and order['state'] in ('executing','blocked'): matches.append(g['position'])
        if not matches or any(math.dist(p,journey_position(journey,minute-1)[0])>=.01 for p in matches):return False
        return self._router.safe_journey_segment(journey,minute-1,self._fire.burning(),self._conditions(minute)['wind'],
            mode='engine',excluded_cells=self._excluded | self._restricted) is True

    def _guard_bases(self, ops, emit):
        for u in ops.resources.values():
            if (u['kind']!='aircraft' and u['state'] in ('available','on_scene','working','refilling','resting','unavailable')
                    and _same_position(u['position'],u['base_position']) and not self._safe(u['position'],u['kind'])):
                if u['state']=='refilling': ops._cancel_refill(u)
                u.update(state='needs_assistance',journey=None);u.pop('blocked_state',None)
                emit(u['id'],'resource_status',{'resource':_view(u)},actionable=True)

    def _weather_reports(self, minute):
        for r in self._weather._profile.records:
            if r.minute==minute:
                self._bus.emit('weather','weather_update',dict(wind=list(r.wind),temperature_c=r.temperature_c,
                    relative_humidity_pct=r.relative_humidity_pct,rain_mm_per_hour=r.rain_mm_per_hour,
                    text='Synthetic fixture weather.'),minute,delay=r.received_at-r.minute)

    def _incoming(self, minute):
        for r in self._fixture['incoming_reports']:
            if r['minute']==minute:
                self._bus.emit(r['source'],'incoming_report',{'text':r['text']},minute,
                    role=r['role'],channel=r['channel'],delay=r['delay'])

    def _sync(self):
        for r in self._bus.reports[self._seen:]:
            p=r['payload']; observed=r['observed_at']
            if r['kind']=='resource_status' and p['resource']['id'] in self._known:
                u=p['resource']; k=u['id']
                if observed>=self._known_at[k]:self._known[k]=copy.deepcopy(u);self._known_at[k]=observed
            elif r['kind']=='resident_update' and p['group']['id'] in self._groups:
                g=p['group'];k=g['id']
                if g.get('exposed') is True:self._exposed_at[k]=observed if self._exposed_at[k] is None else min(self._exposed_at[k],observed)
                if observed>self._group_at[k]:self._groups[k]=copy.deepcopy(g);self._group_at[k]=observed
                if self._exposed_at[k] is not None:self._groups[k]['exposed']=True
            elif r['kind']=='destination_threatened':self._destinations[p['destination']['id']]=copy.deepcopy(p['destination'])
            elif r['kind']=='weather_update' and observed>self._wind_at:
                self._known_wind=copy.deepcopy(p['wind']);self._wind_at=observed
        self._seen=len(self._bus.reports)

    def _call(self,name,args,command):
        self._active(); self._step(command); result=self.situation()
        if self._recording:self._calls.append(dict(operation=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
        return result

    def dispatch_hold(self, resource_id, site_id):
        self._active()
        if resource_id not in self._known or site_id not in self._sites or self._known[resource_id]['kind'] not in self._sites[site_id]['kinds']:
            raise ValueError('unknown resource or declared work site')
        return self._call('dispatch_hold',[resource_id,site_id],dict(kind='hold',resource=resource_id,site=site_id))

    def clear_aircraft(self, resource_id):
        self._active()
        if resource_id not in self._known or self._known[resource_id]['kind']!='aircraft':raise ValueError('unknown aircraft')
        return self._call('clear_aircraft',[resource_id],dict(kind='clear',resource=resource_id))

    def dispatch_aircraft(self, resource_id, target):
        self._active()
        if resource_id not in self._known or self._known[resource_id]['kind']!='aircraft' or target not in self._handover_fire:
            raise ValueError('aircraft and reported handover fire cell required')
        return self._call('dispatch_aircraft',[resource_id,copy.deepcopy(target)],dict(kind='air',resource=resource_id,target=copy.deepcopy(target)))

    def protect(self, group_id, action, destination_id=None):
        self._active()
        if group_id not in self._groups or action not in ('shelter','evacuate') or (action=='shelter' and destination_id is not None) or (action=='evacuate' and destination_id not in self._destinations):
            raise ValueError('invalid population request')
        return self._call('protect',[group_id,action,destination_id],dict(kind='protect',group=group_id,action=action,destination=destination_id))

    def release(self, resource_id):
        self._active()
        if resource_id not in self._known:raise ValueError('unknown resource')
        return self._call('release',[resource_id],dict(kind='release',resource=resource_id))

    def request_status(self, center_id):
        self._active()
        if center_id not in self._fixture['centers']:raise ValueError('unknown center')
        return self._call('request_status',[center_id],dict(kind='status',center=center_id))

    def advance(self, minutes=1):
        self._active()
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step()
            if self._status!='active':break
        result=self.situation()
        if self._recording:self._calls.append(dict(operation='advance',args=[minutes],result=copy.deepcopy(result)))
        return result

    def _step(self, command=None):
        try:
            if not self._fire._check():self._status=self._weather._status='censored';return
            minute=self._minute+1
            ops=Operations.restore(self._ops.checkpoint(),return_planner=self._return)
            bus=EventBus.restore(self._bus.checkpoint());pending=[];assignments=[];drops=[]
            before={k:_view(u) for k,u in ops.resources.items()};group_before={k:_group(g) for k,g in ops.groups.items()}
            missions=copy.deepcopy(self._missions);dose=copy.deepcopy(self._dose)
            def emit(source,kind,payload,**kwargs):pending.append((source,kind,copy.deepcopy(payload),copy.deepcopy(kwargs)))
            self._minute=minute
            for e in self._fixture['events']:
                if e['minute']!=minute:continue
                if e['kind']=='center_unavailable':
                    self._closed[e['center_id']]=max(self._closed.get(e['center_id'],0),e['until'])
                elif e['kind']=='aircraft_unavailable':
                    self._air_until=max(self._air_until,e['until'])
                    emit('regional','aircraft_window',{'until':self._air_until})
                elif e['kind']=='access_restriction':self._restricted.update(map(tuple,e['cells']));emit('road-patrol','access_restriction',{'text':'Synthetic fixture road restriction.'},urgent=True)
                elif e['kind']=='destination_closed':
                    d=ops.destinations[e['destination_id']];d['status']='closed'
                    emit('destination','destination_threatened',{'destination':d},urgent=True)
            conditions=self._conditions(minute)
            daylight=bool(solar_daylight(self._start+timedelta(minutes=minute),self._fixture['longitude'],self._fixture['latitude']) and minute>=self._air_until)
            ops.set_flight_conditions(conditions['wind'],daylight)
            if command:
                kind=command['kind'];accepted=False
                if kind=='hold':
                    u=ops.resources[command['resource']];site=self._sites[command['site']]
                    plan=self._road(u['position'],site['node'],u['kind'])
                    accepted=bool(plan and ops.dispatch(u['id'],'fixture',list(self._router._cell(site['node'])),plan,minute))
                    if accepted:missions[u['id']]=command['site']
                elif kind=='clear':accepted=bool(ops.clear_aircraft(command['resource'],minute))
                elif kind=='air':
                    u=ops.resources[command['resource']];x,y=command['target'];t=self._inputs.contract.transform
                    target=[t[2]+(x+.5)*t[0],t[5]+(y+.5)*t[4]];plan=self._flight(u['position'],target)
                    accepted=bool(plan['legs'] and ops.dispatch(u['id'],'fixture',command['target'],plan,minute))
                elif kind=='release':
                    u=ops.resources[command['resource']];accepted=bool(ops.release(u['id'],self._return(u,minute),minute))
                    if accepted and u['kind']=='aircraft' and u['state']=='returning':
                        u['journey']['departed_at']=minute-1;u['journey']['arrives_at']=minute-1+u['journey']['duration_minutes']
                    missions.pop(u['id'],None)
                elif kind=='protect':
                    g=ops.groups[command['group']];dest=command['destination'];plan=None
                    if command['action']=='evacuate':plan=self._road(g['position'],ops.destinations[dest]['position'],'engine')
                    try:
                        if command['action']=='evacuate' and plan is None:raise ValueError('no guarded route')
                        ops.protect(g['id'],command['action'],dest,plan,minute);accepted=True
                    except ValueError:pass
                elif kind=='status':bus.request_status(command['center'],'situation',minute-1);accepted=True
                if kind!='status':emit('operations','request_result',{'kind':kind,'accepted':bool(accepted)})
            shadow={tuple(p):copy.deepcopy(self._fire._fire.cells[p[1]][p[0]]) for p in self._fire.burning()}
            def work(u):
                if u['kind']=='aircraft':
                    for point in sorted(shadow):
                        cell=shadow[point]
                        if u.get('target_grid') is not None and math.dist(point,u['target_grid'])<=3 and cell['heat']>0 and cell['fuel']>0:
                            before_heat=cell['heat'];cell['heat']=max(0.,before_heat-.15);cell['wet']=max(cell.get('wet',0),5)
                            drops.append(dict(resource_id=u['id'],cell=point,before=before_heat,after=cell['heat'],work=u['work_remaining'],water=u['water_litres'],total=u.get('total_work_remaining')));return True
                    return False
                site=self._sites.get(missions.get(u['id']))
                if not site or u['position']!=site['node'] or u['kind'] not in site['kinds']:return False
                edge=_edge(site['edge']);protected=_point(site['protected_cell']);line=self._fire._fire._containment._lines.get(edge)
                decay=self._fire._fire._containment.config['decay']
                if line and (line['breached'] or 0<line['strength']<=decay or (line.get('protected_cell') is not None and tuple(line['protected_cell'])!=protected)):return False
                if any(_edge(a['edge'])==edge and tuple(a['protected_cell'])!=protected for a in assignments):return False
                cost=0 if u['kind']=='crew' else min(u['water_litres'],ops.profile['engine_litres_per_minute'])
                if u['kind']=='engine' and cost<=0:return False
                assignments.append(dict(resource_id=u['id'],edge=copy.deepcopy(site['edge']),protected_cell=list(protected),
                    kind=u['kind'],ready=True,work_remaining=u['work_remaining'],water_litres=u['water_litres'],water_cost=cost,total=u.get('total_work_remaining')))
                return True
            ops.tick(minute,wind=conditions['wind'],daylight=daylight,hazard_check=self._safe,
                movement_check=lambda j,m,k:self._movement(ops,j,m,k),suppress=work,emit=emit,return_planner=self._return)
            self._guard_bases(ops,emit)
            for item in drops:
                u=ops.resources[item['resource_id']]
                if u['work_remaining']!=item['work']-1 or u['water_litres']!=0 or (item['total'] is not None and u.get('total_work_remaining')!=item['total']-1):raise RuntimeError('aircraft costs differ')
                x,y=item['cell'];cell=self._fire._fire.cells[y][x]
                if not self._fire._is_land(x,y) or cell['heat']!=item['before'] or cell['fuel']<=0:raise RuntimeError('unsupported aircraft drop')
                cell['heat']=item['after'];cell['wet']=max(cell.get('wet',0),5)
            if not self._fire._check():raise RuntimeError('censored after aircraft drop')
            staged=[{k:v for k,v in a.items() if k!='total'} for a in assignments]
            self._fire.stage(staged);state=self._weather.step();self._guard_bases(ops,emit)
            expected=[dict(resource_id=a['resource_id'],work_minutes=1,water_litres=float(a['water_cost'])) for a in sorted(assignments,key=lambda a:a['resource_id'])]
            if self._fire.costs!=expected:raise RuntimeError('hold costs differ')
            for a in assignments:
                u=ops.resources[a['resource_id']]
                if u['work_remaining']!=a['work_remaining']-1 or u['water_litres']!=a['water_litres']-a['water_cost'] or (a['total'] is not None and u.get('total_work_remaining')!=a['total']-1):raise RuntimeError('ground costs differ')
            missions={k:v for k,v in missions.items() if ops.resources[k]['state'] in ('mobilizing','travel','on_scene','working','blocked')}
            self._ops=ops;self._bus=bus;self._missions=missions
            if state['status']=='censored':self._status='censored';return
            burning=self._fire.burning()
            for k,g in ops.groups.items():
                cell=self._router._cell(g['position'])
                if any(math.dist(cell,p)<=1.5 for p in burning):dose[k]+=2 if g['status']=='sheltering' else 10
                if list(cell) in burning or dose[k]>=180:g['exposed']=True
                if _group(g)!=group_before[k]:emit(k,'resident_update',{'group':_group(g)},role='resident',channel='radio')
            self._dose=dose
            for k,u in ops.resources.items():
                if before[k]!=_view(u) or minute%30==0:emit(k,'resource_status',{'resource':_view(u)},actionable=True)
            for item in drops:emit(item['resource_id'],'drop_completed',{'resource_id':item['resource_id']})
            for source,kind,payload,kwargs in pending:self._bus.emit(source,kind,payload,minute,**kwargs)
            self._weather_reports(minute);self._incoming(minute)
            views={c:({'available':False} if self._closed.get(c,0)>minute else {'situation':{'observed_at':minute,'text':'Synthetic fixture'}}) for c in self._fixture['centers']}
            self._bus.tick(minute,views);self._sync()
            if state['status']=='diagnostic_end':self._status='end_unscored'
        except BaseException:
            self._poisoned=True;raise

    def transcript(self):
        self._ensure()
        return dict(version=VERSION,identity_sha256=api._hash(self._identity),calls=copy.deepcopy(self._calls))

    def checkpoint(self):
        self._ensure()
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),minute=self._minute,status=self._status,
            weather=self._weather.checkpoint(),operations=self._ops.checkpoint(),communications=self._bus.checkpoint(),
            known=copy.deepcopy(self._known),known_at=copy.deepcopy(self._known_at),groups=copy.deepcopy(self._groups),
            group_at=copy.deepcopy(self._group_at),exposed_at=copy.deepcopy(self._exposed_at),
            destinations=copy.deepcopy(self._destinations),known_wind=copy.deepcopy(self._known_wind),wind_at=self._wind_at,
            handover_fire=copy.deepcopy(self._handover_fire),closed=copy.deepcopy(self._closed),
            restricted=[list(x) for x in sorted(self._restricted)],air_until=self._air_until,dose=copy.deepcopy(self._dose),
            missions=copy.deepcopy(self._missions),seen=self._seen,calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value)
        return value

    def restore(self, checkpoint, *, expected_checkpoint_sha256):
        try:
            v=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(v)!=expected_checkpoint_sha256):raise ValueError('independent checkpoint digest required')
            if (type(v) is not dict or v.get('version')!=VERSION or v.get('identity')!=self._identity
                    or v.get('sha256')!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                    or _sha()!=self._source or pins._sources()!=self._deps or hold_sha()!=self._hold_source
                    or Operations is not pins.operations_module.Operations or EventBus is not pins.events_module.EventBus
                    or MixedHoldFireV1 is not hold_module.MixedHoldFireV1 or hold_sha is not hold_module.source_sha256
                    or HazardRouter is not pins.routing_module.HazardRouter or RoadNetwork is not pins.mobility_module.RoadNetwork
                    or make_journey is not pins.mobility_module.make_journey or make_leg is not pins.mobility_module.make_leg
                    or journey_position is not pins.mobility_module.journey_position
                    or api._hash(self._fixture)!=self._fixture_sha
                    or solar_daylight is not clock_module.solar_daylight
                    or hashlib.sha256(Path(clock_module.__file__).read_bytes()).hexdigest()!=self._clock
                    or v.get('handover_fire')!=self._handover_fire
                    or v.get('status') not in ('active','censored','end_unscored')
                    or type(v.get('minute')) is not int or not 0<=v['minute']<=1440):
                raise ValueError('joint checkpoint identity differs')
            self._weather.restore(v['weather'])
            ops=Operations.restore(v['operations'],return_planner=self._return);bus=EventBus.restore(v['communications'])
            if (self._weather._minute!=v['minute'] or ops.last_minute!=(v['minute'] if v['minute'] else None)
                    or (self._weather._status=='censored')!=(v['status']=='censored')
                    or (v['status']=='end_unscored')!=(self._weather._status=='diagnostic_end')
                    or set(ops.resources)!=set(self._known) or set(ops.groups)!=set(self._groups)
                    or set(ops.destinations)!=set(self._destinations) or v['seen']!=len(bus.reports)
                    or bus._last_tick>v['minute'] or type(v['calls']) is not list):
                raise ValueError('joint checkpoint state or clocks differ')
            self._weather._fire=self._fire=MixedHoldFireV1(self._weather._fire)
            self._ops,self._bus=ops,bus
            self._minute,self._status=v['minute'],v['status']
            self._known,self._known_at=v['known'],v['known_at'];self._groups,self._group_at=v['groups'],v['group_at']
            self._exposed_at,self._destinations=v['exposed_at'],v['destinations']
            self._known_wind,self._wind_at=v['known_wind'],v['wind_at']
            self._closed,self._restricted,self._air_until=v['closed'],set(map(tuple,v['restricted'])),v['air_until']
            self._dose,self._missions,self._seen,self._calls=v['dose'],v['missions'],v['seen'],v['calls']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('joint checkpoint continuation differs')
        except BaseException:self._poisoned=True;raise

    def replay(self, transcript, *, expected_sha256, expected_identity_sha256, expected_final_checkpoint_sha256):
        self._active()
        if self._minute!=0 or self._calls:raise ValueError('replay requires a fresh fixture host')
        if (type(transcript) is not dict or set(transcript)!={'version','identity_sha256','calls'}
                or transcript['version']!=VERSION or api._hash(transcript)!=expected_sha256
                or transcript['identity_sha256']!=expected_identity_sha256
                or expected_identity_sha256!=api._hash(self._identity) or type(transcript['calls']) is not list):
            raise ValueError('joint transcript identity differs')
        self._recording=False
        try:
            for call in transcript['calls']:
                if (type(call) is not dict or set(call)!={'operation','args','result'}
                        or call['operation'] not in ('dispatch_hold','clear_aircraft','dispatch_aircraft','protect','release','request_status','advance')
                        or type(call['args']) is not list):raise ValueError('invalid joint replay call')
                if api._encoded(getattr(self,call['operation'])(*call['args']))!=api._encoded(call['result']):
                    raise ValueError('joint replay result differs')
            self._calls=copy.deepcopy(transcript['calls'])
            if api._hash(self.checkpoint())!=expected_final_checkpoint_sha256:raise ValueError('joint replay checkpoint differs')
            return self.situation()
        except BaseException:self._poisoned=True;raise
        finally:self._recording=True
