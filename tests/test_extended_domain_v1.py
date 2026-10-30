import pytest

from eval.extended_domain_v1 import domain_from_bounds


def test_official_reference_buffer_and_parent_alignment():
    bounds = [295023.6081, 4035210.911, 326731.6042, 4059055.8712]
    grid = domain_from_bounds(bounds)
    assert grid['bounds'] == [290000, 4035000, 332000, 4064500]
    assert grid['width'] == 84 and grid['height'] == 59
    assert grid['bounds'][0] <= bounds[0] - 5000
    assert grid['bounds'][2] >= bounds[2] + 5000
    assert grid['bounds'][3] >= bounds[3] + 5000


@pytest.mark.parametrize('bounds', [[1, 2, 0, 3], [0, 0, float('nan'), 2], [0, 0, 0, 2]])
def test_invalid_source_bounds_rejected(bounds):
    with pytest.raises(ValueError):
        domain_from_bounds(bounds)
