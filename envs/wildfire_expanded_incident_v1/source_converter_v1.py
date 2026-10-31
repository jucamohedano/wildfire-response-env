"""Private source-grounded conversion; no estimated fuel or inferred access."""
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts

VERSION = 'wildfire-expanded-source-converter-v1'


def _checked_document(value, expected, version):
    if type(value) is not dict or value.get('version') != version or value.get('sha256') != expected:
        raise ValueError('source document identity differs')
    if api._hash({k: v for k, v in value.items() if k != 'sha256'}) != expected:
        raise ValueError('source document hash differs')
    return copy.deepcopy(value)


def _road(path, expected):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000_000:
        raise ValueError('invalid private road candidate')
    with path.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('road candidate byte pin differs')
    with gzip.open(path, 'rb') as f:
        raw = f.read(120_000_001)
    if len(raw) > 120_000_000:
        raise ValueError('road candidate exceeds decompression limit')
    value = json.loads(raw)
    if (value.get('version') != 'extended-access-directed-road-candidate-v2'
            or value.get('status') != 'source_candidate_not_operational_access'
            or value.get('crs') != 'EPSG:25830'):
        raise ValueError('unrecognized source road candidate')
    return dict(sha256=actual, version=value['version'], status=value['status'],
                bounds=value['bounds'], crs=value['crs'],
                nodes=len(value['nodes']), arcs=len(value['arcs']))


def convert(resolution, parent, catalog, *, road_path, expected_resolution_sha256,
            expected_parent_sha256, expected_catalog_sha256, expected_road_sha256,
            opt_in_source_grounded=False):
    """Return private source-only contract, separate evidence and remapped drafts.

    Estimated classes remain evidence only, never passed to the v1 fire engine.
    Road candidate is identity-checked but does not establish any access.
    """
    if opt_in_source_grounded is not True:
        raise ValueError('explicit private source-grounded opt-in required')
    res = _checked_document(resolution, expected_resolution_sha256, 'extended-map-resolution-evidence-v1')
    if res.get('status') != 'private_evidence_not_simulator_input':
        raise ValueError('expected strict private resolution evidence')
    parent = _checked_document(parent, expected_parent_sha256, 'incident-day-inputs-v2')
    catalog = _checked_document(catalog, expected_catalog_sha256, 'wildfire-incident-eval-catalog-v1')
    if catalog.get('inputs_sha256') != expected_parent_sha256 or catalog.get('network_sha256') != parent.get('network_sha256'):
        raise ValueError('catalog and parent input/network pins differ')
    road = _road(road_path, expected_road_sha256)
    grid = res['grid']
    if (grid.get('crs') != road['crs'] or grid.get('bounds') != road['bounds']
            or grid.get('cell_metres') != 500):
        raise ValueError('road and resolution grids differ')
    w, h = grid['width'], grid['height']
    if (type(w) is not int or type(h) is not int or not 1 <= w <= 1000 or not 1 <= h <= 1000
            or w*h > 200000):
        raise ValueError('invalid resolution dimensions')
    cells, heights = res['cells'], res['terrain_candidate']
    if any(type(m) is not list or len(m) != h or any(type(row) is not list or len(row) != w for row in m)
           for m in (cells, heights)):
        raise ValueError('resolution dimensions differ')
    surface, fuel, elevation, provenance = [], [], [], []
    for y in range(h):
        ss, fs, es, ps = [], [], [], []
        for x in range(w):
            cell = cells[y][x]
            s, status = cell['surface'], cell['fuel_status']
            source, estimate = cell['source_fuel_class'], cell['estimated_fuel']
            if s not in ('land', 'sea', 'unknown') or status not in ('source', 'estimated', 'unknown'):
                raise ValueError('unsupported resolution classification')
            if status == 'source':
                if type(source) is not int or source not in range(1, 11) or estimate is not None:
                    raise ValueError('invalid source class')
            elif status == 'estimated':
                if (source is not None or type(estimate) is not dict or type(estimate.get('class')) is not int
                        or estimate['class'] not in range(1, 11)):
                    raise ValueError('invalid estimate provenance')
            elif source is not None or estimate is not None:
                raise ValueError('unknown fuel cannot have a replacement')
            height = heights[y][x]
            if height is not None and (type(height) not in (int, float) or not math.isfinite(height)):
                raise ValueError('invalid terrain candidate')
            if s == 'sea' and (source is not None or estimate is not None):
                raise ValueError('sea cannot carry land fuel')
            ss.append(s)
            fs.append(source if status == 'source' else None)
            es.append(None if s == 'sea' else height)
            ps.append(dict(status=status, source=source, estimate=copy.deepcopy(estimate),
                           provenance=copy.deepcopy(cell['fuel_provenance'])))
        surface.append(ss); fuel.append(fs); elevation.append(es); provenance.append(ps)
    pins = dict(surface=expected_resolution_sha256, fuel=expected_resolution_sha256,
                elevation=expected_resolution_sha256, access=expected_road_sha256)
    document = dict(version=api._contract_module.VERSION,
        grid={k: copy.deepcopy(grid[k]) for k in ('width', 'height', 'crs', 'transform')},
        domain_mask=[[True]*w for _ in range(h)], surface=surface, fuel=fuel, elevation=elevation,
        access=[['unknown']*w for _ in range(h)], source_pins=pins)
    document_hash = api._hash(document)
    api.load_inputs(document, document_hash, expected_source_pins=pins, opt_in=True)
    drafts = build_drafts(catalog, parent['grid'], grid)
    # All of this is private evidence, not a public scenario or training record.
    evidence = dict(version=VERSION, resolution=expected_resolution_sha256,
        parent=expected_parent_sha256, catalog=expected_catalog_sha256, road=road,
        inputs_sha256=document_hash, drafts_sha256=drafts['sha256'], fuel_provenance=provenance,
        limitations=['estimates retained separately and unsupported', 'all access unknown',
                     'candidate road graph is not routed', 'terrain candidate is unapproved'])
    return document, evidence, drafts
