import copy
from dataclasses import FrozenInstanceError
import importlib.util
import json
from pathlib import Path

import pytest

from envs.wildfire_extended_inputs_v1 import Censor, VERSION, canonical_sha256, load_inputs


@pytest.fixture
def document():
    # A synthetic 7x7 square, with known land surrounding the ignition.
    n = 7
    return dict(version=VERSION,
                grid=dict(width=n, height=n, crs='EPSG:25830',
                          transform=[500, 0, 290000, 0, -500, 4064500]),
                domain_mask=[[True] * n for _ in range(n)],
                surface=[['land'] * n for _ in range(n)],
                fuel=[[4] * n for _ in range(n)],
                elevation=[[100.] * n for _ in range(n)],
                access=[['unknown'] * n for _ in range(n)],
                source_pins={name: char * 64 for name, char in
                             [('surface', 'a'), ('fuel', 'b'), ('elevation', 'c'), ('access', 'd')]})


def load(doc, **kwargs):
    return load_inputs(doc, kwargs.pop('pin', canonical_sha256(doc)),
                       expected_source_pins=kwargs.pop('pins', copy.deepcopy(doc['source_pins'])),
                       opt_in=kwargs.pop('opt_in', True), **kwargs)


def test_explicit_opt_in_and_independent_pins(document):
    with pytest.raises(ValueError, match='opt-in'):
        load(document, opt_in=False)
    with pytest.raises(ValueError, match='document pin'):
        load(document, pin='0' * 64)
    wrong = dict(document['source_pins'], fuel='0' * 64)
    with pytest.raises(ValueError, match='source pins'):
        load(document, pins=wrong)
    document['source_pins']['fuel'] = 'invalid'
    with pytest.raises(ValueError, match='source pins'):
        load(document)


def test_fixed_masks_are_deeply_immutable(document):
    inputs = load(document)
    document['domain_mask'][3][3] = False
    document['fuel'][3][3] = None
    assert inputs.domain[3][3] and inputs.fire_support[3][3]
    with pytest.raises(TypeError):
        inputs.fire_support[3][3] = False
    with pytest.raises(FrozenInstanceError):
        inputs.width = 8


def test_confirmed_sea_is_nonburnable_but_does_not_censor_neighbour(document):
    document['surface'][3][4] = 'sea'
    document['fuel'][3][4] = None
    document['elevation'][3][4] = None
    inputs = load(document)
    assert inputs.fire_support[3][4]
    assert Censor(inputs).check([(3, 3)])
    censor = Censor(inputs)
    assert not censor.check([(4, 3)])
    assert censor.reasons == ('invalid_burning_cell',)
    document['fuel'][3][4] = 4
    with pytest.raises(ValueError, match='confirmed sea'):
        load(document)


@pytest.mark.parametrize('layer,value', [('surface', 'mixed'), ('surface', 'unknown'),
                                          ('fuel', None), ('elevation', None),
                                          ('domain_mask', False)])
def test_unknowns_and_excluded_domain_censor_before_spread(document, layer, value):
    document[layer][4][4] = value
    censor = Censor(load(document))
    assert not censor.check([(3, 3)])  # includes diagonal spread
    assert censor.reasons == ('unknown_fire_state',)
    assert not censor.check([])  # censorship remains sticky after extinction


def test_boundary_censor_and_invalid_observation_fail_closed(document):
    censor = Censor(load(document))
    assert not censor.check([(0, 3)])
    assert 'grid_boundary' in censor.reasons
    censor = Censor(load(document))
    with pytest.raises(ValueError):
        censor.check([(7, 2)])
    assert censor.censored and 'invalid_fire_observation' in censor.reasons


def test_access_unknown_is_distinct_from_blocked_and_fuel(document):
    document['access'][3][3] = 'accessible'
    document['access'][3][4] = 'blocked'
    document['fuel'][3][3] = None
    inputs = load(document)
    assert inputs.access_status([(3, 3)]) == 'accessible'
    assert inputs.access_status([(3, 3), (3, 2)]) == 'unknown'
    assert inputs.access_status([(3, 2), (4, 3)]) == 'blocked'
    assert not Censor(inputs).check([(3, 2)])
    with pytest.raises(ValueError):
        inputs.access_status([])
    document['surface'][3][3] = 'mixed'
    with pytest.raises(ValueError, match='accessibility'):
        load(document)


@pytest.mark.parametrize('layer,value', [('fuel', 0), ('fuel', True), ('fuel', 11),
                                          ('elevation', True), ('access', 'passable'),
                                          ('surface', 'water'), ('domain_mask', 1)])
def test_invalid_cell_values_rejected(document, layer, value):
    document[layer][2][2] = value
    with pytest.raises(ValueError):
        load(document)


def test_shape_grid_and_version_fail_closed(document):
    for mutate in (lambda d: d['fuel'][0].pop(),
                   lambda d: d['grid']['transform'].__setitem__(1, 1),
                   lambda d: d['grid'].__setitem__('width', True),
                   lambda d: d.__setitem__('version', 'incident-day-inputs-v2'),
                   lambda d: d.__setitem__('extra', None)):
        changed = copy.deepcopy(document)
        mutate(changed)
        with pytest.raises(ValueError):
            load(changed)


def test_json_path_duplicate_keys_and_hash(document, tmp_path):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(document))
    assert load_inputs(path, canonical_sha256(document), expected_source_pins=document['source_pins'], opt_in=True).width == 7
    path.write_text('{"version":"x","version":"y"}')
    with pytest.raises(ValueError, match='duplicate'):
        load_inputs(path, '0' * 64, expected_source_pins=document['source_pins'], opt_in=True)
    path.write_text('{"version":NaN}')
    with pytest.raises(ValueError, match='number'):
        load_inputs(path, '0' * 64, expected_source_pins=document['source_pins'], opt_in=True)


def test_corrected_grid_all_unknown_fixture_cannot_be_ignited(document):
    # Synthetic source-shaped fixture, not the private source-derived map.
    width, height = 84, 59
    document['grid'].update(width=width, height=height)
    for name, value in [('domain_mask', True), ('surface', 'unknown'),
                        ('fuel', None), ('elevation', None), ('access', 'unknown')]:
        document[name] = [[value] * width for _ in range(height)]
    document['fuel'][25][25] = 4  # a centre sample does not establish land
    inputs = load(document)
    assert sum(sum(row) for row in inputs.fire_support) == 0
    censor = Censor(inputs)
    assert not censor.check([(25, 25)])
    assert set(censor.reasons) == {'invalid_burning_cell', 'unknown_fire_state'}
    assert inputs.access_status([(25, 25)]) == 'unknown'


def test_frozen_incident_day_loader_rejects_extended_contract(document):
    path = (Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src/'
            'envs/wildfire_response/incident_day/data.py')
    spec = importlib.util.spec_from_file_location('frozen_incident_data', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extended = copy.deepcopy(document)
    extended['sha256'] = module.digest(extended)
    with pytest.raises(ValueError, match='Invalid incident-day input contract'):
        module.load_inputs(extended, extended['sha256'], allow_unapproved=True)
