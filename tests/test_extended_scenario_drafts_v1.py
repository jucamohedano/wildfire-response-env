"""Draft remapping and wind direction, independent of the private map pack."""
import copy

import pytest

from eval.extended_scenario_drafts_v1 import _digest, build_drafts, wind_from_degrees

OLD = {'crs': 'EPSG:25830', 'transform': [500, 0, 297000, 0, -500, 4054500],
       'width': 37, 'height': 36}
NEW = {'crs': 'EPSG:25830', 'transform': [500, 0, 292000, 0, -500, 4059500],
       'width': 57, 'height': 46}


def source():
    cases = []
    for i in range(12):
        case = {'id': f'incident-eval-v1-{i:03}', 'seed': 8140000 + i,
                'initial_fire': [[14, 26], [15, 26]], 'sector_grid': [16, 28],
                'weather_schedule': [{'minute': 0, 'wind': [1, 0]}],
                'wind': [1, 0], 'incoming_reports': [{'location': [15, 26]}]}
        case['sha256'] = _digest(case)
        cases.append(case)
    catalog = {'version': 'wildfire-incident-eval-catalog-v1', 'cases': cases}
    catalog['sha256'] = _digest(catalog)
    return catalog


@pytest.mark.parametrize(('direction', 'expected'), [
    (0, [0, 1]), (90, [-1, 0]), (180, [0, -1]), (270, [1, 0]),
])
def test_wind_from_direction(direction, expected):
    assert wind_from_degrees(direction, 1) == pytest.approx(expected)


def test_drafts_preserve_source_and_remap_geography():
    original = source()
    before = copy.deepcopy(original)
    result = build_drafts(original, OLD, NEW)
    assert original == before
    assert result['status'] == 'unqualified-not-runnable'
    assert result['cases'][0]['initial_fire'] == [[24, 36], [25, 36]]
    assert result['cases'][0]['sector_grid'] == [26, 38]
    assert result['cases'][0]['incoming_reports'][0]['location'] == [25, 36]
    assert sum(case['wind'][1] < 0 for case in result['cases']) == 10
    assert result['cases'][11]['wind'][1] > 0
    assert len({case['sha256'] for case in result['cases']}) == 12
    assert result == build_drafts(original, OLD, NEW)


def test_invalid_or_outside_grids_fail_closed():
    with pytest.raises(ValueError, match='aligned'):
        build_drafts(source(), OLD, {**NEW, 'transform': [500, 0, 292001, 0, -500, 4059500]})
    with pytest.raises(ValueError, match='cover'):
        build_drafts(source(), OLD, {**NEW, 'width': 20})


def test_tampered_catalog_and_invalid_wind_rejected():
    catalog = source()
    catalog['cases'][0]['initial_fire'][0][0] = 100
    with pytest.raises(ValueError, match='unpinned'):
        build_drafts(catalog, OLD, NEW)
    with pytest.raises(ValueError, match='wind'):
        wind_from_degrees(180, float('nan'))
