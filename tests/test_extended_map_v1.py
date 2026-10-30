"""Tests for the corrected map, with all generated grids kept private."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
import extended_map_common as common
import extended_map_v2 as v2
import extended_map_terrain_v2 as terrain

PARENT = Path('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json')
ARCHIVE = Path('/home/juancm/hackspain/wildfire-response-env/.runtime/malaga-missing-data-v1/malaga-terrain-fuel-search/FuelMap_LAEA.zip')
BOUNDARY = Path('/tmp/wildfire-natura-2025-shp.zip')
NEW_TERRAIN = Path('/tmp/wildfire-bermeja-full-mdt25-halo.tif')
OLD_TERRAIN = Path('/home/juancm/hackspain/wildfire-response-env/.runtime/malaga-terrain-validation-v2/acquisition/mdt05-sampled25m-halo.asc')
GIS_PYTHON = Path('/home/juancm/miniconda3/envs/detectree/bin/python')
EVIDENCE = ROOT / 'data/extended_map_v1/evidence-v2.json'


@pytest.fixture
def parent(monkeypatch):
    value = {'version': 'fixture', 'heights': [[0.0] * 37 for _ in range(36)],
             'provenance': {'source_fuel_classes': [[0] * 37 for _ in range(36)],
                            'source_fuel_known_mask': [[0] * 37 for _ in range(36)]}}
    value['provenance']['source_fuel_classes'][0][0] = 4
    value['provenance']['source_fuel_known_mask'][0][0] = 1
    value['sha256'] = common.digest(value)
    monkeypatch.setattr(v2, 'PARENT_DOCUMENT_SHA256', value['sha256'])
    return value


@pytest.fixture
def samples():
    value = [[None] * v2.WIDTH for _ in range(v2.HEIGHT)]
    value[20][14] = 4
    value[20][15] = 7  # A parent unknown remains unknown.
    value[0][0] = 2
    return value


def test_extent_and_unknown_semantics(parent, samples):
    value = v2.compile_map(parent, samples, {'status': 'fixture'})
    assert value['grid']['bounds'] == [290000, 4035000, 332000, 4064500]
    assert value['grid']['parent_offset'] == {'row': 20, 'column': 14}
    assert value['elevation'][20][14] == 0.0
    assert value['source_fuel_class'][20][14] == 4
    assert value['source_fuel_class'][20][15] is None
    assert value['source_fuel_class'][0][0] == 2
    assert value['overlap']['parent_unknown_with_archive_value'] == [{'parent_row': 0, 'parent_column': 1, 'sample': 7}]
    assert all(cell == 'unknown' for row in value['surface'] for cell in row)
    assert value['status'] == 'incomplete_not_simulator_ready'
    v2.validate_map(value, parent, samples, {'status': 'fixture'})


@pytest.mark.parametrize('field,value,row,col', [
    ('surface', 'sea', 0, 0), ('surface', 'land', 20, 14),
    ('elevation', 0, 0, 0), ('source_fuel_class', 0, 0, 0),
    ('source_fuel_class', 7, 20, 15),
])
def test_validator_rejects_rehashed_claims(parent, samples, field, value, row, col):
    result = v2.compile_map(parent, samples, {'status': 'fixture'})
    result[field][row][col] = value
    result['sha256'] = common.digest({k: v for k, v in result.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='differs'):
        v2.validate_map(result, parent, samples, {'status': 'fixture'})


def test_parent_and_overlap_pins(parent, samples, tmp_path):
    bad = tmp_path / 'parent.json'
    bad.write_text('{}')
    with pytest.raises(ValueError, match='parent file hash'):
        common.load_parent(bad)
    samples[20][14] = 5
    with pytest.raises(ValueError, match='parent known fuel mismatch'):
        v2.compile_map(parent, samples, {})
    parent['heights'][0][0] = 99
    with pytest.raises(ValueError, match='parent document hash'):
        v2.compile_map(parent, samples, {})


def test_private_output_and_json_safety(tmp_path):
    p = tmp_path / 'data.json'
    common.write_private_json(p, {'x': 1})
    assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='output must be'):
        common.write_private_json(ROOT / 'data/extended_map_v1/not-allowed.json', {})
    p.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match='duplicate'):
        common.read_json(p)
    p.write_text('{"a":NaN}')
    with pytest.raises(ValueError, match='invalid number'):
        common.read_json(p)


def test_terrain_aggregation_and_halo():
    np = pytest.importorskip('numpy')
    values = np.full((1182, 1682), 100, dtype=np.int16)
    values[0, 0] = 0
    values[1, 1] = 0
    means, zeros, stats = terrain.screen_and_aggregate(values)
    assert means[0][0] is None and means[0][1] == 100
    assert zeros[0][0] == 1
    assert stats['halo_pixels'] == 5724 and stats['zero_halo_samples'] == 1
    assert stats['cells_with_zero'] == 1 and stats['pixels_per_cell'] == 400
    with pytest.raises(ValueError):
        terrain.screen_and_aggregate(values[:, :-1])
    values[10, 10] = -9999
    with pytest.raises(ValueError):
        terrain.screen_and_aggregate(values)


def test_aggregate_evidence():
    evidence = common.read_json(EVIDENCE)
    assert evidence['fuel_coverage']['fringe_fuel_values'] == 2766
    assert evidence['fuel_coverage']['fringe_fuel_unknown'] == 858
    assert evidence['fuel_overlap']['known_samples_agree'] == 1252
    assert evidence['terrain_source']['acquired_on'] == '2026-10-30'
    assert evidence['terrain_source']['request_uri'] == terrain.WCS_REQUEST_URI
    assert evidence['terrain_statistics']['zero_samples'] == 115380
    assert evidence['terrain_statistics']['candidate_means'] == 4639
    assert evidence['terrain_statistics']['cells_with_zero'] == 317


@pytest.mark.skipif(not all(p.is_file() for p in (PARENT, ARCHIVE, BOUNDARY, GIS_PYTHON)), reason='pinned GIS sources or runtime unavailable')
def test_real_map_reproduces_evidence_in_private_directory(tmp_path):
    output = tmp_path / 'map.json'
    args = [str(GIS_PYTHON), str(ROOT / 'eval/extended_map_v2.py'),
            '--parent', str(PARENT), '--fuel-archive', str(ARCHIVE),
            '--boundary-archive', str(BOUNDARY), '--map', str(output)]
    for action in ('compile', 'validate'):
        subprocess.run(args[:2] + [action] + args[2:], check=True, capture_output=True, text=True)
    result = common.read_json(output)
    evidence = common.read_json(EVIDENCE)
    assert result['coverage'] == evidence['fuel_coverage']
    assert result['reference'] == evidence['reference']
    assert result['sha256'] == evidence['private_map_sha256']
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(not NEW_TERRAIN.is_file() or not OLD_TERRAIN.is_file(), reason='pinned terrain sources unavailable')
def test_real_terrain_reproduces_evidence_in_private_directory(tmp_path):
    pytest.importorskip('rasterio')
    result = terrain.build(NEW_TERRAIN, OLD_TERRAIN)
    output = tmp_path / 'terrain.json'
    common.write_private_json(output, result)
    evidence = common.read_json(EVIDENCE)
    assert result['sha256'] == evidence['private_terrain_sha256']
    assert result['sample_statistics'] == evidence['terrain_statistics']
    assert result['parent_sample_comparison'] == evidence['terrain_parent_comparison']
    assert result['parent_sample_comparison']['overlap_pixels'] == 624780
