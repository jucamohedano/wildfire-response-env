"""Deterministic observation-bounded fleet policy."""
import math


class DeterministicFleetPolicy:
    def decide(self, sim):
        sim.observe()
        urgent = self._urgent_districts(sim)
        reserved = set()
        scout_orders = []
        for scout in sim.scouts:
            district = self._next_district(sim,urgent,reserved)
            if district:
                reserved.add(district)
                group = sim.groups[district]
                scout_orders.append(dict(drone_id=scout['drone_id'],command='evacuate_farm' if group['kind']=='farm' else 'evacuate_town',district_id=district,waypoints=[],reason=f"Avisar a {group['name']} porque está expuesto a sotavento."))
            elif scout.get('mode','').startswith('evacuate_') and scout.get('evacuation_group') in sim.groups and sim.groups[scout['evacuation_group']]['status']=='unwarned':
                scout_orders.append(dict(drone_id=scout['drone_id'],command='continue',district_id='',waypoints=[],reason='Continuar la misión presencial de aviso en curso.'))
            else:
                points = self._scout_points(sim,scout)
                scout_orders.append(dict(drone_id=scout['drone_id'],command='patrol' if points else 'hold',district_id='',waypoints=points,reason='Inspeccionar el sector reportado desde aproximaciones seguras.' if points else 'No hay un objetivo de patrulla seguro y útil disponible.'))
        extinguisher_orders = []
        for drone in sim.extinguishers:
            district = self._next_district(sim,urgent,reserved)
            if district:
                reserved.add(district)
                group = sim.groups[district]
                extinguisher_orders.append(self._drone_order(drone,'evacuate_farm' if group['kind']=='farm' else 'evacuate_town',group['home'],f"Avisar a {group['name']} porque no hay explorador disponible para este distrito urgente.",district))
                continue
            positions = sim.safe_drone_positions(drone)
            if sim.observation and positions:
                point = positions[len(extinguisher_orders)%len(positions)]
                extinguisher_orders.append(self._drone_order(drone,'contain',[point['x'],point['y']],'Contener el fuego confirmado desde una posición segura validada por el motor.'))
                continue
            approaches = sim.smoke_scout_positions()
            if approaches:
                point = approaches[len(extinguisher_orders)%len(approaches)]
                extinguisher_orders.append(self._drone_order(drone,'scout',[point['x'],point['y']],'Aproximarse al humo no confirmado manteniendo distancia segura y sin extinción a ciegas.'))
            else:
                extinguisher_orders.append(self._drone_order(drone,'hold',[round(drone['x']),round(drone['y'])],'No hay un objetivo seguro y eficaz disponible.'))
        truck_orders = []
        observed = [(cell['x'],cell['y']) for cell in sim.observation]
        for truck in sim.trucks:
            if observed:
                target = min(observed,key=lambda point:math.dist(point,(truck['x'],truck['y'])))
                truck_orders.append(dict(truck_id=truck['truck_id'],command='attack_sector',target_x=target[0],target_y=target[1],reason='Atacar el sector confirmado más cercano usando las observaciones compartidas.'))
            elif sim.called:
                truck_orders.append(dict(truck_id=truck['truck_id'],command='attack_sector',target_x=sim.report[0],target_y=sim.report[1],reason='Movilizarse hacia el sector del humo reportado mientras los drones lo verifican.'))
            else:
                truck_orders.append(dict(truck_id=truck['truck_id'],command='hold',target_x=round(truck['x']),target_y=round(truck['y']),reason='No hay ningún aviso de incidente activo.'))
        return dict(mission=self._mission(urgent,sim),reason='Prioridades deterministas: seguridad de la población, observación y contención validada.',extinguisher_orders=extinguisher_orders,scout_orders=scout_orders,truck_orders=truck_orders)

    @staticmethod
    def _drone_order(drone,command,target,reason,district=''):
        return dict(drone_id=drone['drone_id'],command=command,target_x=int(target[0]),target_y=int(target[1]),district_id=district,reason=reason,mission=reason)

    @staticmethod
    def _urgent_districts(sim):
        if math.hypot(*sim.wind) <= 1.8:
            return []
        alignment = sim.population_wind_alignment()
        candidates = [key for key,group in sim.groups.items() if group['status']=='unwarned' and alignment[key]['downwind_sector']]
        return sorted(candidates,key=lambda key:min((m['distance'] for m in alignment[key]['measurements']),default=float('inf')))

    @staticmethod
    def _next_district(sim,urgent,reserved):
        return next((key for key in urgent if key not in reserved and sim.groups[key]['status']=='unwarned'),None)

    @staticmethod
    def _scout_points(sim,scout):
        points=[];report_seen=f'{sim.report[0]},{sim.report[1]}' in sim.memory
        if not report_seen:
            points.extend([[point['x'],point['y']] for point in sim.smoke_scout_positions()[:2]])
        if not sim.observation:
            points.extend(sim.scout_search_waypoints(scout,limit=6-len(points)))
        elif scout.get('target'):
            points.append(list(map(round,scout['target'])))
        unique=[]
        for point in points:
            if point not in unique:unique.append(point)
        return unique[:6]

    @staticmethod
    def _mission(urgent,sim):
        if urgent:
            return 'Avisar a los distritos amenazados, verificar el incidente y posicionar los medios de extinción'
        if sim.observation:
            return 'Contener el fuego confirmado manteniendo la observación compartida'
        return 'Verificar el humo y movilizar los medios hacia el sector reportado'
