import pytest

from wildfire_core.incident_day import ALL_CASE_IDS, CALIBRATED_CASE_IDS, EGRESS_CASE_IDS, fixture_inputs
from wildfire_core.mobility_routing import RoadNetwork
from envs.wildfire_response.incident_day.calibrated_scenarios import EGRESS_POLICY
from envs.wildfire_response.incident_day.scenarios import build_scenario


SHIFT_V1_HASHES = (
    '4cb39b551968a8919f2414b6db346a38e2d2f1c6438c763dab9c7e989385e45f',
    '63dac7149665d0f2d7d62e5e31e13ff6608cefecafea6c8d05eb109a0dbddd6e',
    'dacbb66d6100dba8cd9929ab065cdab1b6db9a93b44a31750e02ae711c9aed10',
    '2a982836afce4d1b3ca017ee0b72b3cde3d97db6b061f18b76233ea5bfd8f925',
    'fa52609d11d5e29e188e415b3482fa2360730575e84652c128c164561460811c',
    '776a7c7eecbd767190bca67ee49fc184c73b8941fcfaa625e0d6874448c35910',
)


@pytest.fixture(scope='module')
def day_inputs():
    inputs, document = fixture_inputs()
    return inputs, RoadNetwork(document, document['sha256'])


@pytest.mark.parametrize('index', range(6))
def test_egress_profiles_are_additive_and_keep_old_identity(day_inputs, index):
    inputs, network = day_inputs
    old = build_scenario(inputs, network, CALIBRATED_CASE_IDS[index])
    new = build_scenario(inputs, network, EGRESS_CASE_IDS[index])
    assert old['sha256'] == SHIFT_V1_HASHES[index]
    assert 'emergency_egress_policy' not in old['profile']
    assert new['sha256'] != old['sha256']
    assert new['parent_scenario_sha256'] == old['sha256']
    assert new['profile']['emergency_egress_policy'] == EGRESS_POLICY
    for field in ('seed', 'events', 'initial_fire', 'resources', 'groups', 'destinations', 'handover_staging'):
        assert new[field] == old[field]
    assert new['id'] in ALL_CASE_IDS and old['id'] in ALL_CASE_IDS


def test_egress_seed_and_default_reject_arbitrary_variants(day_inputs):
    inputs, network = day_inputs
    with pytest.raises(ValueError, match='seeds are pinned'):
        build_scenario(inputs, network, EGRESS_CASE_IDS[0], 1)
