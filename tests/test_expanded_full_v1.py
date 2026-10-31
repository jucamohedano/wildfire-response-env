"""Synthetic-only weather and ground operations integration checks."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_weather_ground_movement_slice():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'), str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import json
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.scenarios import fixture_inputs
    from wildfire_expanded_incident_v1.host import CATALOG_VERSION
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
    from wildfire_expanded_full_v1 import WeatherMixedMovementV1, WeatherMixedHoldV1

    base, network = fixture_inputs()
    for edge in network['edges']:
        edge['allowed_modes'] = ['crew', 'engine']
    network['sha256'] = api._hash({k:v for k,v in network.items() if k != 'sha256'})
    n = base['grid']['width']
    doc = dict(version='wildfire-extended-inputs-v1',
        grid={k:copy.deepcopy(base['grid'][k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*n for _ in range(n)], surface=[['land']*n for _ in range(n)],
        fuel=copy.deepcopy(base['fuel_classes']), elevation=copy.deepcopy(base['heights']),
        access=[['accessible']*n for _ in range(n)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    doc['surface'][16][17] = 'mixed'; doc['access'][16][17] = 'unknown'
    pos = next(node['position'] for node in network['nodes'] if node['id'] == '2:2')
    case = dict(id='synthetic-weather-movement', seed=7, initial_fire=[[16,16]], wind=[0.,0.],
        centers=['center-0'], warmup_minutes=2,
        weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,
                               rain_mm_per_hour=0,received_at=0),
                          dict(minute=3,wind=[.2,.3],temperature_c=12,relative_humidity_pct=80,
                               rain_mm_per_hour=5,received_at=8)],
        events=[dict(minute=3,kind='wind',wind=[.2,.3])])
    case['sha256'] = api._hash(case)
    draft = dict(version='wildfire-extended-scenario-drafts-v1', status='unqualified-not-runnable',
                 source_catalog_sha256='e'*64, cases=[case])
    draft['sha256'] = api._hash(draft)
    config = dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def factory(document=doc, scenario=case, kind='crew', drafts=draft,
                host_class=WeatherMixedMovementV1, work=240, water=600):
        inputs = api.load_inputs(document, api._hash(document), expected_source_pins=document['source_pins'], opt_in=True)
        # Test-only stand-in for the separate v4 source reconstruction trust gate.
        ident = dict(version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64)
        factors = ((17, 16, .8, .5),) if document['surface'][16][17] == 'mixed' else ()
        mixed = MixedInputsV2(inputs, factors, tuple(sorted(ident.items())), _VERIFIED)
        catcase = dict(id=scenario['id'],seed=scenario['seed'],initial_fire=scenario['initial_fire'],
            wind=scenario['wind'],center='center-0',resources=[dict(id='crew-0',kind=kind,position=pos,
                                                                  work_remaining=work,water_litres=0 if kind == 'crew' else water)])
        catalog = dict(version=CATALOG_VERSION,role='synthetic_fixture_only',inputs_sha256=inputs.document_sha256,
                       network_sha256=network['sha256'],cases=[catcase])
        return host_class(mixed,drafts,expected_drafts_sha256=drafts['sha256'],case_id=scenario['id'],
            network=network,expected_network_sha256=network['sha256'],catalog=catalog,
            expected_catalog_sha256=api._hash(catalog),containment_config=config,pressure_scale=1,opt_in=True)
    a = factory()
    assert a.situation()['status'] == 'active' and a._weather._warmup_done == 2
    assert a._weather._weather_state['minute'] == 0 and a._fire.snapshot()['tick'] == 2
    assert a._fire._fire._mixed_openness[(17,16)] == .5
    route = a.route_options('crew-0',[6,4])['options'][0]
    a.dispatch('crew-0',[6,4],route['id'])
    assert a._ops.resources['crew-0']['state'] == 'mobilizing'
    a.advance(3)
    assert a._weather._minute == 4 and list(a._fire._fire.wind) == [.2,.3]
    assert not any(r['kind']=='weather_update' and r['observed_at']==3 for r in a.reports(limit=200))
    before = json.loads(json.dumps(a.checkpoint()))
    a.situation(); a.reports(limit=200); a.route_options('crew-0',[6,4])
    assert api._encoded(before) == api._encoded(a.checkpoint())
    b = factory(); b.restore(before)
    for _ in range(25):
        assert a.advance() == b.advance()
        assert api._encoded(a.checkpoint()) == api._encoded(b.checkpoint())
    unit = a._ops.resources['crew-0']
    assert unit['position'] != pos
    assert unit['state'] == 'needs_assistance'  # actual Ops reached work, unsupported suppression rejected
    assert unit['work_remaining'] == 240 and unit['water_litres'] == 0
    reports = a.reports(limit=200)
    assert any(r['kind']=='dispatch_result' and r['payload']['accepted'] for r in reports)
    assert any(r['kind']=='work_target_unavailable' for r in reports)
    assert any(r['kind']=='weather_update' and r['observed_at']==3 and r['received_at']==8 for r in reports)
    assert 'weather_schedule' not in json.dumps(a.situation()) + json.dumps(reports)
    assert 'moisture_proxy' not in json.dumps(a.situation()) + json.dumps(reports)
    bad = copy.deepcopy(before); bad['weather']['minute'] += 1
    bad['sha256'] = api._hash({k:v for k,v in bad.items() if k!='sha256'})
    try: b.restore(bad)
    except ValueError: pass
    else: raise AssertionError('tampered checkpoint accepted')
    # A real engine follows the same Ops lifecycle and does not consume water
    # when the unsupported work callback rejects the assignment.
    engine = factory(kind='engine')
    engine_route = engine.route_options('crew-0',[6,4])['options'][0]
    engine.dispatch('crew-0',[6,4],engine_route['id'])
    engine.advance(40)
    engine_unit = engine._ops.resources['crew-0']
    assert engine_unit['position'] != pos and engine_unit['state'] == 'needs_assistance'
    assert engine_unit['water_litres'] == 600 and engine_unit['work_remaining'] == 240

    # Exercise an actual physical post-step boundary censor. This controlled
    # test-only ignition and RNG cause spread; no censor is injected by a mock.
    import random
    class ZeroRandom(random.Random):
        def random(self): return 0.0
    boundary = factory()
    assert boundary._fire.change({'ignite': [[1,1]]})
    from eval import incident_weather_v1 as weather
    conditions = weather.conditions(boundary._weather._profile, 1)
    conditions.update(minute=0, received_at=0)
    profile = weather.WeatherProfile.from_records([conditions])
    prior = weather.initial_state(profile, moisture_proxy=boundary._weather._weather_state['moisture_proxy'])
    next_weather = weather.advance(profile, prior, 1)
    f = boundary._fire._fire
    f.wind = tuple(conditions['wind'])
    f.rules['spread_factor'] = weather.spread_factor(next_weather, base_factor=.5)
    f.cells[1][1].update(heat=1., age=f.spread_interval(0,-1)-1)
    f.rng = ZeroRandom(3)
    for _ in range(50):
        if boundary.advance()['status'] == 'censored': break
    assert boundary.situation()['status'] == 'censored'
    assert boundary._weather._minute == boundary._minute == boundary._ops.last_minute
    try: boundary.reports()
    except api.CensoredError: pass
    else: raise AssertionError('post-censor reports exposed')
    snapshot = json.loads(json.dumps(boundary.checkpoint()))
    restored = factory(); restored.restore(snapshot)
    assert api._encoded(snapshot) == api._encoded(restored.checkpoint())
    extra = copy.deepcopy(draft)
    extra_case = extra['cases'][0]
    extra_case['events'].append(dict(minute=5,kind='access_restriction'))
    extra_case['sha256'] = api._hash({k:v for k,v in extra_case.items() if k != 'sha256'})
    extra['sha256'] = api._hash({k:v for k,v in extra.items() if k != 'sha256'})
    try: factory(scenario=extra_case, drafts=extra)
    except ValueError: pass
    else: raise AssertionError('unsupported future event silently discarded')
    # True unknown access must never become an available route.
    no_access = copy.deepcopy(doc); no_access['access'] = [['unknown']*n for _ in range(n)]
    try: factory(no_access)
    except ValueError: pass
    else: raise AssertionError('unknown access admitted a ground resource')
    # Real ignition censor fires before the operations or weather clocks advance.
    unknown = copy.deepcopy(doc)
    unknown['surface'][16][17] = 'unknown'; unknown['access'][16][17] = 'unknown'
    censored = factory(unknown)
    assert censored.situation()['status'] == 'censored'
    assert censored._ops.last_minute is None and censored._weather._warmup_done == 0
    try: censored.reports()
    except api.CensoredError: pass
    else: raise AssertionError('censored report exposed')
    saved = json.loads(json.dumps(censored.checkpoint()))
    resumed = factory(unknown); resumed.restore(saved)
    assert api._encoded(saved) == api._encoded(resumed.checkpoint())

    # A real mixed-fire spread enters the setup hazard buffer. Exercise both
    # Ops lifecycles and both ground kinds, including a mid-setup restore.
    for host_class in (WeatherMixedMovementV1, WeatherMixedHoldV1):
        for kind in ('crew', 'engine'):
            setup = factory(host_class=host_class, kind=kind, work=3, water=900)
            option = setup.route_options('crew-0',[6,4])['options'][0]
            if host_class is WeatherMixedHoldV1:
                setup.dispatch_hold('crew-0',[6,4],option['id'],[[6,4],[7,4]],[7,4])
            else:
                setup.dispatch('crew-0',[6,4],option['id'])
            while setup._ops.resources['crew-0']['state'] != 'on_scene':
                setup.advance()
            unit = setup._ops.resources['crew-0']
            assert unit['due_at'] >= setup._minute + 5
            assert setup._ops.profile['revalidate_setup_safety'] is True
            saved = json.loads(json.dumps(setup.checkpoint()))
            resumed = factory(host_class=host_class, kind=kind, work=3, water=900)
            resumed.restore(saved)
            assert resumed._ops.profile['revalidate_setup_safety'] is True
            # Even an integrity-rehashed private snapshot cannot turn off this
            # runtime safety requirement.
            bad = copy.deepcopy(saved)
            movement = bad['base'] if host_class is WeatherMixedHoldV1 else bad
            movement['operations']['profile']['revalidate_setup_safety'] = False
            movement['sha256'] = api._hash({k:v for k,v in movement.items() if k!='sha256'})
            if host_class is WeatherMixedHoldV1:
                bad['sha256'] = api._hash({k:v for k,v in bad.items() if k!='sha256'})
            try: resumed.restore(bad)
            except ValueError: pass
            else: raise AssertionError('setup safety disabled by checkpoint')
            resumed.restore(saved)
            # Both restored drivers receive identical synthetic test setup.
            for host in (setup, resumed):
                assert host._fire.change({'ignite':[[8,5]]})
                assert host._safe_position(host._ops.resources['crew-0']['position'],kind)
                row = weather.conditions(host._weather._profile,host._minute+1)
                row.update(minute=0,received_at=0)
                profile = weather.WeatherProfile.from_records([row])
                prior = weather.initial_state(profile,moisture_proxy=host._weather._weather_state['moisture_proxy'])
                next_weather = weather.advance(profile,prior,1)
                f = host._fire._fire
                f.wind = tuple(row['wind'])
                f.rules['spread_factor'] = weather.spread_factor(next_weather,base_factor=.5)
                f.cells[5][8].update(heat=1.,age=f.spread_interval(-1,0)-1)
                f.rng = ZeroRandom(19)
            assert setup.advance() == resumed.advance()
            assert [7,5] in setup._fire.burning()  # real spread, not a mocked hazard callback
            assert setup._ops.resources['crew-0']['state'] == 'on_scene'
            assert not setup._safe_position(setup._ops.resources['crew-0']['position'],kind)
            assert setup._minute < setup._ops.resources['crew-0']['due_at']
            assert setup.advance() == resumed.advance()
            assert setup._ops.resources['crew-0']['state'] == 'needs_assistance'
            assert setup._ops.resources['crew-0']['work_remaining'] == 3
            assert setup._ops.resources['crew-0']['water_litres'] == (900 if kind == 'engine' else 0)
            assert api._encoded(setup.checkpoint()) == api._encoded(resumed.checkpoint())
            if host_class is WeatherMixedHoldV1:
                assert not setup._missions
                assert not setup._fire._fire._containment._lines

    # Fire already threatening a base at handover must not make a physical
    # resource available; the corrected status is still delivered by radio.
    initial_case = copy.deepcopy(case)
    initial_case['initial_fire'] = [[3,3]]
    initial_case['sha256'] = api._hash({k:v for k,v in initial_case.items() if k!='sha256'})
    initial_drafts = copy.deepcopy(draft)
    initial_drafts['cases'] = [initial_case]
    initial_drafts['sha256'] = api._hash({k:v for k,v in initial_drafts.items() if k!='sha256'})
    for host_class in (WeatherMixedMovementV1, WeatherMixedHoldV1):
        handover = factory(host_class=host_class, scenario=initial_case, drafts=initial_drafts)
        assert handover._ops.resources['crew-0']['state'] == 'needs_assistance'
        assert handover.situation()['resources'][0]['state'] == 'available'
        assert not any(r['kind']=='resource_status' for r in handover.reports(limit=200))
        handover.advance(6)
        assert handover._ops.resources['crew-0']['state'] == 'needs_assistance'
        assert handover.situation()['resources'][0]['state'] == 'needs_assistance'
        assert any(r['kind']=='resource_status' and r['payload']['resource']['state']=='needs_assistance'
                   for r in handover.reports(limit=200))

    # Real routes back to each resource's base can enter on-scene setup at
    # the exact base position. Fire then spreads into its safety buffer.
    for host_class in (WeatherMixedMovementV1, WeatherMixedHoldV1):
        for kind in ('crew', 'engine'):
            at_base = factory(host_class=host_class, kind=kind, work=3, water=900)
            options = at_base.route_options('crew-0',[2,2])['options']
            candidates = [o for o in options if o['journey']['duration_minutes'] > 0]
            assert candidates, options
            if host_class is WeatherMixedHoldV1:
                at_base.dispatch_hold('crew-0',[2,2],candidates[0]['id'],[[2,2],[3,2]],[3,2])
            else:
                at_base.dispatch('crew-0',[2,2],candidates[0]['id'])
            while at_base._ops.resources['crew-0']['state'] != 'on_scene':
                at_base.advance()
            assert at_base._ops.resources['crew-0']['position'] == pos
            assert at_base._ops.resources['crew-0']['base_position'] == pos
            assert at_base._fire.change({'ignite':[[4,3]]})
            assert at_base._safe_position(pos,kind)
            row = weather.conditions(at_base._weather._profile,at_base._minute+1)
            row.update(minute=0,received_at=0)
            selected = weather.WeatherProfile.from_records([row])
            prior = weather.initial_state(selected,moisture_proxy=at_base._weather._weather_state['moisture_proxy'])
            next_weather = weather.advance(selected,prior,1)
            f = at_base._fire._fire
            f.wind = tuple(row['wind'])
            f.rules['spread_factor'] = weather.spread_factor(next_weather,base_factor=.5)
            f.cells[3][4].update(heat=1.,age=f.spread_interval(-1,0)-1)
            f.rng = ZeroRandom(27)
            at_base.advance()
            assert [3,3] in at_base._fire.burning()
            assert not at_base._safe_position(pos,kind)
            assert at_base._ops.resources['crew-0']['state'] == 'needs_assistance'
            assert at_base._ops.resources['crew-0']['work_remaining'] == 3
            assert at_base._ops.resources['crew-0']['water_litres'] == (900 if kind == 'engine' else 0)
            if host_class is WeatherMixedHoldV1:
                assert not at_base._missions
            # True status has changed, but no same-minute observation can have
            # traversed the radio; public knowledge remains delivered-only.
            reports = at_base.reports(limit=200)
            assert not any(r['kind']=='resource_status' and
                r['payload']['resource']['state']=='needs_assistance' for r in reports)
            assert at_base.situation()['resources'][0]['state'] != 'needs_assistance'
            # Restore the normal RNG implementation before checkpoint replay.
            ordinary_rng = random.Random()
            ordinary_rng.setstate(at_base._fire._fire.rng.getstate())
            at_base._fire._fire.rng = ordinary_rng
            saved = json.loads(json.dumps(at_base.checkpoint()))
            restored = factory(host_class=host_class, kind=kind, work=3, water=900)
            restored.restore(saved)
            for _ in range(6):
                assert at_base.advance() == restored.advance()
                assert at_base._ops.resources['crew-0']['state'] == 'needs_assistance'
            assert api._encoded(at_base.checkpoint()) == api._encoded(restored.checkpoint())
            reports = at_base.reports(limit=200)
            assert any(r['kind']=='resource_status' and
                r['payload']['resource']['state']=='needs_assistance' for r in reports)
            assert at_base.situation()['resources'][0]['state'] == 'needs_assistance'

    # Exercise the exact core Operations._return-at-base branch: fire is
    # already threatening the base when setup revalidation runs.
    for host_class, kind in ((WeatherMixedMovementV1, 'crew'), (WeatherMixedHoldV1, 'engine')):
        at_base = factory(host_class=host_class, kind=kind, work=3, water=900)
        route = next(o for o in at_base.route_options('crew-0',[2,2])['options']
                     if o['journey']['duration_minutes'] > 0)
        if host_class is WeatherMixedHoldV1:
            at_base.dispatch_hold('crew-0',[2,2],route['id'],[[2,2],[3,2]],[3,2])
        else:
            at_base.dispatch('crew-0',[2,2],route['id'])
        while at_base._ops.resources['crew-0']['state'] != 'on_scene': at_base.advance()
        assert at_base._ops.resources['crew-0']['position'] == pos
        # A partially empty engine would otherwise start refilling at the unsafe base.
        if kind == 'engine': at_base._ops.resources['crew-0']['water_litres'] = 400
        assert at_base._fire.change({'ignite':[[3,3]]})
        assert not at_base._safe_position(pos,kind)
        at_base.advance()
        unit = at_base._ops.resources['crew-0']
        assert unit['state'] == 'needs_assistance'
        assert unit['work_remaining'] == 3 and unit['water_litres'] == (400 if kind == 'engine' else 0)
        assert not any(r['kind']=='resource_status' and r['payload']['resource']['state']=='needs_assistance'
                       for r in at_base.reports(limit=200))
        saved = json.loads(json.dumps(at_base.checkpoint()))
        restored = factory(host_class=host_class, kind=kind, work=3, water=900)
        restored.restore(saved)
        assert at_base.advance() == restored.advance()
        assert at_base._ops.resources['crew-0']['state'] == 'needs_assistance'
        assert api._encoded(at_base.checkpoint()) == api._encoded(restored.checkpoint())

    # Run a real dispatched hold through Ops and the mixed containment kernel.
    hold = factory(host_class=WeatherMixedHoldV1, kind='engine', work=2, water=600)
    option = hold.route_options('crew-0',[6,4])['options'][0]
    edge = [[6,4],[7,4]]
    hold.dispatch_hold('crew-0',[6,4],option['id'],edge,[7,4])
    while hold._ops.resources['crew-0']['state'] != 'working':
        hold.advance()
    state = hold._ops.resources['crew-0']
    assert state['work_remaining'] == 1 and state['water_litres'] == 100
    line = hold._fire._fire._containment._lines[((6,4),(7,4))]
    assert line['strength'] > 0 and not line['breached']
    saved = json.loads(json.dumps(hold.checkpoint()))
    restored = factory(host_class=WeatherMixedHoldV1, kind='engine', work=2, water=600)
    restored.restore(saved)
    assert hold.advance() == restored.advance()
    assert api._encoded(hold.checkpoint()) == api._encoded(restored.checkpoint())
    assert hold._ops.resources['crew-0']['water_litres'] == 0
    assert hold._ops.resources['crew-0']['work_remaining'] == 0
    assert hold._ops.resources['crew-0']['state'] == 'needs_assistance'
    assert not hold._missions
    assert 'strength' not in json.dumps(hold.situation()) + json.dumps(hold.reports(limit=200))

    # Fresh instances reproduce the same public calls and complete private
    # state, without relying on the midpoint checkpoint.
    replay_a = factory(host_class=WeatherMixedHoldV1, kind='engine', work=2, water=600)
    replay_b = factory(host_class=WeatherMixedHoldV1, kind='engine', work=2, water=600)
    route_a = replay_a.route_options('crew-0',[6,4])['options'][0]
    route_b = replay_b.route_options('crew-0',[6,4])['options'][0]
    assert route_a == route_b
    assert replay_a.dispatch_hold('crew-0',[6,4],route_a['id'],edge,[7,4]) == \
           replay_b.dispatch_hold('crew-0',[6,4],route_b['id'],edge,[7,4])
    for _ in range(18): assert replay_a.advance() == replay_b.advance()
    assert api._encoded(replay_a.checkpoint()) == api._encoded(replay_b.checkpoint())

    # Cost mismatch after a physical step leaves no committed Ops changes and
    # blocks all public calls until a prior joint checkpoint is restored.
    fault = factory(host_class=WeatherMixedHoldV1, kind='engine', work=3, water=900)
    route = fault.route_options('crew-0',[6,4])['options'][0]
    fault.dispatch_hold('crew-0',[6,4],route['id'],edge,[7,4])
    while fault._ops.resources['crew-0']['state'] != 'working': fault.advance()
    snapshot = json.loads(json.dumps(fault.checkpoint()))
    original = fault._fire.step
    def corrupt_costs(*args, **kwargs):
        result = original(*args, **kwargs)
        fault._fire.costs = []
        return result
    fault._fire.step = corrupt_costs
    try: fault.advance()
    except RuntimeError: pass
    else: raise AssertionError('mismatched resource accounting accepted')
    assert fault._ops.checkpoint() == snapshot['base']['operations']
    try: fault.situation()
    except RuntimeError: pass
    else: raise AssertionError('partially stepped host exposed')
    fault.restore(snapshot)
    assert api._encoded(snapshot) == api._encoded(fault.checkpoint())

    # An actual pre-step hazard at the working position prevents further work.
    unsafe = factory(host_class=WeatherMixedHoldV1, kind='engine', work=3, water=900)
    route = unsafe.route_options('crew-0',[6,4])['options'][0]
    unsafe.dispatch_hold('crew-0',[6,4],route['id'],edge,[7,4])
    while unsafe._ops.resources['crew-0']['state'] != 'working': unsafe.advance()
    prior_unit = copy.deepcopy(unsafe._ops.resources['crew-0'])
    assert unsafe._fire.change({'ignite':[[6,5]]})
    unsafe.advance()
    assert unsafe._ops.resources['crew-0']['work_remaining'] == prior_unit['work_remaining']
    assert unsafe._ops.resources['crew-0']['water_litres'] == prior_unit['water_litres']
    assert unsafe._ops.resources['crew-0']['state'] == 'needs_assistance'

    # A physical post-step boundary censor still commits the matching real Ops
    # charge privately and suppresses reports; the joint snapshot restores.
    censored_hold = factory(host_class=WeatherMixedHoldV1, kind='engine', work=3, water=900)
    route = censored_hold.route_options('crew-0',[6,4])['options'][0]
    censored_hold.dispatch_hold('crew-0',[6,4],route['id'],edge,[7,4])
    while censored_hold._ops.resources['crew-0']['state'] != 'working': censored_hold.advance()
    prev = copy.deepcopy(censored_hold._ops.resources['crew-0'])
    assert censored_hold._fire.change({'ignite':[[1,1]]})
    from eval import incident_weather_v1 as weather
    row = weather.conditions(censored_hold._weather._profile,censored_hold._minute+1)
    row.update(minute=0,received_at=0)
    profile = weather.WeatherProfile.from_records([row])
    prior = weather.initial_state(profile,moisture_proxy=censored_hold._weather._weather_state['moisture_proxy'])
    new_weather = weather.advance(profile,prior,1)
    f = censored_hold._fire._fire
    f.wind = tuple(row['wind']); f.rules['spread_factor'] = weather.spread_factor(new_weather,base_factor=.5)
    f.cells[1][1].update(heat=1.,age=f.spread_interval(0,-1)-1)
    f.rng = ZeroRandom(42)
    assert censored_hold.advance()['status'] == 'censored'
    assert censored_hold._ops.resources['crew-0']['work_remaining'] == prev['work_remaining']-1
    assert censored_hold._ops.resources['crew-0']['water_litres'] == max(0,prev['water_litres']-500)
    try: censored_hold.reports()
    except api.CensoredError: pass
    else: raise AssertionError('censored hold leaked reports')
    cp = json.loads(json.dumps(censored_hold.checkpoint()))
    after = factory(host_class=WeatherMixedHoldV1, kind='engine', work=3, water=900)
    after.restore(cp)
    assert api._encoded(cp) == api._encoded(after.checkpoint())

    # A crew expends finite duty without water; no new work after exhaustion.
    crew = factory(host_class=WeatherMixedHoldV1, work=1)
    option = crew.route_options('crew-0',[6,4])['options'][0]
    crew.dispatch_hold('crew-0',[6,4],option['id'],edge,[7,4])
    crew.advance(40)
    assert crew._ops.resources['crew-0']['work_remaining'] == 0
    assert crew._ops.resources['crew-0']['state'] == 'needs_assistance'
    assert crew._fire._fire._containment._lines[((6,4),(7,4))]['strength'] >= 0

    # An unsupported edge, unknown access, and non-adjacent target fail before a tick.
    safety = factory(host_class=WeatherMixedHoldV1)
    option = safety.route_options('crew-0',[6,4])['options'][0]
    start = safety.checkpoint()
    for bad_edge, protected in [([[6,4],[8,4]],[8,4]), ([[16,16],[17,16]],[17,16]),
                                ([[8,4],[9,4]],[9,4])]:
        try: safety.dispatch_hold('crew-0',[6,4],option['id'],bad_edge,protected)
        except ValueError: pass
        else: raise AssertionError('invalid hold accepted')
        assert api._encoded(start) == api._encoded(safety.checkpoint())

    # A physical attack is stopped by a real line, using identical backend RNG
    # and fire state. This is a controlled kernel-level experiment, not a claim
    # that a unit can safely stand next to a burning cell.
    from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1
    import random
    left = factory(host_class=WeatherMixedHoldV1)
    right = factory(host_class=WeatherMixedHoldV1)
    for h in (left, right):
        f = h._fire._fire
        assert h._fire.change({'ignite':[[9,9]]})
        f.wind = (0.,0.)
        f.cells[9][9].update(heat=1., age=f.spread_interval(1,0)-1)
        f.rng = ZeroRandom(99)
    command = dict(resource_id='synthetic',edge=[[9,9],[10,9]],protected_cell=[10,9],
                   kind='crew',ready=True,work_remaining=1,water_litres=0,water_cost=0)
    left._fire.stage([command]); right._fire.stage([])
    assert left._fire.step() and right._fire.step()
    assert left._fire._fire.cells[9][10]['heat'] == 0
    assert right._fire._fire.cells[9][10]['heat'] > 0
    assert left._fire.costs == [dict(resource_id='synthetic',work_minutes=1,water_litres=0.)]


if __name__ == '__main__':
    main()
