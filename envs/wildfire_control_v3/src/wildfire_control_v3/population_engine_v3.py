"""Incident-day engine with event-time, delivered population knowledge.

The physics and outcome accounting remain the v2 engine's; this module owns a
separately pinned observation and validation contract and replay implementation.
"""
import copy
import math

from wildfire_core.incident_day import IncidentDayEnv
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.outcomes import recompute
from envs.wildfire_response.regional.ops.generator import digest
from . import _capture_source, _source_sha256

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)
VERSION = 'wildfire-incident-day-population-v3'


def _identity():
    if not _LOADED_CODE_MATCH or _source_sha256(__file__) != _LOADED_SOURCE_SHA256:
        raise ValueError('Population engine loaded source differs')
    return digest(dict(version=VERSION, core=implementation_digest(), source=_LOADED_SOURCE_SHA256))


def population_engine_identity():
    """Return the pinned v3 implementation identity for trusted evaluation."""
    return _identity()


def _public_group(group):
    # Operation events add only the observed dynamic group facts; existing
    # resident and relocation reports retain their original public payloads.
    return {key: copy.deepcopy(group[key]) for key in
            ('id', 'position', 'status', 'destination_id') if key in group}


def _delivered_group(group):
    # The resident and relocation contract includes exposure, but not
    # operational order bookkeeping or arbitrary simulator internals.
    return {key: copy.deepcopy(group[key]) for key in
            ('id', 'count', 'home', 'position', 'status', 'destination_id', 'exposed') if key in group}


class PopulationEngineV3(IncidentDayEnv):
    def reset(self, scenario_id=None, seed=None):
        _identity()
        self._population_evidence = {}
        result = super().reset(scenario_id, seed)
        self._install_population_bus_filter()
        return result

    def _install_population_bus_filter(self):
        emit = self.bus.emit

        def filtered_emit(source, kind, payload, minute, **kwargs):
            payload = copy.deepcopy(payload)
            if isinstance(payload, dict) and isinstance(payload.get('group'), dict):
                payload['group'] = _delivered_group(payload['group'])
            return emit(source, kind, payload, minute, **kwargs)

        self.bus.emit = filtered_emit

    def _emit(self, source, kind, payload, urgent=False, actionable=True):
        payload = copy.deepcopy(payload)
        group_id = payload.get('group_id')
        if (kind in ('evacuation_arrived', 'movement_blocked', 'movement_resumed',
                     'unable_to_comply')
                and group_id in self.ops.groups):
            payload['group'] = _public_group(self.ops.groups[group_id])
        return super()._emit(source, kind, payload, urgent=urgent, actionable=actionable)

    def _receive(self, records):
        # Handover is a delivered snapshot at minute zero. Retain every field
        # already public in the v2 handover, including exposure.
        if not self._population_evidence and self.minute == 0:
            for identifier, group in self._known_groups.items():
                snapshot = _delivered_group(group)
                snapshot.update(observed_at=0, received_at=0)
                self._population_evidence[identifier] = [snapshot]
                self._reconcile_group(identifier)
        stripped = []
        for original in records:
            record = copy.deepcopy(original)
            payload = record.get('payload', {})
            group = payload.pop('group', None)
            stripped.append(record)
            observed, reported, received = (record.get(k) for k in ('observed_at', 'reported_at', 'received_at'))
            if not (all(type(t) is int and t >= 0 for t in (observed, reported, received))
                    and observed <= reported <= received <= self.minute):
                continue
            kind = record.get('kind')
            if kind in ('protection_accepted', 'order_delivered', 'order_acknowledged'):
                identifier = payload.get('group_id')
                order = payload.get('order')
                # Shelter notices convey no movement. A missing, conflicting or
                # unrecognized order cannot establish that movement is absent.
                matched = (isinstance(order, dict) and isinstance(order.get('id'), str)
                           and order.get('id') == payload.get('order_id')
                           and order.get('group_id') == identifier)
                if matched and order.get('action') == 'shelter':
                    continue
                group = {'id': identifier, 'position': None}
                basis = 'order_notice_position_unknown'
            else:
                if not isinstance(group, dict) or not isinstance(group.get('status'), str):
                    continue
                order = payload.get('order')
                matched_order = (isinstance(order, dict) and isinstance(order.get('id'), str)
                                 and order['id'] == payload.get('order_id')
                                 and order.get('group_id') == group.get('id')
                                 and payload.get('group_id') == group.get('id')
                                 and order.get('action') == 'evacuate')
                arrival = (kind == 'evacuation_arrived' and record.get('source') == 'operations'
                           and record.get('role') == 'responder' and order.get('state') == 'completed'
                           and order.get('completed_at') == observed) if matched_order else False
                stopped = (kind in ('movement_blocked', 'unable_to_comply')
                           and record.get('source') == 'operations' and record.get('role') == 'responder'
                           and order.get('state') == ('blocked' if kind == 'movement_blocked' else 'unable_to_comply')
                           and (kind != 'unable_to_comply' or order.get('completed_at') == observed)
                           and payload.get('position') is not None
                           and payload.get('position') == group.get('position')) if matched_order else False
                confirmed = arrival or stopped
                basis = 'position_confirmed' if confirmed else 'delivered_group_snapshot'
            identifier = group.get('id')
            if identifier not in self._population_evidence:
                continue
            snapshot = _delivered_group(group)
            # Only the public order embedded in this delivered record can
            # establish its issue time. Never inspect physical order state.
            order = payload.get('order')
            issued = order.get('issued_at') if isinstance(order, dict) else None
            if not (isinstance(order, dict) and isinstance(order.get('id'), str)
                    and order['id'] == payload.get('order_id') and order.get('group_id') == identifier
                    and order.get('action') == 'evacuate' and type(issued) is int
                    and 0 <= issued <= observed):
                issued = None
            successful_arrival = (basis == 'position_confirmed' and kind == 'evacuation_arrived'
                                  and record.get('source') == 'operations' and record.get('role') == 'responder')
            # Report timestamps describe the whole captured snapshot; ignore
            # any conflicting timestamps embedded in a payload.
            snapshot.update(observed_at=observed, received_at=received, _basis=basis,
                            _evidence_order_id=payload.get('order_id'), _order_issued_at=issued,
                            _successful_arrival=successful_arrival)
            evidence = self._population_evidence[identifier]
            if snapshot not in evidence:
                evidence.append(snapshot)
            self._reconcile_group(identifier)
        # The parent handles other public information, but must not overwrite
        # our reconciled population with its last-arrival-wins update.
        super()._receive(stripped)

    def _reconcile_group(self, identifier):
        evidence = self._population_evidence[identifier]
        result = {'id': identifier}
        provenance = {}
        fields = set().union(*(set(item) for item in evidence)) - {
            'id', 'observed_at', 'received_at', '_basis', '_evidence_order_id',
            '_order_issued_at', '_successful_arrival', 'field_provenance'}
        for field in fields:
            candidates = [item for item in evidence if field in item]
            latest = max(item['observed_at'] for item in candidates)
            top = [item for item in candidates if item['observed_at'] == latest]
            values = [item[field] for item in top]
            conflict = any(value != values[0] for value in values[1:])
            result[field] = None if conflict else copy.deepcopy(values[0])
            provenance[field] = dict(observed_at=latest,
                received_at=max(item['received_at'] for item in top),
                basis='conflicting_reports' if conflict else
                    ('order_notice_position_unknown' if any(item.get('_basis') == 'order_notice_position_unknown' for item in top)
                     else 'delivered_group_snapshot'))
            if field == 'position' and not conflict:
                order_ids = {item.get('_evidence_order_id') for item in top
                             if item.get('_basis') == 'position_confirmed'}
                if len(order_ids) == 1 and None not in order_ids:
                    provenance[field]['order_id'] = next(iter(order_ids))
        # A resident's stationary snapshot does not cancel a pending evacuation:
        # activation can occur later without a further notice. Only a later
        # matching arrival, blocked movement or inability report resolves it.
        # This is a last-observed position, not a claim of current location;
        # the physical engine revalidates the route during execution.
        hazards = [item for item in evidence if item.get('_basis') == 'order_notice_position_unknown']
        confirmations = [item for item in evidence if item.get('_basis') == 'position_confirmed'
                         and item.get('position') is not None]
        if hazards:
            # Each order remains uncertain until a matching stop or arrival,
            # or a later-issued replacement's successful arrival proves it was
            # superseded. A failed replacement clears only its own order.
            latest_by_order = {}
            for item in hazards:
                order_id = item.get('_evidence_order_id')
                latest_by_order[order_id] = max(latest_by_order.get(order_id, -1), item['observed_at'])
            order_records = [item for item in evidence if item.get('_basis') in
                             ('order_notice_position_unknown', 'position_confirmed')]

            def issued_at(order_id):
                records = [item for item in order_records if item.get('_evidence_order_id') == order_id]
                times = {item.get('_order_issued_at') for item in records}
                return next(iter(times)) if records and len(times) == 1 and None not in times else None

            successes = [item for item in confirmations if item.get('_successful_arrival')]

            def resolved(item):
                order_id = item.get('_evidence_order_id')
                if order_id is None:
                    return False
                if any(confirmed['observed_at'] > item['observed_at']
                       and confirmed.get('_evidence_order_id') == order_id for confirmed in confirmations):
                    return True
                older_issued = issued_at(order_id)
                if older_issued is None:
                    return False
                for success in successes:
                    successor_id = success.get('_evidence_order_id')
                    successor_issued = issued_at(successor_id)
                    if (successor_id != order_id and successor_issued is not None
                            and older_issued < successor_issued
                            and success['observed_at'] > item['observed_at']):
                        return True
                return False

            unresolved = [item for item in hazards
                          if item['observed_at'] == latest_by_order[item.get('_evidence_order_id')]
                          and not resolved(item)]
            if unresolved:
                latest_hazard = max(item['observed_at'] for item in unresolved)
                result['position'] = None
                provenance['position'] = dict(observed_at=latest_hazard,
                    received_at=max(item['received_at'] for item in unresolved if item['observed_at'] == latest_hazard),
                    basis='order_notice_position_unknown')
        snapshots = [item for item in evidence if item.get('_basis') != 'order_notice_position_unknown']
        latest = max(item['observed_at'] for item in snapshots)
        result['observed_at'] = latest
        result['received_at'] = max(item['received_at'] for item in snapshots if item['observed_at'] == latest)
        result['field_provenance'] = provenance
        self._known_groups[identifier] = result

    def get_situation(self):
        result = super().get_situation()
        result['interface_version'] = VERSION
        return result

    def _info(self, error, wake):
        result = super()._info(error, wake)
        result['interface_version'] = VERSION
        return result

    def _usable_position(self, group_id):
        group = self._known_groups.get(group_id, {})
        pos = group.get('position')
        return (group.get('status') not in (None, 'unknown', 'evacuating') and isinstance(pos, (list, tuple))
                and len(pos) == 2 and all(type(x) in (int, float) and math.isfinite(x) for x in pos))

    def evacuation_options(self, group_id, destination_id):
        if group_id not in self._known_groups or destination_id not in self._known_destinations:
            raise ValueError('Choose a reported group and destination')
        if not self._usable_position(group_id):
            result = dict(status='position_unknown', options=[],
                          explanation='The last delivered population position is unknown or moving.')
            super()._log_query('evacuation_options', dict(group_id=group_id, destination_id=destination_id), result)
            return result
        self._population_route_query_active = True
        try:
            result = super().evacuation_options(group_id, destination_id)
        finally:
            self._population_route_query_active = False
        origin = self._known_groups[group_id]
        position_source = origin.get('field_provenance', {}).get('position', {})
        result['population_origin'] = dict(position=copy.deepcopy(origin['position']),
            observed_at=position_source.get('observed_at'), received_at=position_source.get('received_at'),
            order_id=position_source.get('order_id'),
            note='Last reported position; the group may have moved since it was observed.')
        # The core route ID is stable for a route, so wrap it in the complete
        # delivered knowledge identity. Re-querying cannot revive an old token.
        for option in result['options']:
            old_id = option['id']
            approach = self._approaches.pop(old_id)
            identifier = digest(dict(route_id=old_id, population_knowledge=self._known_groups[group_id]))[:20]
            option['id'] = identifier
            approach['id'] = identifier
            approach['_population_observed_at'] = self._known_groups[group_id]['observed_at']
            approach['_population_knowledge'] = digest(self._known_groups[group_id])
            self._approaches[identifier] = approach
        super()._log_query('evacuation_options', dict(group_id=group_id, destination_id=destination_id), result)
        return result

    def _log_query(self, tool, arguments, result):
        if tool == 'evacuation_options' and getattr(self, '_population_route_query_active', False):
            return
        return super()._log_query(tool, arguments, result)

    def _validate(self, action):
        super()._validate(action)
        if not isinstance(action, dict):
            return
        for order in action.get('protection_orders', []):
            if order['action'] != 'evacuate':
                continue
            identifier = order['group_id']
            if not self._usable_position(identifier):
                raise ValueError('Population position is unknown or moving; obtain a delivered position before routing')
            approach = self._approaches[order['approach_id']]
            if (approach.get('journey', {}).get('origin') != self._known_groups[identifier]['position']
                    or approach.get('_population_observed_at') != self._known_groups[identifier].get('observed_at')
                    or approach.get('_population_knowledge') != digest(self._known_groups[identifier])):
                raise ValueError('Population position changed; request a new evacuation approach')

    def trajectory(self):
        result = super().trajectory()
        result.update(interface_version=VERSION, implementation_sha256=_identity())
        return result

    def checkpoint(self):
        value = dict(version=VERSION, implementation_sha256=_identity(),
                     core=super().checkpoint(), population_evidence=copy.deepcopy(self._population_evidence))
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        value = copy.deepcopy(checkpoint)
        if (not isinstance(value, dict) or set(value) != {'version', 'implementation_sha256', 'core', 'population_evidence', 'sha256'}
                or value['version'] != VERSION or value['implementation_sha256'] != _identity()
                or digest({key: item for key, item in value.items() if key != 'sha256'}) != value['sha256']):
            raise ValueError('Population checkpoint identity or integrity differs')
        self._population_evidence = value['population_evidence']
        result = super().restore(value['core'])
        self._install_population_bus_filter()
        return result

    def verify_episode(self):
        if not self.terminated and not self.truncated:
            raise RuntimeError('Finish the episode before verification')
        _identity()
        replay = PopulationEngineV3(self.inputs, self.network.document, expected_inputs_sha256=self.inputs['sha256'],
            expected_network_sha256=self.network.sha256,
            allow_unapproved_data=self.inputs['approval_status'] != 'approved_synthetic_fixture')
        replay.reset(self._scenario['id'], self._scenario['seed'])
        for transition in self._actions:
            for query in (q for q in self._query_log if q['before_decision'] == replay.decision):
                result = getattr(replay, query['tool'])(**query['arguments'])
                if digest(result) != query['result_sha256']:
                    raise ValueError('Public route query did not replay exactly')
            replay.step(transition['action'])
            if replay._actions[-1] != transition:
                raise ValueError('Incident-day transition did not replay exactly')
        if digest(replay._audit) != digest(self._audit) or replay.outcomes() != self.outcomes():
            raise ValueError('Independent audit differs from replay')
        independent = recompute(self._audit, terminal=self.terminated, abandoned=self.abandoned, censored=self.censored)
        for key, value in self.outcomes().items():
            if (not math.isclose(value, independent[key], rel_tol=1e-12, abs_tol=1e-12)
                    if type(value) is float else independent[key] != value):
                raise ValueError('Independent outcome does not agree')
        return dict(passed=True, outcomes=independent, exact_replay=True, independent_reward_agreement=True)
