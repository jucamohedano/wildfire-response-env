"""Additive causal access and reception-centre exercises, not historical cases."""

import copy

from ..regional.ops.generator import digest
from .calibrated_scenarios import build_egress_scenario
from .scenarios import COVERAGE_VARIANT_IDS, EGRESS_CASE_IDS


def build_coverage_scenario(inputs, network, identifier, seed=None):
    if identifier not in COVERAGE_VARIANT_IDS:
        raise ValueError('Unknown scenario-coverage variant')
    index = 2 if identifier == COVERAGE_VARIANT_IDS[0] else 4
    scenario = copy.deepcopy(build_egress_scenario(inputs, network, EGRESS_CASE_IDS[index], seed))
    scenario.update(id=identifier, version='wildfire-incident-day-coverage-scenarios-v1',
                    parent_scenario_sha256=scenario['sha256'])
    scenario['profile'].update(version='wildfire-incident-day-coverage-profile-v1',
                               causal_coverage=True)
    if index == 4:
        destination = next(item for item in scenario['destinations'] if item['id'] == 'destination-1')
        group = next(item for item in scenario['groups'] if item['id'] == 'population-1')
        group.update(position=destination['position'][:], status='arrived', destination_id=destination['id'])
        event = next(item for item in scenario['events'] if item['kind'] == 'destination_closed')
        event['destination_id'] = destination['id']
        scenario['profile']['closure_affects_occupants'] = True
        scenario['population_handover'] = dict(group_id=group['id'], count=group['count'],
            destination_id=destination['id'], arrived_before_handover=True,
            role='Synthetic initial occupancy, not an expert demonstration or reconstructed historical journey')
        scenario['handover_notice'] += (f" EXERCISE. {group['count']} people are already at "
            f"{destination['name']}. Their earlier journey is a declared initialization assumption.")
    scenario['sha256'] = digest({key: value for key, value in scenario.items() if key != 'sha256'})
    return scenario
