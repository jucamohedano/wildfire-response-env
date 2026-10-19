"""Seeded stochastic, deliberately simplified wildfire demo; not a prediction model."""
import copy
import heapq
import json
import math
import random
import uuid
from pathlib import Path
try:
    from .terrain import PROFILES, make_cells
    from .contacts import directory as contact_directory
except ImportError:
    from terrain import PROFILES, make_cells
    from contacts import directory as contact_directory

GEOGRAPHY = json.loads((Path(__file__).parent / "static/maps/brunete-illustrated.json").read_text(encoding="utf-8"))


class Simulation:
    width, height = 80, 56
    base = tuple(GEOGRAPHY["base"])
    town = tuple(GEOGRAPHY["town"])
    farm = tuple(GEOGRAPHY["farm"])
    report = tuple(GEOGRAPHY["ignition"])
    extinguisher_sensor_radius = 10
    scout_sensor_radius = 16
    sensor_radius = extinguisher_sensor_radius
    truck_sensor_radius = 9
    rules = dict(observation_sharing='All vehicles share current fire and clear sightings and timestamped memory. Truck may suppress drone-observed fire within hose range and pursue active sightings within its assigned sector. Never use hidden truth or stale sightings for suppression.',
                 truck_coordination='Drone can issue attack_sector with truck_target_x/y and truck_reason, or continue the prior truck order. Truck has 5 jets with 60% success each versus drone 1 jet with 40% success; use truck for main attack, drone for scouting, flank support and urgent warnings. Orders persist; truck chooses safe stand-off and route.',
                 evacuation='Warning delivery completes the drone task; people continue independently. Reassign drone to urgent unwarned people, otherwise scout/contain to assist truck.',
                 drone_planner='Optimistic A*: unknown cells traversable; replan when observed fire blocks the route; retain known fire until observed clear',
                 vehicle_movement='8 neighbors; diagonals cost sqrt(2) distance; blocked corners cannot be crossed',
                 ignition_probability_per_eligible_cell=0.5,
                 spread_attempts="One chance per eligible adjacent cell per step; failed attempts retry at wind-dependent intervals.",
                 drone_cells_per_step=3,
                 drone_jets=1,
                 drone_suppression_success_probability=0.4, truck_suppression_success_probability=0.6,
                 satellite_delay_steps=12,
                 satellite_interval_steps=12, satellite_block_size=8,
                 truck_cells_per_step=2, truck_offroad_speed_factor=0.8, truck_mobilization_steps=8,
                 truck_jets=5, hose_range=10, truck_approach_range=4,
                 drone_standoff_cells=3, drone_suppression_range=8)

    def __init__(self, seed=9, drone_count=1, fleet_counts=None, incident_id=None):
        self.incident_id = incident_id or str(uuid.uuid4())
        self.rng = random.Random(seed)
        self.suppression_rng = random.Random(seed+1)
        self.phase = "active"
        self.tick = 0
        self.wind = (1, 0)
        self.rules = dict(self.rules, spread_factor=0.5, spread_factor_policy="Divide wind-dependent attempt intervals by spread_factor, round to whole steps, minimum 1 step. Base ignition probability remains 50% before terrain/intensity modifiers; vehicle speed and suppression are unchanged.")
        self.revision = 0
        self.drone = dict(x=float(self.base[0]), y=float(self.base[1]), status='at_station', target=None, mode='hold')
        self.scouts = []
        self.scout_reports = []
        self.ignited = self.called = False
        self.call_text = ''
        self.history = []
        self.communications = []
        self.dispatch = None
        # Field events for the shared state (drones confirming fire, truck deployed/arrived/containing).
        self.pending_events = []
        self.event_log = []
        self._fire_reported_by = set()
        self._truck_milestones = {}
        self.mission_records = []
        self.seen_commands = set()
        self.suppressed = 0
        self.last_result = None
        self.pending_decision_event = None
        self.observation = []
        self.memory = {}
        self.satellite = None
        self.satellite_queue = []
        self.mission = 'Awaiting a report'
        self.truck = dict(x=float(self.base[0]), y=float(self.base[1]), status="at_station", target=None, route=[], mobilized_at=None, observed_fire=[],drone_order=None)
        self.roads = {tuple(p) for p in GEOGRAPHY['roads']}
        self.cells = make_cells(self.width,self.height,self.roads,GEOGRAPHY.get("terrain_source_window"),GEOGRAPHY)
        for x,y in (self.base,self.farm):
            self.cells[y][x].update(terrain='built',fuel=1.,initial_fuel=1.)
        self.rules.update(terrain_profiles=PROFILES,fire_model='Intensity 0..1 grows while fuel burns. Eight-neighbor spread depends on target terrain, source intensity, wind and wetness. Roads/bare cells block surface fire; no ember spotting. Terrain is a hand-authored approximation, not measured land cover.',suppression_policy='Each drone jet succeeds with 40% probability, each truck jet with 60%. A successful jet reduces intensity by 0.20 and wets its target for 8 steps, preventing regrowth and new ignition during that period. Intense fire needs repeated hits. Truck has 5 jets; drone 1. Counters count actual extinguished cells, not successful hits. Cooled fuel can reignite after drying. Prioritize urgent warnings; truck handles main suppression.',jet_cooling=0.20,wet_steps=8,intensity_growth_per_step=0.04,burn_duration_multiplier=2.0)
        self.crew_due = None
        self.crew_target = None
        self.crew_extinguished = 0
        self.groups = {
            zone['id']: dict(x=float(zone['anchor'][0]),y=float(zone['anchor'][1]),
                name=zone['name'],kind=zone['kind'],home=zone['anchor'][:],
                count=zone['population'],population_basis=zone['population_basis'],
                burnt=0,status='unwarned',refuge=zone['refuge'][:])
            for zone in GEOGRAPHY['observation_zones']}
        self.rules['evacuation'] = ('Each population district is independent. Use evacuate_town with target_x/y equal to the chosen unwarned town district home coordinates from people; use evacuate_farm for the single farm district. Always supply district_id copied from evacuation_targets. The decision policy selects the district explicitly; missing/invalid IDs are rejected, never replaced with a nearest district. One warning evacuates ONLY that district, never the whole town. After delivery choose the next threatened unwarned district, or help the truck. Never repeat a warning for evacuating/blocked/safe/burnt people. District counts are scenario allocations of the official municipal total; farm occupancy is assumed.')
        self.configure_fleet(drone_count, **(fleet_counts or {}))
        self.observe()

    def configure_fleet(self, count=None, scouts=None, extinguishers=1, trucks=1):
        if self.ignited or self.called:raise ValueError('Reset before changing the fleet.')
        if scouts is None:
            if type(count) is not int or not 1<=count<=4:raise ValueError('Choose 1–4 drones.')
            scouts=count-1
        if any(type(n) is not int or not 0<=n<=3 for n in (scouts,extinguishers,trucks)) or scouts+extinguishers+trucks==0:
            raise ValueError('Choose 0–3 of each vehicle, with at least one vehicle overall.')
        self.scouts=[dict(drone_id=f'scout-{i+1}',role='scout',x=float(self.base[0]),y=float(self.base[1]),
            mode='hold',status='at_station',target=None,waypoints=[],route=[],sensor_radius=self.scout_sensor_radius,
            capabilities=['patrol','report','evacuate_town','evacuate_farm']) for i in range(scouts)]
        def drone(i):return dict(drone_id=f'drone-{i+1}',name='Squirtle' if i==0 else f'Drone {i+1}',role='extinguisher',x=float(self.base[0]),y=float(self.base[1]),status='at_station',target=None,mode='hold')
        def truck(i):return dict(truck_id=f'engine-{i+1}',role='truck',x=float(self.base[0]),y=float(self.base[1]),status='at_station',target=None,route=[],mobilized_at=None,observed_fire=[],drone_order=None,crew_target=None,crew_due=None)
        self.extinguishers=[drone(i) for i in range(extinguishers)]
        self.trucks=[truck(i) for i in range(trucks)]
        # Legacy aliases keep old recordings/tests readable; absent vehicles never sense or act.
        self.drone=self.extinguishers[0] if self.extinguishers else drone(0)
        self.truck=self.trucks[0] if self.trucks else truck(0)
        self.crew_target=self.crew_due=None
        self.memory={}

    def fleet_counts(self):
        return dict(scouts=len(self.scouts),extinguishers=len(self.extinguishers),trucks=len(self.trucks))

    def vehicles(self):
        return self.extinguishers+self.scouts+self.trucks

    def validate_ignition(self,x,y):
        if not self.ignited:raise ValueError('Start the scenario first.')
        if self.phase!='active':raise ValueError('Reset to start a new incident.')
        if type(x) is not int or type(y) is not int or not (1<=x<self.width-1 and 1<=y<self.height-1):
            raise ValueError('Select an interior map cell.')
        c=self.cells[y][x]
        if c['fuel']<=0 or c.get('wet',0)>0:raise ValueError('Choose dry vegetation or buildings.')

    def add_fire(self,x,y):
        self.validate_ignition(x,y)
        c=self.cells[y][x]
        c.update(heat=.25,age=0)
        self.log('simulation',f'Additional ignition at ({x}, {y}); hidden until observed.')
        self.update_people_exposure()

    def scout_telemetry(self):
        return [dict(d,speed=4,jets=0,standoff_cells=3) for d in self.scouts]

    def deliver_warning(self, d):
        if d['target'] is None and d['mode'].startswith('evacuate_'):
            name = d.get('evacuation_group') or d['mode'].split('_',1)[1]
            group = self.groups[name]
            if group['status'] == 'unwarned':
                group['status'] = 'evacuating'
                d.update(mode='hold',status='awaiting_assignment',route=[],travel_credit=0)
                self.last_result=dict(status='warning_delivered',settlement=name,tick=self.tick,
                                      detail='Residents move independently; drone available for the next mission.')
                self.pending_decision_event='evacuation_warning_delivered'
                self.log(d.get('drone_id','drone'), f'Loudspeaker warning delivered to {group.get("name",name)}: {group["count"]} people moving to refuge.')

    def scout_search_waypoints(self, scout, limit=3):
        radius=int(scout.get('sensor_radius',self.scout_sensor_radius));step=max(6,radius)
        blocked=self.danger_zone([(cell['x'],cell['y']) for cell in self.memory.values() if cell['burning']])
        current=(scout['x'],scout['y']);candidates=[]
        for y in range(radius//2,self.height,step):
            for x in range(radius//2,self.width,step):
                point=(min(self.width-1,x),min(self.height-1,y))
                if point in blocked or math.dist(current,point)<radius*.6:continue
                cells=[];score=0.
                for yy in range(max(0,point[1]-radius),min(self.height,point[1]+radius+1)):
                    for xx in range(max(0,point[0]-radius),min(self.width,point[0]+radius+1)):
                        if math.hypot(xx-point[0],yy-point[1])>radius:continue
                        cells.append((xx,yy));seen=self.memory.get(f'{xx},{yy}')
                        score+=2 if seen is None else min(1,max(0,self.tick-seen['observed_at'])/12)
                candidates.append((point,cells,score-math.dist(current,point)*.02))
        chosen=[];covered=set()
        while candidates and len(chosen)<limit:
            point,cells,_=max(candidates,key=lambda item:(sum(2 if f'{x},{y}' not in self.memory else min(1,max(0,self.tick-self.memory[f'{x},{y}']['observed_at'])/12) for x,y in item[1] if (x,y) not in covered),-math.dist(current,item[0]),item[0]))
            gain=sum((x,y) not in covered for x,y in cells)
            if not gain:break
            chosen.append(list(point));covered.update(cells);candidates=[item for item in candidates if item[0]!=point]
        return chosen

    def update_scouts(self):
        for d in self.scouts:
            if d['target'] is None and d['waypoints']:
                d['target']=d['waypoints'].pop(0)
            self.move_safely(d,4)
            self.deliver_warning(d)
            self.observe()
            if d.get('observed_fire') and not d.get('reported_first_fire'):
                d['reported_first_fire']=True
                self.pending_decision_event='scout_fire_confirmation'
                self.log('scout → central',f"{d['drone_id']} confirms fire through shared sensors; reassess all drone missions now, without waiting for arrival.")
            for f in d.get('observed_fire',[]):
                point=[f['x'],f['y']]
                # A new focus must be spatially separate from the report and prior scout alerts.
                known=[self.report]+[r['location'] for r in self.scout_reports]
                if any(math.dist(point,q)<12 for q in known):continue
                report=dict(scout_id=d['drone_id'],location=point,observed_at=self.tick,kind='new_fire_focus')
                self.scout_reports.append(report);self.scout_reports=self.scout_reports[-32:]
                self.pending_decision_event='scout_fire_report'
                self.log('scout → central',f"{d['drone_id']} reports a separate observed fire at {point}; requesting reassessment.")
            if d['target'] is None and not d['waypoints'] and d['mode']=='patrol':
                continuation=self.scout_search_waypoints(d) if not self.observation else []
                if continuation:
                    d.update(target=continuation.pop(0),waypoints=continuation,status='en_route',route=[])
                    self.log('scout autopilot',f"{d['drone_id']} continues searching because shared sensors detect no fire.")
                else:
                    d.update(mode='hold',status='awaiting_assignment')
                    self.pending_decision_event=self.pending_decision_event or 'scout_patrol_complete'

    def safe_scout_waypoint(self, point, blocked):
        x=max(0,min(self.width-1,point[0]));y=max(0,min(self.height-1,point[1]))
        if (x,y) not in blocked:return [x,y]
        safe=((xx,yy) for yy in range(self.height) for xx in range(self.width) if (xx,yy) not in blocked)
        chosen=min(safe,key=lambda p:((p[0]-x)**2+(p[1]-y)**2,p),default=None)
        return list(chosen) if chosen is not None else None

    def validate_scout_orders(self, raw):
        if isinstance(raw,str):
            try:raw=json.loads(raw)
            except (ValueError,TypeError):raise ValueError('scout_orders must be a JSON array.')
        if raw is None and not self.scouts:return []
        if not isinstance(raw,list):raise ValueError('Supply scout_orders for every configured scout.')
        expected={d['drone_id'] for d in self.scouts};seen=set()
        blocked=self.danger_zone([(c['x'],c['y']) for c in self.memory.values() if c['burning']])
        for order in raw:
            if not isinstance(order,dict):raise ValueError('Invalid scout order.')
            ident=order.get('drone_id')
            if not isinstance(ident,str) or ident not in expected or ident in seen:raise ValueError('Unknown or duplicate scout ID.')
            seen.add(ident)
            if order.get('command') not in {'patrol','hold','continue','evacuate_town','evacuate_farm'}:raise ValueError('Scouts can patrol, hold, continue or evacuate; never suppress.')
            district=order.get('district_id')
            if order['command'].startswith('evacuate_'):
                kind='farm' if order['command']=='evacuate_farm' else 'town'
                if not isinstance(district,str) or district not in self.groups or self.groups[district]['kind']!=kind or self.groups[district]['status']!='unwarned':
                    raise ValueError('Scout evacuation requires an explicit unwarned district of the matching kind.')
                if order.get('waypoints'):raise ValueError('Evacuation uses district home, not patrol waypoints.')
            elif district not in (None,''):raise ValueError('Use district_id only for a scout evacuation command.')
            points=order.get('waypoints',[])
            if not isinstance(points,list) or len(points)>6 or (order['command']=='patrol' and not points):raise ValueError('Patrol needs 1–6 waypoints.')
            for point in points:
                if not isinstance(point,list) or len(point)!=2 or any(type(v) is not int for v in point):raise ValueError('Scout waypoint must be [integer x, integer y].')
            if order['command']=='patrol':
                corrected=[]
                for point in points:
                    safe=self.safe_scout_waypoint(point,blocked)
                    if safe is not None and (not corrected or safe!=corrected[-1]):corrected.append(safe)
                if corrected!=points:
                    order['waypoints']=corrected
                    order['reason']=str(order.get('reason') or 'Scout patrol')+' [Autopilot adjusted waypoints for map bounds and fire clearance.]'
                    if not corrected:order['command']='hold'
            if not isinstance(order.get('reason'),str) or not order['reason'].strip():raise ValueError('Explain each scout assignment.')
        if seen!=expected:raise ValueError('Include exactly one order for every configured scout.')
        return raw

    def log(self, source, message, **extra):
        self.history.append(dict(tick=self.tick, source=source, message=message, **extra))
        self.history = self.history[-100:]

    def checkpoint(self):
        state=copy.deepcopy(self.__dict__)
        state.pop('rng');state.pop('suppression_rng')
        state['rng_state']=self.rng.getstate()
        state['suppression_rng_state']=self.suppression_rng.getstate()
        state['wind']=list(self.wind)
        state['roads']=[list(point) for point in sorted(self.roads)]
        state['seen_commands']=sorted(self.seen_commands)
        state['_fire_reported_by']=sorted(self._fire_reported_by)
        state['_truck_milestones']=[[list(key),value] for key,value in self._truck_milestones.items()]
        return dict(version=1,state=state)

    @classmethod
    def restore(cls, value):
        if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('state'),dict):
            raise ValueError('Unsupported simulation checkpoint version.')
        state=copy.deepcopy(value['state'])
        required={'incident_id','tick','wind','cells','extinguishers','scouts','trucks','roads','groups','rng_state','suppression_rng_state'}
        if not required<=state.keys():raise ValueError('Incomplete simulation checkpoint.')
        def tuples(item):
            return tuple(tuples(v) for v in item) if isinstance(item,list) else item
        rng_state=tuples(state.pop('rng_state'));suppression_state=tuples(state.pop('suppression_rng_state'))
        state['wind']=tuple(state['wind'])
        state['roads']={tuple(point) for point in state['roads']}
        state['seen_commands']=set(state.get('seen_commands',[]))
        state['_fire_reported_by']=set(state.get('_fire_reported_by',[]))
        state['_truck_milestones']={tuple(key):tick for key,tick in state.get('_truck_milestones',[])}
        for satellite in [state.get('satellite'),*state.get('satellite_queue',[])]:
            if satellite:satellite['blocks']=[tuple(point) for point in satellite.get('blocks',[])]
        sim=cls.__new__(cls);sim.__dict__.update(state)
        sim.rng=random.Random();sim.rng.setstate(rng_state)
        sim.suppression_rng=random.Random();sim.suppression_rng.setstate(suppression_state)
        sim.drone=sim.extinguishers[0] if sim.extinguishers else sim.drone
        sim.truck=sim.trucks[0] if sim.trucks else sim.truck
        if sim.trucks:
            sim.crew_target=sim.truck.get('crew_target')
            sim.crew_due=sim.truck.get('crew_due')
        return sim

    def emit(self, kind, source, x=None, y=None, **extra):
        """Queue a field event for the session's operational history."""
        event=dict(kind=kind,source=source,sim_time=self.tick,incident_id=self.incident_id,
                   x=None if x is None else int(round(x)),y=None if y is None else int(round(y)),**extra)
        self.pending_events.append(event)
        self.event_log=(self.event_log+[event])[-200:]
        return event

    def drain_events(self):
        events,self.pending_events=self.pending_events,[]
        return events

    def nearest_district(self, x, y):
        return min(self.groups,key=lambda k:math.dist((x,y),self.groups[k]['home'])) if self.groups else None

    def _truck_milestone(self, truck, kind, detail):
        key=(truck.get('truck_id','engine-1'),kind)
        if key in self._truck_milestones:return
        self._truck_milestones[key]=self.tick
        target=truck.get('crew_target') or [truck['x'],truck['y']]
        status={'deployed':'en_route','arrived':'arrived','containing':'containing','returning':'returning'}[kind]
        self.emit(kind,truck.get('truck_id','engine-1'),truck['x'],truck['y'],vehicle_id=truck.get('truck_id','engine-1'),status=status,
                  detail=detail,district_id=self.nearest_district(target[0],target[1]))
        self.log(truck.get('truck_id','engine-1'),detail)

    @staticmethod
    def burning(cell):
        return cell['heat'] > 0 and cell['fuel'] > 0

    def place_fire(self,x,y):
        if self.ignited or self.called:raise ValueError('Reset before moving the ignition point.')
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (x,y)) or not (1<=x<self.width-1 and 1<=y<self.height-1):
            raise ValueError('Select an interior map cell.')
        if math.hypot(x-self.base[0],y-self.base[1])<6:raise ValueError('Place the fire away from the station.')
        if self.cells[y][x]['fuel']<=0:raise ValueError('Choose vegetation or buildings, not a road or bare ground.')
        self.report=(x,y)

    def ignite(self):
        if not self.ignited:
            self.ignited = True
            for x,y in [self.report,(self.report[0],self.report[1]+1),(self.report[0]+1,self.report[1])]:
                if self.cells[y][x]['fuel']>0:self.cells[y][x]['heat'] = 0.25
            self.log('simulation', 'Fire ignited at the selected location. Ground truth only.')
            self.update_people_exposure()

    def farmer_call(self, message=''):
        if not self.ignited:
            raise ValueError('Start the fire first.')
        if self.called:
            raise ValueError('Farmer report already received; request a new decision instead.')
        self.called = True
        self.call_text = message or f'I am reporting a smoke column around grid {self.report}. Please investigate.'
        for truck in self.trucks:
            truck.update(mobilized_at=self.tick+8,status="mobilizing",crew_target=list(self.report))
        self.crew_target = list(self.report) if self.trucks else None
        self.log('farmer', self.call_text)
        self.log('dispatch', 'Truck mobilizing for 8 steps, then travelling on roads at 2 cells/step or off-road at 1.6 cells/step. Drone scouts ahead.')

    def set_wind(self, name=None, x=None, y=None):
        if x is None and y is None:
            choices = {'east':(1,0), 'north':(0,-1), 'west':(-1,0), 'south':(0,1), 'calm':(0,0)}
            if name not in choices:raise ValueError('Invalid wind direction.')
            x,y=choices[name]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>3 for v in (x,y)):
            raise ValueError('Wind X and Y must be finite numbers between -3 and 3.')
        self.wind=(round(x,2),round(y,2))
        self.revision+=1
        self.log('weather',f'Wind vector updated: X={self.wind[0]}, Y={self.wind[1]} (east/south positive); strength {math.hypot(*self.wind):.2f}.')

    def set_spread_factor(self, value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0.25<=value<=4:
            raise ValueError('Spread factor must be a finite number between 0.25 and 4.')
        self.rules['spread_factor']=float(value)
        self.revision+=1
        self.log('weather',f'Fire spread factor updated: {value:g}×.')

    def spread_interval(self, dx, dy):
        projection=dx*self.wind[0]+dy*self.wind[1]
        base = max(1,round(10/(1+4*projection))) if projection>=0 else round(10*(1-projection))
        return max(1,round(base/self.rules['spread_factor']))

    def step(self, count=1):
        for _ in range(count):
            self.update_completion()
            if self.phase == "finished":break
            self.tick += 1
            ignitions = {}
            for y,row in enumerate(self.cells):
                for x,c in enumerate(row):
                    wet=c.get('wet',0)
                    c['wet']=max(0,wet-1)
                    if not self.burning(c):continue
                    profile=PROFILES[c.get('terrain','field')]
                    c['age']+=1
                    if not wet:c['heat']=min(1.,c['heat']+self.rules['intensity_growth_per_step'])
                    if c['fuel']<.2:c['heat']=min(c['heat'],max(.05,c['fuel']/.2))
                    consumed=min(c['fuel'],max(.15,c['heat'])/(profile['duration']*self.rules['burn_duration_multiplier']))
                    c['fuel']=max(0.,c['fuel']-consumed)
                    c['burned']=min(1.,c.get('burned',0)+consumed)
                    for dx,dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                        nx,ny=x+dx,y+dy
                        if not (0<=nx<self.width and 0<=ny<self.height):continue
                        n=self.cells[ny][nx]
                        if n['fuel']<=0 or n['heat']>0 or n.get('wet',0)>0:continue
                        if dx and dy and (self.cells[y][nx]['fuel']<=0 or self.cells[ny][x]['fuel']<=0):continue
                        interval=self.spread_interval(dx/math.hypot(dx,dy),dy/math.hypot(dx,dy))
                        if c['age']%interval:continue
                        chance=min(1.,self.rules['ignition_probability_per_eligible_cell']*PROFILES[n.get('terrain','field')]['spread']*c['heat']/math.hypot(dx,dy))
                        ignitions[(nx,ny)]=max(ignitions.get((nx,ny),0),chance)
                    if c['fuel']<=0:c['heat']=0.
            # One seeded draw per destination; stronger exposure wins rather than stacking draws.
            for (x,y),chance in sorted(ignitions.items()):
                if self.rng.random()<chance:self.cells[y][x].update(heat=.25,age=0)
            self.update_people_exposure()
            self.update_scouts()
            for d in self.extinguishers:
                d['last_drop'] = None
                d['suppression_attempts'] = []
                self.move_safely(d,3)
                self.observe()
                if d['mode'] == 'contain' and d['status'] != 'retreating':
                    candidates = [c for c in self.observation if math.hypot(c['x']-d['x'],c['y']-d['y'])<=self.rules['drone_suppression_range']]
                    if candidates:
                        c = max(candidates,key=lambda c:c['x']*self.wind[0]+c['y']*self.wind[1])
                        attempts=self.suppress([(c['x'],c['y'])],self.rules['drone_jets'],self.rules['drone_suppression_success_probability'])
                        d['suppression_attempts']=attempts
                        self.suppressed += sum(a['extinguished'] for a in attempts)
                        d['last_drop'] = [c['x'],c['y']]
                self.deliver_warning(d)
            for name,g in self.groups.items():
                if g['status'] in {'evacuating','blocked'}:
                    tx,ty = g['refuge']
                    dist = math.hypot(tx-g['x'],ty-g['y'])
                    nx,ny = (float(tx),float(ty)) if dist<=.8 else (g['x']+.8*(tx-g['x'])/dist,g['y']+.8*(ty-g['y'])/dist)
                    if any(self.burning(self.cells[y][x]) for y in range(max(0,round(ny)-1),min(self.height,round(ny)+2)) for x in range(max(0,round(nx)-1),min(self.width,round(nx)+2))):
                        if g['status'] != 'blocked':
                            self.log('people', f'{name} evacuation route blocked by fire; ground assistance needed.')
                        g['status'] = 'blocked'
                    else:
                        g.update(x=nx,y=ny,status='safe' if dist<=.8 else 'evacuating')
                        if g['status']=='safe':
                            self.log('people', f'{name}: {g["count"]} people reached refuge.')
            self.update_completion()
            self.update_trucks()
            self.update_completion()
            self.observe()
            if self.tick % 12 == 0:
                blocks = sorted({(x//8*8,y//8*8) for y,row in enumerate(self.cells) for x,c in enumerate(row) if self.burning(c)})
                self.satellite_queue.append(dict(captured_at=self.tick,available_at=self.tick+12,blocks=blocks))
            while self.satellite_queue and self.satellite_queue[0]['available_at']<=self.tick:
                self.satellite = self.satellite_queue.pop(0)

    def update_completion(self):
        if self.phase == 'active' and self.ignited and not self.fire_points():
            self.phase='returning'
            self.mission='Fire out — drone and truck returning to station'
            for drone in self.extinguishers:drone.update(mode='returning',status='returning',target=list(self.base),last_drop=None)
            for scout in self.scouts:scout.update(mode='returning',status='returning',target=list(self.base),waypoints=[])
            for truck in self.trucks:
                truck.update(status='returning',target=list(self.base),last_drops=[])
                self._truck_milestone(truck,'returning',f"{truck.get('truck_id','engine-1')} returning to station: fire out.")
            self.emit('fire_out','simulation',detail='No fire remains; all vehicles returning.')
            self.log('simulation','Global simulator trigger: no fire remains. Returning both vehicles to station.')
        if self.phase == 'returning':
            for vehicle in self.vehicles():
                if (vehicle['x'],vehicle['y']) == self.base:
                    vehicle.update(status='at_station',target=None,route=[])
            if all((v['x'],v['y']) == self.base for v in self.vehicles()):
                self.phase='finished'
                self.mission='Finished — fire out, both vehicles at station'
                self.log('simulation','Both vehicles returned to station. Simulation finished.')

    def update_people_exposure(self):
        # Each population group occupies one cell in this simplified demo.
        for name,g in self.groups.items():
            if g.get('burnt',0) < g['count'] and self.burning(self.cells[round(g['y'])][round(g['x'])]):
                g['burnt']=g['count']
                g['status']='burnt'
                self.log('people',f"{name}: fire reached the group; {g['burnt']} people burnt.")

    def fire_points(self):
        return [(x,y) for y,row in enumerate(self.cells) for x,c in enumerate(row) if self.burning(c)]

    def danger_zone(self, points, clearance=3):
        return {(x+dx,y+dy) for x,y in points for dx in range(-clearance,clearance+1)
                for dy in range(-clearance,clearance+1) if dx*dx+dy*dy<clearance*clearance}

    def neighbors(self, point, blocked, roads=None):
        def clear(p):
            return 0<=p[0]<self.width and 0<=p[1]<self.height and p not in blocked and (roads is None or p in roads)
        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
            p=(point[0]+dx,point[1]+dy)
            if clear(p) and (not (dx and dy) or
                            (clear((point[0]+dx,point[1])) and clear((point[0],point[1]+dy)))):
                yield p

    def route(self, start, goal, blocked, roads=None):
        start=tuple(map(round,start));goal=tuple(map(round,goal))
        if goal in blocked:return []
        # Octile heuristic is admissible for eight-direction movement.
        def heuristic(p):
            dx,dy=abs(p[0]-goal[0]),abs(p[1]-goal[1])
            return max(dx,dy)+(math.sqrt(2)-1)*min(dx,dy)
        queue=[(heuristic(start),0,start)];costs={start:0};parents={start:None}
        while queue:
            _,cost,point=heapq.heappop(queue)
            if cost>costs[point]:continue
            if point==goal:
                path=[]
                while parents[point] is not None:path.append(list(point));point=parents[point]
                return path[::-1]
            for p in self.neighbors(point,blocked,roads):
                candidate=cost+math.dist(point,p)
                if candidate<costs.get(p,float('inf')):
                    costs[p]=candidate;parents[p]=point;heapq.heappush(queue,(candidate+heuristic(p),candidate,p))
        return []

    def move_safely(self, vehicle, speed, roads=None):
        # Local collision avoidance is a physical safeguard, not a strategic AI substitute.
        self.observe()
        # Unknown cells are traversable. Remember observed fires until sensors see them clear.
        local=[(c['x'],c['y']) for c in self.memory.values() if c['burning']]
        blocked=self.danger_zone(local)
        here=(round(vehicle['x']),round(vehicle['y']))
        if here in blocked:
            options=[]
            for dx in range(-speed,speed+1):
                for dy in range(-speed,speed+1):
                    p=(here[0]+dx,here[1]+dy)
                    if math.hypot(dx,dy)<=speed and p not in blocked and 0<=p[0]<self.width and 0<=p[1]<self.height and (roads is None or p in roads):
                        path=self.route(here,p,set(local),roads)
                        distance=sum(math.dist(a,b) for a,b in zip([here]+path,path))
                        if distance<=speed and path:options.append((distance,p,path))
            if options:
                _,p,path=min(options);vehicle.update(x=float(p[0]),y=float(p[1]),route=path,status='retreating',travel_credit=0)
            else:vehicle.update(status='trapped',route=[],travel_credit=0)
            return
        target=vehicle.get('target')
        if vehicle.get('role')=='scout' and vehicle.get('mode')=='patrol' and target is not None:
            safe=self.safe_scout_waypoint(target,blocked)
            if safe!=target:
                vehicle.update(target=safe,route=[])
                self.log('autopilot',f"{vehicle['drone_id']} adjusted its patrol waypoint away from observed fire.")
                target=safe
        if target is None:
            vehicle['travel_credit']=0
            return
        path=vehicle.get('route',[])
        previous=here
        valid=bool(path) and tuple(path[-1])==tuple(target)
        if valid:
            for point in path:
                if tuple(point) not in self.neighbors(previous,blocked,roads):
                    valid=False;break
                previous=tuple(point)
        if not valid:
            had_route=bool(path)
            path=self.route(here,target,blocked,roads)
            vehicle['route_plans']=vehicle.get('route_plans',0)+1
            vehicle['planner']='optimistic_astar'
            if had_route:
                self.log('autopilot','Drone replanned its route from observed fire or a changed destination; unknown cells remain traversable.')
        vehicle['route']=path
        if not path and here!=tuple(target):
            vehicle.update(status='blocked',travel_credit=0)
            if vehicle.get('reported_blocked_target')!=list(target):
                vehicle['reported_blocked_target']=list(target)
                self.pending_decision_event=self.pending_decision_event or 'route_blocked'
                self.log('autopilot',f"{vehicle.get('drone_id','drone')} cannot reach {target} using shared remembered fire; The decision policy must choose a new safe approach or containment position.")
            return
        vehicle.pop('reported_blocked_target',None)
        budget=speed+vehicle.get('travel_credit',0)
        while path and math.dist(here,path[0])<=budget+1e-9:
            step=tuple(path.pop(0));budget-=math.dist(here,step);here=step
        vehicle.update(x=float(here[0]),y=float(here[1]),route=path,
                       travel_credit=max(0,budget) if path else 0)
        if (vehicle['x'],vehicle['y'])==tuple(target):
            vehicle.update(target=None,status=vehicle.get('mode','on_scene'),route=[])
        else:vehicle['status']='en_route'

    def safe_drone_positions(self, drone=None):
        drone=self.drone if drone is None else drone
        blocked=self.danger_zone([(c['x'],c['y']) for c in self.memory.values() if c['burning']])
        candidates=[]
        strength=math.hypot(*self.wind)
        unit=tuple(v/strength for v in self.wind) if strength else (0,0)
        projection=lambda p:p[0]*unit[0]+p[1]*unit[1]
        front=max((projection((f['x'],f['y'])) for f in self.observation),default=0)
        leading=[f for f in self.observation if not strength or projection((f['x'],f['y']))>=front-1]
        for c in self.memory.values():
            p=(c['x'],c['y'])
            if c['observed_at']==self.tick and not c['burning'] and p not in blocked:
                fires=sum(math.hypot(f['x']-p[0],f['y']-p[1])<=self.rules['drone_suppression_range'] for f in self.observation)
                if fires:
                    coverage=sum(math.hypot(f['x']-p[0],f['y']-p[1])<=self.rules['drone_suppression_range'] for f in leading)
                    offset=projection(p)-front if strength else 0
                    distance=math.hypot(p[0]-drone['x'],p[1]-drone['y'])
                    # Supply safe tactical options; the policy still chooses the mission and target.
                    rank=(-coverage,-int(offset>=0) if strength else 0,-fires,distance,p)
                    candidates.append((rank,dict(x=p[0],y=p[1],downwind_front_reachable=coverage,
                                                 downwind_offset=round(offset,2))))
        return [candidate for _,candidate in sorted(candidates,key=lambda item:item[0])[:12]]

    def truck_edge_time(self, start, end):
        road=tuple(start) in self.roads and tuple(end) in self.roads
        if start[0]!=end[0] and start[1]!=end[1]:
            road=road and (start[0],end[1]) in self.roads and (end[0],start[1]) in self.roads
        speed=self.rules['truck_cells_per_step']*(1 if road else self.rules['truck_offroad_speed_factor'])
        return math.dist(start,end)/speed

    def truck_route(self, start, goals, blocked):
        # Fastest travel-time path: roads are faster, but off-road shortcuts are allowed.
        start=tuple(map(round,start));queue=[(0,start)];costs={start:0};parents={start:None}
        while queue:
            cost,p=heapq.heappop(queue)
            if cost>costs[p]:continue
            if p in goals:
                path=[]
                while parents[p] is not None:path.append(list(p));p=parents[p]
                return path[::-1],cost
            for q in self.neighbors(p,blocked):
                candidate=cost+self.truck_edge_time(p,q)
                if candidate<costs.get(q,float('inf')):
                    costs[q]=candidate;parents[q]=p;heapq.heappush(queue,(candidate,q))
        return None,None

    def suppress(self, candidates, jets, success_probability):
        attempts=[]
        for x,y in list(dict.fromkeys(candidates))[:jets]:
            if not self.burning(self.cells[y][x]):continue
            success=self.suppression_rng.random()<success_probability
            cell=self.cells[y][x]
            before=cell['heat']
            if success:
                cell['heat']=max(0.,round(before-self.rules['jet_cooling'],6))
                cell['wet']=self.rules['wet_steps']
            attempts.append(dict(x=x,y=y,success=success,extinguished=success and cell['heat']==0,
                                 intensity_before=round(before,3),intensity_after=round(cell['heat'],3)))
        return attempts

    def update_trucks(self):
        primary=self.truck
        for truck in self.trucks:
            self.truck=truck
            if truck is not primary:self.crew_target,self.crew_due=truck.get('crew_target'),truck.get('crew_due')
            self.update_truck()
            truck.update(crew_target=self.crew_target,crew_due=self.crew_due)
        self.truck=primary
        self.crew_target,self.crew_due=primary.get('crew_target'),primary.get('crew_due')

    def update_truck(self):
        t=self.truck;t['last_drops']=[];t['suppression_attempts']=[]
        if self.phase == 'finished':return
        self.observe()
        visible=[(c['x'],c['y']) for c in self.observation]
        returning=self.phase=='returning'
        if not returning and (t['mobilized_at'] is None or self.tick<t['mobilized_at']):return
        if visible and not t.get('drone_order'):
            self.crew_target=list(min(visible,key=lambda p:math.hypot(p[0]-t['x'],p[1]-t['y'])))
        if not returning and not self.crew_target:return
        here=(round(t['x']),round(t['y']))
        blocked=self.danger_zone(visible)
        retreat=here in blocked
        if returning:
            goals={self.base};obstacles=blocked
        elif retreat:
            goals={(x,y) for y in range(max(0,here[1]-3),min(self.height,here[1]+4))
                   for x in range(max(0,here[0]-3),min(self.width,here[0]+4)) if (x,y) not in blocked}
            obstacles=set(visible)
        else:
            # Follow shared active sightings within the assigned sector without replacing the order.
            nearby=[p for p in visible if math.dist(p,self.crew_target)<=self.rules['hose_range']]
            aim=max(nearby,key=lambda p:(p[0]*self.wind[0]+p[1]*self.wind[1],-math.dist(p,self.crew_target))) if nearby else self.crew_target
            fx,fy=aim;radius=self.rules['truck_approach_range']
            goals={(x,y) for y in range(max(0,fy-radius),min(self.height,fy+radius+1))
                   for x in range(max(0,fx-radius),min(self.width,fx+radius+1))
                   if 3<=math.hypot(x-fx,y-fy)<=radius and (x,y) not in blocked}
            # Prefer close observation, but retain a reachable outer attack position
            # when a broad front blocks all close approaches to the assigned sector.
            if not goals:
                radius=self.rules['hose_range']
                goals={(x,y) for y in range(max(0,fy-radius),min(self.height,fy+radius+1))
                       for x in range(max(0,fx-radius),min(self.width,fx+radius+1))
                       if 3<=math.hypot(x-fx,y-fy)<=radius and (x,y) not in blocked}
            obstacles=blocked
        path,cost=self.truck_route(here,goals,obstacles)
        if path is None:
            t.update(status='trapped' if retreat else 'blocked',route=[],travel_credit=0)
            self.crew_due=None;return
        budget=1+t.get('travel_credit',0)
        while path and self.truck_edge_time(here,path[0])<=budget+1e-9:
            step=tuple(path.pop(0));budget-=self.truck_edge_time(here,step);here=step
        t.update(x=float(here[0]),y=float(here[1]),route=path,
                 target=path[-1] if path else None,travel_credit=max(0,budget) if path else 0,
                 terrain='road' if here in self.roads else 'offroad',
                 status=('returning' if path else 'at_station') if returning else 'retreating' if retreat else 'en_route' if path else 'on_scene')
        # Use the remaining weighted route and accumulated fractional movement for ETA.
        remaining=0;point=here
        for step in path:remaining+=self.truck_edge_time(point,step);point=tuple(step)
        self.crew_due=self.tick+math.ceil(max(0,remaining-t['travel_credit']))
        self.observe()
        visible=[(c['x'],c['y']) for c in self.observation]
        local=[p for p in visible if math.hypot(p[0]-t['x'],p[1]-t['y'])<=self.rules['hose_range']]
        if local and here not in self.danger_zone(visible):
            attempts=self.suppress(sorted(local,key=lambda p:(-(p[0]*self.wind[0]+p[1]*self.wind[1]),math.hypot(p[0]-t['x'],p[1]-t['y']))),self.rules['truck_jets'],self.rules['truck_suppression_success_probability'])
            t['suppression_attempts']=attempts
            self.crew_extinguished+=sum(a['extinguished'] for a in attempts)
            t['last_drops']=[[a['x'],a['y']] for a in attempts]
            t['status']='suppressing'
        self.observe()
        if not returning:
            if t['status'] in {'en_route','on_scene','suppressing'}:
                self._truck_milestone(t,'deployed',f"{t.get('truck_id','engine-1')} deployed from station toward sector {self.crew_target}.")
            if t['status'] in {'on_scene','suppressing'}:
                self._truck_milestone(t,'arrived',f"{t.get('truck_id','engine-1')} arrived at the fire sector.")
            if t['status']=='suppressing':
                self._truck_milestone(t,'containing',f"{t.get('truck_id','engine-1')} containing: hoses on the fire.")

    def truck_telemetry(self, truck=None):
        truck=self.truck if truck is None else truck
        due=self.crew_due if truck is self.truck else truck.get("crew_due")
        offroad=self.rules['truck_cells_per_step']*self.rules['truck_offroad_speed_factor']
        return dict(truck,truck_id=truck.get('truck_id','engine-1'),speed=self.rules['truck_cells_per_step'],
                    preferred_approach_range=self.rules['truck_approach_range'],standoff_cells=3,offroad_speed=offroad,can_travel_offroad=True,sensor_radius=self.truck_sensor_radius,hose_range=self.rules['hose_range'],jets=self.rules['truck_jets'],
                    suppression_success_probability=self.rules['truck_suppression_success_probability'],expected_successful_jet_hits_per_step=3.0,
                    position_reported_at=self.tick,arrival_estimate_steps=max(0,due-self.tick) if due is not None else None)

    def observe(self):
        current={}
        for source,vehicle,radius in [(d['drone_id'],d,self.extinguisher_sensor_radius) for d in self.extinguishers]+[(t['truck_id'],t,self.truck_sensor_radius) for t in self.trucks]+[(d['drone_id'],d,self.scout_sensor_radius) for d in self.scouts]:
            own=[]
            for y in range(max(0,int(vehicle['y'])-radius),min(self.height,int(vehicle['y'])+radius+1)):
                for x in range(max(0,int(vehicle['x'])-radius),min(self.width,int(vehicle['x'])+radius+1)):
                    if math.hypot(x-vehicle['x'],y-vehicle['y'])<=radius:
                        key=f'{x},{y}'
                        cell=current.setdefault(key,dict(x=x,y=y,burning=self.burning(self.cells[y][x]),
                                                         observed_at=self.tick,sources=[]))
                        cell.update(intensity=round(self.cells[y][x]['heat'],3),terrain=self.cells[y][x].get('terrain','field'),burned_fraction=round(self.cells[y][x].get('burned',0),3),wet_steps_remaining=self.cells[y][x].get('wet',0))
                        cell['sources'].append(source)
                        if cell['burning']:own.append(dict(x=x,y=y))
            vehicle['observed_fire']=own
            if own and vehicle.get('drone_id') and source not in self._fire_reported_by:
                # First confirmed sighting by this drone: write it to the shared state as fire_detected.
                self._fire_reported_by.add(source)
                first=min(own,key=lambda c:math.hypot(c['x']-vehicle['x'],c['y']-vehicle['y']))
                self.emit('fire_detected',source,first['x'],first['y'],vehicle_id=source,role=vehicle.get('role','drone'),
                          cells=len(own),district_id=self.nearest_district(first['x'],first['y']),
                          detail=f"{source} confirms {len(own)} burning cell(s) near ({first['x']}, {first['y']})")
        self.memory.update(current)
        self.observation=[dict(x=c['x'],y=c['y']) for c in current.values() if c['burning']]
        if self.trucks and self.observation and not self.truck.get('drone_order'):
            self.crew_target = [self.observation[0]['x'],self.observation[0]['y']]
        return self.observation

    def telemetry(self, drone=None):
        drone=self.drone if drone is None else drone
        return dict(drone,drone_id=drone.get('drone_id','drone-1'),role='extinguisher',jets=self.rules['drone_jets'],suppression_success_probability=self.rules['drone_suppression_success_probability'],expected_successful_jet_hits_per_step=0.4,sensor_radius=self.extinguisher_sensor_radius,standoff_cells=3,suppression_range=self.rules['drone_suppression_range'],safe_containment_positions=self.safe_drone_positions(drone),capabilities=['scout','contain','evacuate_farm','evacuate_town'])

    def population_wind_alignment(self):
        sources=[(f['x'],f['y']) for f in self.observation]
        basis='local observed fire'
        if not sources and self.called:sources=[self.report];basis='uncertain farmer smoke report'
        strength=math.hypot(*self.wind)
        result={}
        for name,g in self.groups.items():
            measurements=[]
            for x,y in sources:
                dx,dy=g['x']-x,g['y']-y
                distance=math.hypot(dx,dy)
                dot=dx*self.wind[0]+dy*self.wind[1]
                cosine=dot/(distance*strength) if distance and strength else 0
                measurements.append(dict(source=[x,y],distance=round(distance,2),
                    directional_cosine=round(cosine,3),downwind_sector=dot>0 and cosine>=0.7))
            result[name]=dict(basis=basis,people_status=g['status'],
                downwind_sector=any(m['downwind_sector'] for m in measurements),
                nearby_smoke_or_fire=any(m['distance']<=8 for m in measurements),
                measurements=measurements)
        return result

    def smoke_scout_positions(self):
        if not self.called:return []
        blocked=self.danger_zone([(c['x'],c['y']) for c in self.memory.values() if c['burning']])
        rx,ry=self.report
        candidates=[]
        for y in range(max(0,ry-4),min(self.height,ry+5)):
            for x in range(max(0,rx-4),min(self.width,rx+5)):
                distance=math.hypot(x-rx,y-ry)
                if 3<=distance<=4 and (x,y) not in blocked:
                    downwind=(x-rx)*self.wind[0]+(y-ry)*self.wind[1]
                    alignment=downwind/(distance*math.hypot(*self.wind)) if any(self.wind) else 0
                    candidates.append(((alignment<0.7 if any(self.wind) else False,distance,-alignment,math.dist((x,y),(self.drone['x'],self.drone['y']))),
                                       dict(x=x,y=y,distance_from_report=round(distance,2),wind_alignment=round(alignment,3))))
        return [p for _,p in sorted(candidates,key=lambda item:item[0])[:12]]

    def known_map(self):
        # This map never includes cells outside sensor observations. Static landmarks are separate.
        cells=self.memory
        t=self.truck
        rows=[['?']*self.width for _ in range(self.height)]
        for c in cells.values():
            fresh=c['observed_at']==self.tick
            rows[c['y']][c['x']]=('F' if fresh else 'f') if c['burning'] else ('.' if fresh else ',')
        return dict(format='text-grid',width=self.width,height=self.height,origin='top-left; x column, y row',
                    legend={'?':'unobserved','.':'observed clear now',',':'previously clear; stale',
                            'F':'observed fire now','f':'previously burning; stale'},
                    rows=[''.join(row) for row in rows],captured_at=self.tick,
                    stale_observation_ticks=sorted({c['observed_at'] for c in cells.values() if c['observed_at']!=self.tick}),
                    landmarks=dict(farm=list(self.farm),town=list(self.town),station=list(self.base)),
                    smoke_report=list(self.report) if self.called else None,
                    drone_position=[self.drone['x'],self.drone['y']] if self.extinguishers else None,truck_position=[t['x'],t['y']] if self.trucks else None,
                    satellite=self.satellite)

    def mission_context(self):
        records=copy.deepcopy(self.mission_records[-12:])
        if records:
            latest=records[-1]
            latest['outcome_so_far']=dict(observed_at=self.tick,elapsed_steps=self.tick-latest['issued_at'],
                drone_status=self.drone['status'],drone_position=[self.drone['x'],self.drone['y']],
                scouts=[dict(drone_id=d['drone_id'],position=[d['x'],d['y']],status=d['status']) for d in self.scouts],
                drone_cells_extinguished=self.suppressed-latest['baseline']['drone_extinguished'],
                truck_cells_extinguished=self.crew_extinguished-latest['baseline']['truck_extinguished'],
                people={k:g['status'] for k,g in self.groups.items()},last_action_result=self.last_result)
        return records

    def payload(self, event_type='local_observation'):
        self.observe()
        contacts = contact_directory(self.groups)
        channels = {d['district_id']: d for d in contacts['districts']}
        known = dict(width=self.width,height=self.height,wind=dict(dx=self.wind[0],dy=self.wind[1],strength=round(math.hypot(*self.wind),2),units="relative simulation strength",convention="positive X east, positive Y south; vector points TO spread",spread_steps={name:self.spread_interval(dx,dy) for name,dx,dy in [("east",1,0),("west",-1,0),("north",0,-1),("south",0,1)]}),
            forecast=dict(issued_at=self.tick,description='Synthetic forecast; arbitrary X/Y vector points TO destination, including diagonal and calm wind. wind.spread_steps gives directional ignition ATTEMPT intervals, not guaranteed propagation times. Ignition base probability is 50%, modified by terrain, source intensity and diagonal distance; stronger downwind wind shortens the interval, while upwind spread is slower. Failed attempts retry; predict uncertain fire arrival, not exact fronts. Assess settlement alignment with the full vector, not just named cardinal presets.'),
            farmer_report_location=dict(x=self.report[0],y=self.report[1]) if self.called else None,
            scenario_instructions="The current districts list is authoritative: four town neighbourhoods plus one farm. Ignore older prompts enumerating only North/Centre/South or four total districts. Assess and target each current district_id separately. The ignition point is user-selected. Ignore fixed-coordinate examples. Assess life risk from farmer_report_location and forecast BEFORE scouting. Strong wind (magnitude strictly >1.8 in demo units) toward unwarned residents requires a scout to physically deliver evacuation warnings BEFORE smoke investigation or map-wide patrol, without waiting for thermal confirmation. Continue until warning delivery; then warn remaining threatened districts or resume map-wide scouting. Remote messages for these districts must use action=inform so they do not bypass physical arrival. Otherwise scout from safe stand-off.",
            farm=dict(zip(("x","y"),self.farm)),town=dict(zip(("x","y"),self.town)),station=dict(zip(("x","y"),self.base)),
            geography={k:v for k,v in GEOGRAPHY.items() if k not in ("roads","image_source")},
            terrain_map=dict(description='Static approximate land cover; not live fire observations',legend={k[0].upper():k for k in PROFILES if k!='built'},built_symbol='U',rows=[''.join('U' if c['terrain']=='built' else c['terrain'][0].upper() for c in row) for row in self.cells]),
            districts=[dict(district_id=key,name=g['name'],kind=g['kind'],home=g['home'],
                position=[g['x'],g['y']],population=g['count'],population_basis=g['population_basis'],
                burnt=g['burnt'],status=g['status'],refuge=g['refuge'],
                chat_id=channels.get(key,{}).get('chat_id'),evacuation_point=channels.get(key,{}).get('evacuation_point'),
                boundary=next(z['polygon'] for z in GEOGRAPHY['observation_zones'] if z['id']==key))
                for key,g in self.groups.items()],
            contacts=contacts,
            communications_sent=self.communications[-12:],last_dispatch=self.dispatch,
            evacuation_targets=[dict(district_id=key,name=g['name'],count=g['count'],
                command='evacuate_farm' if g['kind']=='farm' else 'evacuate_town',
                target_x=g['home'][0],target_y=g['home'][1])
                for key,g in self.groups.items() if g['status']=='unwarned'],
            population_wind_alignment=self.population_wind_alignment(),
            satellite=self.satellite,rules=self.rules,observed_fire_details=[c for c in self.memory.values() if c['observed_at']==self.tick and c['burning']],people=self.groups,fire_truck=self.truck_telemetry() if self.trucks else None,fire_trucks=[self.truck_telemetry(t) for t in self.trucks],
            firefighters_eta=max(0,self.crew_due-self.tick) if self.crew_due else None,
            mission=self.mission,last_action_result=self.last_result,
            fleet=[self.telemetry(d) for d in self.extinguishers]+self.scout_telemetry(),fleet_counts=self.fleet_counts(),scout_reports=self.scout_reports,
            fleet_policy='SHARED OBSERVATIONS AND REPLANNING: Scout fire confirmation is sufficient for Squirtle to act; it need not reach its original smoke waypoint or personally observe the flames. burning_cells and each extinguisher safe_containment_positions use shared sensors. On scout_fire_confirmation or route_blocked, reassess immediately: choose containment from a validated safe position protecting the advancing front toward threatened population, considering wind and district geometry. Otherwise select a DIFFERENT reachable safe flank to regain observation; never repeat reported_blocked_target unchanged. Preserve urgent evacuation priority. Unknown/stale cells do not prove current fire or clearance. District protection must follow actual evidence, not always target town. DISTRICT RESERVATIONS: Scout orders returned by delegate_scout reserve their evacuation districts, including continue on an active warning. Never assign an extinguisher or another scout to the same district. On coordination_conflict, read last_result and assign held vehicles useful nonduplicate work. RAPID PAIRED RESPONSE: drone-1 is named Squirtle (keep drone_id unchanged in commands). Scouts observe radius 16 while extinguisher drones observe radius 10; current telemetry overrides older range claims. When shared sensors detect no fire, scouts must keep moving through high-information unobserved or stale sectors rather than hold. After a credible smoke/fire warning, when a scout and an extinguisher are available, normally dispatch BOTH in the same response toward safe approaches to the reported focus. Do not hold Squirtle at base waiting for the scout to arrive. Scout reconnoiters and reports; Squirtle approaches alongside on a complementary safe flank, then starts containment at the next decision as soon as confirmed fire and a validated safe containment position exist. For unconfirmed smoke use scout movement for Squirtle, not blind suppression. Urgent district warnings, unsafe approaches, or higher-priority existing missions override pairing; explain any exception. Maintain three-cell clearance and prioritize the downwind front. Use fleet and fire_trucks as the exact available inventory; any role can have zero to three vehicles. Assign every listed ID, never invent absent resources. Scouts patrol agent-selected waypoints, can deliver district evacuation warnings but cannot suppress, and report separate observed fires. Prioritize each observed focus by population exposure and wind, not discovery order. Hidden ignitions are never included.',
            mission_context=self.mission_context(),known_map=self.known_map(),smoke_scout_positions=self.smoke_scout_positions(),
            memory=[e for e in self.history if e['source']!='simulation'][-8:])
        return dict(event_id=str(uuid.uuid4()),event_type=event_type,incident_id=self.incident_id,sim_time=str(self.tick),
            world_state=json.dumps(known),drone_telemetry=json.dumps(self.telemetry() if self.extinguishers else {'available':False,'safe_containment_positions':[]}),
            thermal_detections=json.dumps(dict(observed_at=self.tick,burning_cells=[c for c in self.memory.values() if c['observed_at']==self.tick and c['burning']],
                coverage=f'Joint current observations: radius {self.scout_sensor_radius} around scouts, {self.extinguisher_sensor_radius} around extinguisher drones and {self.truck_sensor_radius} around trucks. All share fire and clear-cell updates. Empty is not global containment.')),
            human_messages=self.call_text,
            contacts=json.dumps(contacts, ensure_ascii=False))

    DELIVERED_STATUSES={'sent','delivered','succeeded'}
    EVACUATION_ACTIONS={'evacuate','evacuar','evacuacion','evacuación','orden_evacuacion','evacuation_order'}
    INFORM_ACTIONS={'inform','informar','informativa','informativo','information','update','reassure','tranquilizar','all_clear'}
    EVACUATION_CRITICALITIES={'critica','crítica','critical','alta','high','emergencia','emergency'}

    @classmethod
    def alert_action(cls,item):
        """Split a zone alert into an evacuation ORDER or an informational broadcast.

        Explicit `action` wins. Without it we fall back to `criticality` and mark the record
        inferred, so the dashboard and the next payload show the agent that it never said which
        one it meant. Anything unlabelled is an information broadcast: a reassurance message
        must never put a district on the road.
        """
        raw=str(item.get('action') or item.get('alert_type') or '').strip().lower()
        if raw in cls.EVACUATION_ACTIONS:return 'evacuate','explicit'
        if raw in cls.INFORM_ACTIONS:return 'inform','explicit'
        criticality=str(item.get('criticality') or '').strip().lower()
        if criticality in cls.EVACUATION_CRITICALITIES:return 'evacuate','inferred_from_criticality'
        return 'inform','default'

    def apply_communications(self, communications):
        """Record simulated calls and alerts. A zone alert either orders its
        district to evacuate or informs it; a call reaches one person."""
        applied=[]
        for item in communications or []:
            if not isinstance(item,dict):continue
            kind=item.get('kind')
            if kind not in {'zone_alert','call','personal_message'}:continue
            record=dict(tick=self.tick,kind=kind,channel='telegram' if kind!='call' else 'phone',
                        district_id=item.get('district_id') or None,contact_name=str(item.get('contact_name',''))[:120],
                        criticality=str(item.get('criticality',''))[:16],status=str(item.get('status','sent'))[:40],
                        information=str(item.get('information',''))[:600],run_id=item.get('run_id'))
            group=self.groups.get(record['district_id']) if record['district_id'] else None
            label=group['name'] if group else (record['contact_name'] or 'unknown recipient')
            if kind=='zone_alert':
                action,source=self.alert_action(item)
                record.update(action=action,action_source=source)
                delivered=record['status'] in self.DELIVERED_STATUSES
                if not group:
                    # No district resolved: nobody was warned. Say so instead of logging a silent success.
                    record['effect']='no_recipient'
                    self.log('central → telegram',f"Zone alert to {label} [{record['status']}] reached NO district "
                             f"(district_id={record['district_id']!r}); population unchanged: {record['information'][:160]}")
                elif action=='evacuate' and group['status']=='unwarned' and delivered:
                    group['status']='evacuating'
                    record['effect']='district_warned'
                    self.pending_decision_event=self.pending_decision_event or 'evacuation_warning_delivered'
                    self.log('central → telegram',f"EVACUATION order ({record['criticality'] or 'n/a'}) to {label}: {group['count']} people moving to refuge. {record['information'][:200]}")
                elif action=='inform':
                    record['effect']='district_informed'
                    self.log('central → telegram',f"Information broadcast to {label} [{record['status']}] — "
                             f"{group['count']} people informed, NOT evacuated (status stays {group['status']}): {record['information'][:200]}")
                else:
                    record['effect']='no_change'
                    self.log('central → telegram',f"Evacuation order to {label} [{record['status']}]"+(f" — already {group['status']}" if group else '')+f": {record['information'][:200]}")
                if source!='explicit' and group:
                    self.log('system',f"Zone alert to {label} carried no explicit action; read as {action!r} from "
                             f"criticality={record['criticality'] or 'empty'!r}. Set action=evacuate|inform in alertar_zona.")
            elif kind=='call':
                record['effect']='person_called'
                self.log('central → phone',f"Call to {label}{' ('+group['name']+')' if group else ''} [{record['status']}]: {record['information'][:200]}")
            else:
                record['effect']='person_informed'
                self.log('central → telegram',f"Message to {label} [{record['status']}]: {record['information'][:200]}")
            applied.append(record)
        self.communications=(self.communications+applied)[-50:]
        if applied:self.update_people_exposure()
        return applied

    def record_dispatch(self, dispatch):
        """Store Despacho Central's structured decision for the dashboard and next payload."""
        if not isinstance(dispatch,dict):return
        self.dispatch=dict(tick=self.tick,**{k:(str(v)[:800] if isinstance(v,str) else v) for k,v in dispatch.items() if k in {'decision','justificacion','criticidad','avisos_lanzados','destinatarios','datos_faltantes'}})
        summary=f"Dispatch: {self.dispatch.get('decision','?')} · {self.dispatch.get('criticidad') or 'n/a'}"
        if self.dispatch.get('destinatarios'):summary+=f" · warned: {self.dispatch['destinatarios']}"
        if self.dispatch.get('datos_faltantes'):summary+=f" · missing: {self.dispatch['datos_faltantes']}"
        self.log('central',summary[:600])

    def validate_drone_order(self, decision, drone):
        command = decision.get('command')
        if command not in {'scout','contain','evacuate_farm','evacuate_town','hold'}:
            raise ValueError('Unsupported drone command.')
        x,y = decision.get('target_x'),decision.get('target_y')
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or int(v)!=v for v in (x,y)):
            raise ValueError('Target must use integer grid coordinates.')
        x,y = int(x),int(y)
        if not (0<=x<self.width and 0<=y<self.height):
            raise ValueError('Target outside map.')
        self.observe()
        if command=='contain' and (x,y) not in {(c['x'],c['y']) for c in self.safe_drone_positions(drone)}:
            raise ValueError('Containment position unsafe or ineffective. Choose exact coordinates from drone_telemetry.safe_containment_positions; these are flight positions, NOT burning targets.')
        if command in {'scout','contain'} and (x,y) in self.danger_zone([(c['x'],c['y']) for c in self.observation]):
            raise ValueError('Flight target violates the 3-cell fire stand-off. Scout from outside the burning area.')
        evacuation_group = None
        if command.startswith('evacuate_'):
            kind='farm' if command=='evacuate_farm' else 'town'
            candidates={key:g for key,g in self.groups.items() if g['kind']==kind and g['status']=='unwarned'}
            if not candidates:
                raise ValueError('No unwarned districts for this evacuation command; choose another mission.')
            requested=decision.get('district_id')
            if not isinstance(requested,str) or requested not in candidates:
                raise ValueError('District must be an unwarned group of the requested kind.')
            evacuation_group=requested
            x,y=candidates[evacuation_group]['home']
        elif decision.get('district_id') not in (None,''):
            raise ValueError('district_id must be empty for non-evacuation commands.')
        return command,x,y,evacuation_group

    @staticmethod
    def order_array(raw, vehicles, id_field, legacy=None):
        if raw is None:
            if not vehicles:return []
            if len(vehicles)==1 and legacy is not None:return [dict(legacy,**{id_field:vehicles[0][id_field]})]
            raise ValueError('Supply an order for every vehicle ID.')
        if isinstance(raw,str):
            try:raw=json.loads(raw)
            except ValueError:raise ValueError('Vehicle orders must be a JSON array.')
        if not isinstance(raw,list) or any(not isinstance(o,dict) for o in raw):raise ValueError('Vehicle orders must be an array of objects.')
        ids=[o.get(id_field) for o in raw]
        if any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or set(ids)!={v[id_field] for v in vehicles}:
            raise ValueError('Orders must cover every configured vehicle exactly once; no unknown IDs.')
        return raw

    def apply(self, decision, command_id, incident_id, expected_tick):
        if self.phase != 'active':raise ValueError('Incident resolved; vehicles are returning or at station.')
        if incident_id!=self.incident_id or expected_tick!=self.tick:raise ValueError('Stale decision: request a new decision.')
        if command_id in self.seen_commands:raise ValueError('Duplicate command rejected.')
        self.observe()
        drone_orders=self.order_array(decision.get('extinguisher_orders'),self.extinguishers,'drone_id',decision)
        scout_orders=self.validate_scout_orders(decision.get('scout_orders'))
        legacy_truck=dict(command=decision.get('truck_command','continue'),target_x=decision.get('truck_target_x'),target_y=decision.get('truck_target_y'),reason=decision.get('truck_reason',''))
        truck_orders=self.order_array(decision.get('truck_orders'),self.trucks,'truck_id',legacy_truck)
        planned=[];reservations={};adjustments=[]
        for order in drone_orders:
            drone=next(d for d in self.extinguishers if d['drone_id']==order['drone_id'])
            command,x,y,district=self.validate_drone_order(order,drone)
            if district:reservations.setdefault(district,[]).append((drone,order,len(planned)))
            planned.append((drone,command,x,y,district))
        for order in scout_orders:
            scout=next(d for d in self.scouts if d['drone_id']==order['drone_id'])
            district=order.get('district_id') if order['command'].startswith('evacuate_') else scout.get('evacuation_group') if order['command']=='continue' and scout['mode'].startswith('evacuate_') else None
            if district and self.groups[district]['status']=='unwarned':
                reservations.setdefault(district,[]).append((scout,order,None))
        for order in truck_orders:
            if order.get('command') not in {'attack_sector','continue','hold'}:raise ValueError('Truck command must be attack_sector, continue or hold.')
            if order['command']=='attack_sector':
                tx,ty=order.get('target_x'),order.get('target_y')
                if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or int(v)!=v for v in (tx,ty)):raise ValueError('Truck sector requires integer coordinates.')
                if not (0<=tx<self.width and 0<=ty<self.height):raise ValueError('Truck sector outside map.')
                if not isinstance(order.get('reason'),str) or not order['reason'].strip():raise ValueError('Truck order requires an explanation.')
        # Resolve only duplicate reservations; do not invent new strategic destinations.
        for district,entries in reservations.items():
            if len(entries)<2:continue
            winner=min(entries,key=lambda e:(not (e[0].get('evacuation_group')==district and e[0]['mode'].startswith('evacuate_')),e[0]['role']!='scout',e[0]['drone_id']))
            for vehicle,order,index in entries:
                if vehicle is winner[0]:continue
                reason=f"Duplicate warning for {district}: retained {winner[0]['drone_id']}; {vehicle['drone_id']} held for reassignment."
                adjustments.append(reason)
                order.update(command='hold',district_id='',reason=reason)
                if index is None:order['waypoints']=[]
                else:
                    x,y=int(vehicle['x']),int(vehicle['y'])
                    order.update(target_x=x,target_y=y)
                    planned[index]=(vehicle,'hold',x,y,None)
        decision=copy.deepcopy(decision)
        decision.update(extinguisher_orders=drone_orders,scout_orders=scout_orders,truck_orders=truck_orders)
        if adjustments:decision['coordination_adjustments']=adjustments
        if self.mission_records:self.mission_records[-1]=self.mission_context()[-1]
        # Commit only after all vehicle orders and shared district reservations validate.
        for order in truck_orders:
            truck=next(t for t in self.trucks if t['truck_id']==order['truck_id'])
            if order['command']=='attack_sector':
                target=[int(order['target_x']),int(order['target_y'])]
                truck.update(crew_target=target,drone_order=dict(command='attack_sector',sector=target,reason=order['reason'][:1000],issued_at=self.tick,issued_by='drone'))
                if truck is self.truck:self.crew_target=target
                self.log('drone → truck',f"{truck['truck_id']} attack sector {target}: {order['reason'][:500]}")
            elif order['command']=='hold':
                truck.update(crew_target=None,drone_order=dict(command='hold',issued_at=self.tick),target=None,route=[],status='holding')
                if truck is self.truck:self.crew_target=None
        for drone,command,x,y,district in planned:
            drone.update(evacuation_group=district,mode=command,target=None if command=='hold' else [x,y],status='holding' if command=='hold' else 'en_route',sector=[x,y])
            self.log(drone['drone_id'],f'{command} at ({x}, {y})'+(f' — {district}' if district else ''))
        for order in scout_orders:
            scout=next(d for d in self.scouts if d['drone_id']==order['drone_id'])
            if order['command']!='continue':
                points=copy.deepcopy(order.get('waypoints',[])) if order['command']=='patrol' else []
                district=order.get('district_id') if order['command'].startswith('evacuate_') else None
                target=list(self.groups[district]['home']) if district else points.pop(0) if points else None
                scout.update(mode=order['command'],evacuation_group=district,status='en_route' if target else 'holding',target=target,waypoints=points,route=[])
            self.log('scout agent',f"{scout['drone_id']}: {order['reason']}")
        self.mission_records.append(dict(issued_at=self.tick,decision=copy.deepcopy(decision),wind=list(self.wind),baseline=dict(drone_extinguished=self.suppressed,truck_extinguished=self.crew_extinguished)))
        self.mission_records=self.mission_records[-128:]
        self.seen_commands.add(command_id)
        self.mission=str(decision.get('mission',''))[:500]
        first=planned[0] if planned else (None,'hold',0,0,None)
        self.last_result=dict(command=first[1],target=list(first[2:4]),district_id=first[4],accepted_at=self.tick,command_id=command_id)
        if adjustments:
            self.last_result['coordination_adjustments']=adjustments
            self.pending_decision_event='coordination_conflict'
            for message in adjustments:self.log('system',message)
        self.log('central',self.mission)
        self.log('edge',str(decision.get('reason',''))[:1000],decision=decision)
        return self.last_result

    def masked_contacts(self):
        """Directory for the dashboard: real numbers/IDs stay in the payload, the browser only sees masked tails."""
        def mask(value):
            if not value:return None
            value=str(value)
            return '…'+value[-3:] if len(value)>3 else '…'
        contacts=contact_directory(self.groups)
        return dict(people=[dict(p,phone_number=mask(p['phone_number']),chat_id=mask(p['chat_id'])) for p in contacts['people']],
                    districts=[dict(d,chat_id=mask(d['chat_id'])) for d in contacts['districts']],
                    missing=contacts['missing'])

    def state(self):
        burning = sum(self.burning(c) for row in self.cells for c in row)
        return copy.deepcopy(dict(incident_id=self.incident_id,tick=self.tick,phase=self.phase,width=self.width,height=self.height,
            geography={k:v for k,v in GEOGRAPHY.items() if k not in ("roads","image_source")},
            cells=self.cells,drone=self.telemetry() if self.extinguishers else None,extinguishers=[self.telemetry(d) for d in self.extinguishers],trucks=[self.truck_telemetry(t) for t in self.trucks],fleet_counts=self.fleet_counts(),scouts=self.scout_telemetry(),drone_count=len(self.extinguishers)+len(self.scouts),wind=self.wind,base=self.base,town=self.town,farm=self.farm,
            ignition_point=self.report,report=self.report if self.called else None,ignited=self.ignited,called=self.called,mission=self.mission,
            observation=self.observation,observed_cells=list(self.memory.values()),satellite=self.satellite,
            history=self.history,mission_context=self.mission_context(),burning=burning,burned=sum(c.get('burned',0)>0 for row in self.cells for c in row),
            burnt_people=sum(g.get('burnt',0) for g in self.groups.values()),
            extinguished=self.suppressed,contained=self.ignited and burning==0,people=self.groups,
            communications=self.communications,dispatch=self.dispatch,contacts=self.masked_contacts(),events=self.event_log[-30:],
            truck=self.truck_telemetry() if self.trucks else None,roads=sorted(self.roads),crew_due=self.crew_due,crew_target=self.crew_target,crew_extinguished=self.crew_extinguished,rules=self.rules))
