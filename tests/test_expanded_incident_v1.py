"""Synthetic-only host checks in isolated imports alongside the packaged core."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['movement_replay', 'unknown_routing', 'missing_mode_evidence', 'censor',
                                  'initial_surface_guards', 'post_step_censor', 'catalog', 'full_day'])
def test_expanded_incident(case):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__, case], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def run(case):
    import copy
    import json
    import adapter_v1 as api
    from wildfire_expanded_incident_v1 import ExpandedIncidentV1, CATALOG_VERSION
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs

    base, network = fixture_inputs()
    # Synthetic network supplies affirmative mode evidence on each edge.
    for edge in network['edges']:
        edge['allowed_modes'] = ['crew', 'engine']
    network['sha256'] = api._hash({k: v for k, v in network.items() if k != 'sha256'})
    n = base['grid']['width']
    doc = dict(version='wildfire-extended-inputs-v1',
        grid={k: copy.deepcopy(base['grid'][k]) for k in ('width', 'height', 'crs', 'transform')},
        domain_mask=[[True] * n for _ in range(n)], surface=[['land'] * n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']), elevation=copy.deepcopy(base['heights']),
        access=[['accessible'] * n for _ in range(n)],
        source_pins={k: c * 64 for k, c in zip(('surface', 'fuel', 'elevation', 'access'), 'abcd')})
    config = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, report_delay=2, wear_factor=.1)
    p = next(node['position'] for node in network['nodes'] if node['id'] == '2:2')
    scenario = dict(id='expanded-synthetic-day-001', seed=13, initial_fire=[[16, 16]], wind=[0, 0],
                    center='center-0', resources=[dict(id='crew-0', kind='crew', position=p,
                    work_remaining=240, water_litres=0)])
    def factory():
        catalog = dict(version=CATALOG_VERSION, role='synthetic_fixture_only', inputs_sha256=api._hash(doc),
                       network_sha256=network['sha256'], cases=[copy.deepcopy(scenario)])
        host = ExpandedIncidentV1(doc, api._hash(doc), expected_source_pins=doc['source_pins'],
            network=network, expected_network_sha256=network['sha256'], catalog=catalog,
            expected_catalog_sha256=api._hash(catalog), case_id=scenario['id'],
            containment_config=config, pressure_scale=1, opt_in=True)
        return host, catalog
    def raises(exc, f):
        try:
            f()
        except exc:
            return
        raise AssertionError('expected ' + str(exc))

    if case == 'movement_replay':
        host, _ = factory()
        start = host.situation()
        assert start['known_fire'] == [[16, 16]] and 'seed' not in str(start)
        route = host.route_options('crew-0', [6, 4])
        candidates = [r for r in route['options'] if r['journey']['duration_minutes'] > 0]
        assert candidates
        host.dispatch('crew-0', [6, 4], candidates[0]['id'])
        assert host.checkpoint()['operations']['resources']['crew-0']['state'] == 'mobilizing'
        assert host.situation()['resources'][0]['state'] == 'available'  # reports arrive later
        request = host.request_status()
        assert request.startswith('request-')
        checkpoint = json.loads(json.dumps(host.checkpoint()))
        resumed, _ = factory()
        resumed.restore(checkpoint)
        assert api._encoded(checkpoint) == api._encoded(resumed.checkpoint())
        for _ in range(30):
            assert host.advance() == resumed.advance()
            assert api._encoded(host.checkpoint()) == api._encoded(resumed.checkpoint())
        reports = host.reports(limit=200)
        assert any(r['kind'] == 'dispatch_result' and r['payload']['accepted'] for r in reports)
        assert any(r['kind'] == 'resource_status' for r in reports)
        assert any(r['kind'] == 'status_response' for r in reports)
        unit = host.checkpoint()['operations']['resources']['crew-0']
        assert unit['position'] != p
        assert unit['work_remaining'] == 240  # no fabricated suppression work
        assert not any('rng' in api._encoded(item) or 'containment' in api._encoded(item) for item in reports)
        raises(NotImplementedError, host.outcomes)
    elif case == 'unknown_routing':
        # Only the resource cell has affirmative access. All other coverage is unknown.
        doc['access'] = [['unknown'] * n for _ in range(n)]
        doc['access'][2][2] = 'accessible'
        host, _ = factory()
        route = host.route_options('crew-0', [6, 4])
        for option in route['options']:
            assert all(host._router._segment_cells(a, b).isdisjoint(host._excluded)
                for leg in option['journey']['legs'] for a, b in zip(leg['coordinates'], leg['coordinates'][1:]))
        assert not any(item['journey']['duration_minutes'] > 0 for item in route['options'])
        if not route['options']:
            assert route['status'] == 'unmapped'
        assert not host._safe_position(host._router._center((3, 2)), 'engine')
        assert not host._safe_position(host._router._center((3, 2)), 'crew')
    elif case == 'missing_mode_evidence':
        for edge in network['edges']:
            edge.pop('allowed_modes')
        network['sha256'] = api._hash({k: v for k, v in network.items() if k != 'sha256'})
        host, _ = factory()
        route = host.route_options('crew-0', [6, 4])
        assert all(not any(leg['mode'] == 'road' for leg in item['journey']['legs'])
                   for item in route['options'])
    elif case == 'censor':
        doc['fuel'][16][17] = None
        host, _ = factory()
        assert host.situation()['status'] == 'censored'
        assert set(host.situation()) == {'version', 'minute', 'status', 'model_limit'}
        raises(api.CensoredError, host.reports)
        raises(api.CensoredError, host.advance)
        saved = json.loads(json.dumps(host.checkpoint()))
        resumed, _ = factory()
        resumed.restore(saved)
        assert resumed.situation() == host.situation()
    elif case == 'initial_surface_guards':
        # Synthetic examples use no source-derived locations or case labels.
        original_ignition = copy.deepcopy(scenario['initial_fire'])
        for count in (1, 2):
            local = [(15, 15), (17, 15)]
            for x, y in local:
                doc['surface'][y][x] = 'land'
                doc['access'][y][x] = 'accessible'
            for x, y in local[:count]:
                doc['surface'][y][x] = 'unknown'
                doc['access'][y][x] = 'unknown'
            scenario['id'] = 'synthetic-initial-guard'
            host, catalog = factory()
            assert host.situation()['status'] == 'censored'
            assert host.situation()['minute'] == 0
            assert catalog['cases'][0]['initial_fire'] == original_ignition
            private = host.checkpoint()
            assert private['fire']['tick'] == 0 and private['fire']['reasons'] == ['unknown_fire_state']
            assert private['operations']['last_minute'] is None
            raises(api.CensoredError, host.advance)
    elif case == 'post_step_censor':
        scenario['wind'] = [-3, 0]
        doc['surface'][16][14] = 'unknown'
        doc['access'][16][14] = 'unknown'
        host, _ = factory()
        assert host.situation()['status'] == 'active'
        for _ in range(300):
            if host.advance()['status'] == 'censored':
                break
        else:
            raise AssertionError('expected spread to reach an unknown neighbour')
        assert host.situation()['minute'] > 0
        raises(api.CensoredError, host.reports)
        checkpoint = json.loads(json.dumps(host.checkpoint()))
        resumed, _ = factory()
        resumed.restore(checkpoint)
        assert resumed.situation() == host.situation()
        raises(api.CensoredError, resumed.advance)
    elif case == 'catalog':
        host, cat = factory()
        damaged = copy.deepcopy(cat)
        damaged['cases'][0]['seed'] += 1
        raises(ValueError, lambda: ExpandedIncidentV1(doc, api._hash(doc), expected_source_pins=doc['source_pins'],
            network=network, expected_network_sha256=network['sha256'], catalog=damaged,
            expected_catalog_sha256=api._hash(cat), case_id=scenario['id'],
            containment_config=config, pressure_scale=1, opt_in=True))
        snap = host.checkpoint()
        changed = copy.deepcopy(snap)
        changed['minute'] = 10
        raises(ValueError, lambda: host.restore(changed))
        raises(RuntimeError, host.situation)
        host.restore(snap)
        assert api._encoded(host.checkpoint()) == api._encoded(snap)
    elif case == 'full_day':
        host, _ = factory()
        while host.situation()['status'] == 'active' and host.situation()['minute'] < 720:
            host.advance(60)
        midpoint = json.loads(json.dumps(host.checkpoint()))
        resumed, _ = factory()
        resumed.restore(midpoint)
        while host.situation()['status'] == 'active':
            assert host.advance(60) == resumed.advance(60)
            assert api._encoded(host.checkpoint()) == api._encoded(resumed.checkpoint())
        assert host.situation()['status'] == 'complete', host.situation()
        assert host.situation()['minute'] == 1440
        checkpoint = json.loads(json.dumps(host.checkpoint()))
        final, _ = factory()
        final.restore(checkpoint)
        assert api._encoded(checkpoint) == api._encoded(final.checkpoint())
        assert final.situation() == host.situation()
        raises(NotImplementedError, host.outcomes)


if __name__ == '__main__':
    run(sys.argv[1])
