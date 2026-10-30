import copy

import pytest

from eval.extended_scenario_drafts_v1 import _digest
from eval.preflight_extended_drafts_v1 import screen


def fixture():
    cell = {'surface': 'land', 'fuel_status': 'source'}
    value = {'version': 'extended-map-resolution-evidence-v1',
             'grid': {'width': 5, 'height': 5},
             'cells': [[copy.deepcopy(cell) for _ in range(5)] for _ in range(5)],
             'terrain_candidate': [[100] * 5 for _ in range(5)]}
    value['sha256'] = _digest(value)
    cases = [{'id': f'case-{i}', 'initial_fire': [[2, 2]]} for i in range(12)]
    return value, cases


def rehash(value):
    value['sha256'] = _digest({k: v for k, v in value.items() if k != 'sha256'})
    return value['sha256']


def test_known_initial_neighborhood_is_not_episode_qualification():
    value, cases = fixture()
    result = screen(cases, value, expected_map_sha256=value['sha256'])
    assert result['status'] == 'initial_check_only_not_qualified'
    assert all(row['status'] == 'initial_check_passed' for row in result['cases'])


def test_unknown_and_unapproved_estimate_block_but_confirmed_sea_is_supported():
    value, cases = fixture()
    value['cells'][1][1]['surface'] = 'unknown'
    value['cells'][1][2]['fuel_status'] = 'estimated'
    value['cells'][1][3]['surface'] = 'sea'
    pin = rehash(value)
    assert screen(cases, value, expected_map_sha256=pin)['cases'][0]['reasons'] == ['unknown_surface', 'unsupported_fuel']
    assert screen(cases, value, expected_map_sha256=pin, allow_estimates=True)['cases'][0]['reasons'] == ['unknown_surface']


def test_burning_sea_and_edge_fail_closed():
    value, cases = fixture()
    value['cells'][2][2]['surface'] = 'sea'
    assert 'ignition_on_sea' in screen(cases, value, expected_map_sha256=rehash(value))['cases'][0]['reasons']
    cases[0]['initial_fire'] = [[0, 0]]
    assert 'domain_boundary' in screen(cases, value, expected_map_sha256=value['sha256'])['cases'][0]['reasons']


def test_tamper_and_missing_dimensions_rejected():
    value, cases = fixture()
    pin = value['sha256']
    value['cells'][0][0]['surface'] = 'sea'
    with pytest.raises(ValueError, match='trusted pin'):
        screen(cases, value, expected_map_sha256=pin)
    value['terrain_candidate'].pop()
    with pytest.raises(ValueError, match='dimensions'):
        screen(cases, value, expected_map_sha256=rehash(value))
