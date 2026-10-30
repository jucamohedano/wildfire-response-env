"""Execute in isolated processes: core's regular envs package shadows repo envs."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['spread_replay', 'unknown_sea', 'inert_sentinels',
                                 'post_step_censor', 'core_parity', 'seam_roundtrip', 'validation'])
def test_expanded_fire_case(case):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__, case], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def run_case(case):
    import copy
    import json
    import adapter_v1 as api
    from adapter_fire_v1 import ExpandedContainmentFireV1, ExpandedContainmentSeamV1

    n = 11
    doc = dict(version='wildfire-extended-inputs-v1',
        grid=dict(width=n, height=n, crs='synthetic-metre-grid', transform=[500, 0, 0, 0, -500, 5500]),
        domain_mask=[[True] * n for _ in range(n)], surface=[['land'] * n for _ in range(n)],
        fuel=[[4] * n for _ in range(n)], elevation=[[float(x + y) for x in range(n)] for y in range(n)],
        access=[['unknown'] * n for _ in range(n)],
        source_pins={k: c * 64 for k, c in zip(('surface', 'fuel', 'elevation', 'access'), 'abcd')})
    config = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, report_delay=2, wear_factor=.1)
    def load():
        return api.load_inputs(doc, api._hash(doc), expected_source_pins=doc['source_pins'], opt_in=True)
    def make(inputs=None):
        return ExpandedContainmentFireV1(inputs or load(), seed=13, containment_config=config,
                                         pressure_scale=1, wind=[1, 0])
    def raises(exc, f):
        try:
            f()
        except exc:
            return
        raise AssertionError('expected exception ' + str(exc))

    if case == 'spread_replay':
        a = make()
        assert a.change({'ignite': [[5, 5]]})
        # Continue until an actual kernel ignition has occurred.
        for _ in range(300):
            assert a.step()
            if len(a.burning()) > 1:
                break
        else:
            raise AssertionError('real kernel never spread in the synthetic fixture')
        checkpoint = json.loads(json.dumps(a.snapshot()))
        b = make()
        b.restore(checkpoint)
        assert api._encoded(checkpoint) == api._encoded(b.snapshot())
        for _ in range(40):
            assert a.step() == b.step()
            assert api._encoded(a.snapshot()) == api._encoded(b.snapshot())
            if a.censored:
                break
        assert a.censored == b.censored
    elif case == 'unknown_sea':
        doc['surface'][5][6], doc['fuel'][5][6], doc['elevation'][5][6] = 'sea', None, None
        doc['fuel'][1][1] = None
        a = make()
        assert a.change({'ignite': [[5, 5]]})
        for _ in range(200):
            if not a.step():
                break
            assert [6, 5] not in a.burning()
        snapshot = a.snapshot()
        positions = {(entry[0], entry[1]) for entry in snapshot['land']}
        assert (6, 5) not in positions and (1, 1) not in positions
        assert all(entry[2]['terrain'] is not None for entry in snapshot['land'])
        b = make()
        assert not b.change({'ignite': [[1, 2]]})
        assert b.reasons == ('unknown_fire_state',)
        assert b.snapshot()['tick'] == 0
        raises(api.CensoredError, b.outcomes)
        raises(api.CensoredError, lambda: b.step())
        c = make()
        assert not c.change({'ignite': [[6, 5]]})
        assert 'invalid_burning_cell' in c.reasons
        assert [6, 5] not in c.burning()
    elif case == 'inert_sentinels':
        doc['surface'][5][6], doc['fuel'][5][6], doc['elevation'][5][6] = 'sea', None, None
        doc['fuel'][1][1] = None
        a = make()
        class GuardCell(dict):
            def __getitem__(self, key):
                if key in ('fuel', 'terrain'):
                    raise AssertionError('physics read unsupported cell')
                return super().__getitem__(key)
        class GuardHeight(list):
            def __getitem__(self, key):
                if key == 6:
                    raise AssertionError('physics read nonexistent sea elevation')
                return super().__getitem__(key)
        a._fire.cells[1][1] = GuardCell(a._fire.cells[1][1])
        a._fire.heights[5] = GuardHeight(a._fire.heights[5])
        assert a.change({'ignite': [[5, 5]]})
        for _ in range(100):
            assert a.step()
    elif case == 'post_step_censor':
        # A fast east wind carries a source at x=9 toward the boundary x=10.
        a = ExpandedContainmentFireV1(load(), seed=13, containment_config=config,
                                      pressure_scale=1, wind=[3, 0])
        assert a.change({'ignite': [[9, 5]]})
        for _ in range(300):
            if not a.step():
                break
        else:
            raise AssertionError('expected fire to reach the boundary')
        assert 'grid_boundary' in a.reasons
        assert a.snapshot()['tick'] > 0
        raises(api.CensoredError, a.outcomes)
        raises(api.CensoredError, lambda: a.step())
        def fast(inputs=None):
            return ExpandedContainmentFireV1(inputs or load(), seed=13, containment_config=config,
                                              pressure_scale=1, wind=[3, 0])
        def driver():
            return ExpandedContainmentSeamV1(doc, api._hash(doc), expected_source_pins=doc['source_pins'],
                backend_factory=fast, expected_backend_identity=fast().identity, opt_in=True)
        host = driver()
        host.change({'ignite': [[9, 5]]})
        for _ in range(300):
            try:
                host.step()
            except api.CensoredError:
                break
        else:
            raise AssertionError('outer seam failed to propagate post-step censor')
        assert host.censored and 'grid_boundary' in host.reasons
        checkpoint = json.loads(json.dumps(host.checkpoint()))
        restored = driver()
        restored.restore(checkpoint)
        assert restored.reasons == host.reasons
        raises(api.CensoredError, restored.outcomes)
    elif case == 'core_parity':
        from envs.wildfire_response.incident_day.scenarios import DayFire, fixture_inputs
        from wildfire_containment_v1.fire import ContainmentDayFire
        from wildfire_containment_v1.kernel import ContainmentKernel
        inputs, _ = fixture_inputs()
        n = inputs['grid']['width']
        doc['grid'] = {key: inputs['grid'][key] for key in ('width', 'height', 'crs', 'transform')}
        doc['domain_mask'] = [[True] * n for _ in range(n)]
        doc['surface'] = [['land'] * n for _ in range(n)]
        doc['access'] = [['unknown'] * n for _ in range(n)]
        doc['fuel'] = copy.deepcopy(inputs['fuel_classes'])
        doc['elevation'] = copy.deepcopy(inputs['heights'])
        expanded = make()
        original = DayFire(inputs, 13)
        original.set_wind(x=1, y=0)
        original.add_fire(16, 16)
        original = ContainmentDayFire.from_day_fire(original, containment=ContainmentKernel(**config),
                                                    pressure_scale=1)
        assert expanded.change({'ignite': [[16, 16]]})
        for _ in range(500):
            original.step_with_containment()
            assert expanded.step()
            assert expanded._fire.cells == original.cells
            assert expanded._fire.rng.getstate() == original.rng.getstate()
            assert expanded._fire._containment.checkpoint() == original._containment.checkpoint()
            if len(expanded.burning()) > 1:
                break
        else:
            raise AssertionError('expected a spread event in the parity fixture')
    elif case == 'seam_roundtrip':
        def driver():
            identity = make().identity
            return ExpandedContainmentSeamV1(doc, api._hash(doc), expected_source_pins=doc['source_pins'],
                backend_factory=make, expected_backend_identity=identity, opt_in=True)
        a, b = driver(), driver()
        a.change({'ignite': [[5, 5]]})
        for _ in range(10):
            a.step()
        checkpoint = json.loads(json.dumps(a.checkpoint()))
        b.restore(checkpoint)
        assert api._encoded(a.checkpoint()) == api._encoded(b.checkpoint())
        for _ in range(5):
            a.step()
            b.step()
            assert api._encoded(a.checkpoint()) == api._encoded(b.checkpoint())
        doc['fuel'][1][1] = None
        c = driver()
        raises(api.CensoredError, lambda: c.change({'ignite': [[1, 2]]}))
        assert c.reasons == ('unknown_fire_state',)
        frozen = c.checkpoint()
        d = driver()
        d.restore(json.loads(json.dumps(frozen)))
        assert d.censored and d.reasons == c.reasons
        raises(api.CensoredError, d.outcomes)
    elif case == 'validation':
        doc['grid']['transform'][0], doc['grid']['transform'][4] = 100, -100
        raises(ValueError, make)
        doc['grid']['transform'][0], doc['grid']['transform'][4] = 500, -500
        a = make()
        before = a.snapshot()
        tampered = copy.deepcopy(before)
        tampered['tick'] = 1
        raises(ValueError, lambda: a.restore(tampered))
        raises(RuntimeError, a.snapshot)
        a.restore(before)
        assert api._encoded(a.snapshot()) == api._encoded(before)
        raises(ValueError, lambda: a.step({'assignments': []}))
        raises(RuntimeError, a.snapshot)
        a.restore(before)


if __name__ == '__main__':
    run_case(sys.argv[1])
