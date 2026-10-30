"""Deterministic edge containment bookkeeping for a trusted simulation driver.

This module does not move units, consume host resources or simulate fire. The
host must apply the returned costs and crossing decisions in the same minute.
"""
import copy
import hashlib
import json
import math

VERSION = 'wildfire-containment-kernel-v2'


def _number(value, name, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return float(value)


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(x) is not int for x in value):
        raise ValueError('Cell must be a pair of integers')
    return tuple(value)


def _edge(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('Edge must contain two cells')
    a, b = map(_point, value)
    if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
        raise ValueError('Line edges must connect orthogonally adjacent cells')
    return tuple(sorted((a, b)))


def _id(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a nonempty identifier')
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ContainmentKernel:
    """One complete, strictly sequential minute per call; state is private.

    Parameters are explicit exercise assumptions, in abstract strength and
    pressure units. No defaults imply real-world productivity or calibration.
    """
    def __init__(self, *, work_gain, max_strength, decay, wind_factor, report_delay, wear_factor=0):
        self.config = dict(work_gain=_number(work_gain, 'work_gain'),
                           max_strength=_number(max_strength, 'max_strength'),
                           decay=_number(decay, 'decay'),
                           wind_factor=_number(wind_factor, 'wind_factor'),
                           wear_factor=_number(wear_factor, 'wear_factor'))
        if self.config['work_gain'] <= 0 or self.config['max_strength'] <= 0:
            raise ValueError('work_gain and max_strength must be positive')
        if type(report_delay) is not int or report_delay < 1:
            raise ValueError('report_delay must be a positive integer')
        self.config['report_delay'] = report_delay
        self._minute = 0
        self._lines = {}
        self._pending = []
        self._delivered = []

    def tick(self, minute, *, assignments, attacks, wind, suppressing=()):
        """Return private crossing decisions and costs; public reports are separate.

        Assignments describe already authorized, arrived, safe, working ground
        resources. One resource may hold one edge and cannot also suppress.
        Attack pressure is an input from the host, not an observed public report.
        """
        if type(minute) is not int or minute != self._minute + 1:
            raise ValueError('Tick must be the next consecutive minute')
        if not isinstance(wind, (list, tuple)) or len(wind) != 2:
            raise ValueError('wind must be a two-component vector')
        vector = tuple(_number(abs(x), 'wind') * (1 if x >= 0 else -1)
                       if type(x) in (int, float) else _number(x, 'wind') for x in wind)
        if not isinstance(assignments, (list, tuple)) or not isinstance(attacks, (list, tuple)):
            raise ValueError('assignments and attacks must be sequences')
        if not isinstance(suppressing, (list, tuple, set)):
            raise ValueError('suppressing must be a sequence of resource IDs')
        suppressing = {_id(item) for item in suppressing}
        units, prepared, sides = set(), [], {}
        for item in assignments:
            if not isinstance(item, dict):
                raise ValueError('Invalid assignment')
            resource = _id(item.get('resource_id'))
            edge = _edge(item.get('edge'))
            protected = _point(item['protected_cell']) if 'protected_cell' in item else None
            if protected is not None and protected not in edge:
                raise ValueError('Protected cell must be on the line edge')
            if edge in sides and sides[edge] != protected:
                raise ValueError('Conflicting protected side')
            sides[edge] = protected
            kind = item.get('kind')
            if kind not in ('crew', 'engine') or item.get('ready') is not True:
                raise ValueError('Only ready ground units can hold a line')
            if resource in units or resource in suppressing:
                raise ValueError('A resource cannot hold twice or suppress simultaneously')
            units.add(resource)
            work = _number(item.get('work_remaining'), 'work_remaining')
            water = _number(item.get('water_litres'), 'water_litres')
            water_cost = _number(item.get('water_cost'), 'water_cost')
            if kind == 'crew' and water_cost != 0:
                raise ValueError('Crew water cost must be zero')
            if kind == 'engine' and water_cost <= 0:
                raise ValueError('Engine water cost must be positive')
            if work < 1 or water < water_cost:
                raise ValueError('Insufficient work or water')
            prepared.append((resource, edge, water_cost, protected))
        crossings, seen = [], set()
        for item in attacks:
            if not isinstance(item, dict):
                raise ValueError('Invalid attack')
            source, target = _point(item.get('source')), _point(item.get('target'))
            edge = _edge((source, target))
            pressure = _number(item.get('pressure'), 'pressure')
            key = (source, target)
            if key in seen:
                raise ValueError('Duplicate directed crossing')
            seen.add(key)
            crossings.append((source, target, edge, pressure))
        # All inputs are checked before modifying state. Work happens before fire.
        for line in self._lines.values():
            was_positive = line['strength'] > 0
            line['strength'] = max(0., line['strength'] - self.config['decay'])
            if was_positive and line['strength'] == 0:
                line['breached'] = True
        costs = []
        for resource, edge, water_cost, protected in sorted(prepared, key=lambda item: item[0]):
            line = self._lines.setdefault(edge, dict(strength=0., breached=False, protected_cell=protected))
            if line['breached']:
                continue
            line['protected_cell'] = protected
            line['strength'] = min(self.config['max_strength'], line['strength'] + self.config['work_gain'])
            costs.append(dict(resource_id=resource, work_minutes=1, water_litres=water_cost))
        # Wear uses the strongest effective attack per undirected line per minute.
        # Cap wear at maximum strength to keep the decrement bounded.
        loads = {}
        for source, target, edge, pressure in crossings:
            downwind = max(0., vector[0] * (target[0] - source[0]) + vector[1] * (target[1] - source[1]))
            line = self._lines.get(edge)
            if line and (line.get('protected_cell') is None or line['protected_cell'] == target):
                loads[edge] = max(loads.get(edge, 0.), pressure + self.config['wind_factor'] * downwind)
        for edge, load in loads.items():
            line = self._lines.get(edge)
            if line and not line['breached'] and line['strength'] > 0:
                wear = min(self.config['max_strength'], self.config['wear_factor'] * load)
                line['strength'] = max(0., line['strength'] - wear)
                if line['strength'] == 0:
                    line['breached'] = True
        decisions = []
        for source, target, edge, pressure in sorted(crossings):
            line = self._lines.get(edge)
            downwind = max(0., vector[0] * (target[0] - source[0]) + vector[1] * (target[1] - source[1]))
            effective = pressure + self.config['wind_factor'] * downwind
            blocked = bool(line and not line['breached'] and line['strength'] > effective)
            if line and not line['breached'] and line['strength'] > 0 and not blocked:
                line['breached'] = True
                # A breach is public only when a host confirms an observation.
            decisions.append(dict(source=list(source), target=list(target), blocked=blocked))
        self._minute = minute
        due = [report for report in self._pending if report['received_at'] <= minute]
        self._pending = [report for report in self._pending if report['received_at'] > minute]
        self._delivered.extend(due)
        return dict(costs=costs, crossings=decisions)

    def restart_line(self, edge):
        """Trusted driver starts a fresh authorized mission on a failed line."""
        edge = _edge(edge)
        line = self._lines.get(edge)
        if not line or not line['breached']:
            raise ValueError('Only a failed line can be restarted')
        self._lines.pop(edge)

    def observe(self, edge, *, status):
        """Queue a host-confirmed field observation; never reveal hidden state."""
        edge = _edge(edge)
        if self._minute < 1 or status not in ('holding', 'breached', 'unknown'):
            raise ValueError('Observation needs a completed minute and valid status')
        # The host supplies its observed status; it may be stale or mistaken.
        self._pending.append(dict(edge=[list(p) for p in edge], status=status,
                                  observed_at=self._minute,
                                  received_at=self._minute + self.config['report_delay']))

    def reports(self):
        """Only delivered reports; reading never changes time or state."""
        return copy.deepcopy(self._delivered)

    def checkpoint(self):
        """Private trusted-driver data, never an actor observation."""
        value = dict(version=VERSION, config=copy.deepcopy(self.config), minute=self._minute,
                     lines=[dict(edge=[list(p) for p in edge], **copy.deepcopy(line))
                            for edge, line in sorted(self._lines.items())],
                     pending=copy.deepcopy(self._pending), delivered=copy.deepcopy(self._delivered))
        value['sha256'] = _digest(value)
        return value

    @classmethod
    def restore(cls, checkpoint):
        value = copy.deepcopy(checkpoint)
        if not isinstance(value, dict) or value.get('version') != VERSION or value.get('sha256') != _digest({k: v for k, v in value.items() if k != 'sha256'}):
            raise ValueError('Checkpoint identity or integrity differs')
        instance = cls(**value['config'])
        instance._minute = value['minute']
        instance._lines = {_edge(line['edge']): dict(strength=line['strength'], breached=line['breached'],
                           protected_cell=_point(line['protected_cell']) if line.get('protected_cell') is not None else None)
                           for line in value['lines']}
        instance._pending = value['pending']
        instance._delivered = value['delivered']
        return instance
