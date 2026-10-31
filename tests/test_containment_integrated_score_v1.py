import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in ('envs/wildfire_response/core/src', 'envs/wildfire_containment_v1/src',
             'envs/wildfire_containment_score_v1/src'):
    sys.path.insert(0, str(ROOT / path))

from wildfire_core.incident_day import fixture_inputs
from wildfire_containment_score_v1 import REWARD_VERSION, mask_sha256, recompute, verify_outcome
from wildfire_containment_score_v1.integrated import (IntegratedContainmentEnv, configuration_sha256,
    scoring_implementation_sha256, verify_integrated_outcome)

# Fixed ahead of episodes: the western half of the synthetic 32x32 all-fuel grid.
MASK = tuple(y * 32 + x for y in range(32) for x in range(16))
CONFIG = dict(work_gain=2, max_strength=10, decay=0, wind_factor=1,
              wear_factor=.05, report_delay=2)


def make(mask=MASK, config=CONFIG, pressure_scale=1):
    inputs, network = fixture_inputs()
    return IntegratedContainmentEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
        expected_network_sha256=network['sha256'], containment_config=config,
        pressure_scale=pressure_scale, scoring_mask=mask)


def trusted_identity(env):
    # Constants pinned for the fixture before running the episode.
    return dict(interface_version='wildfire-containment-integrated-scoring-v2',
        task_id='incident-day-dev-000', seed=2844000,
        inputs_sha256='414f7c71d6c00c76587c54d6d18af6e9ff1df3dc9b214ab814bab5be6640292e',
        network_sha256='50c70935e0519047a3130aa03b04a389d7434afa92e4119ec719b80cbe943402',
        scenario_sha256='a8f1ca0d6c648d8fcdcb080b50421790f2c0dd0846a84fc939de715f4c2079db',
        implementation_sha256=scoring_implementation_sha256(env._integrated_source, configuration_sha256(CONFIG, 1)),
        scoring_mask_sha256=mask_sha256(MASK))


def gate(env, identity):
    return verify_integrated_outcome(env, expected_identity=identity, scoring_mask=MASK,
        expected_configuration_sha256=configuration_sha256(CONFIG, 1))


def test_full_day_with_hold_resume_and_independent_verification():
    env = make()
    identity = trusted_identity(env)
    observation, info = env.reset('incident-day-dev-000')
    assert info['interface_version'] == identity['interface_version']
    assert all(env.trajectory()[key] == value for key, value in identity.items())
    assert 'scoring_mask' not in json.dumps(observation)
    route = env.route_options('engine-1')['options'][1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])]})
    while not env.get_situation()['offers']:
        result = env.advance_time(1)
        assert result[1] == 0
    offer = env.get_situation()['offers'][0]
    proposal = env.propose_hold(offer['id'])[0]
    env.authorize_hold(offer['id'], proposal['id'])
    while env.minute < 600 and not (env.terminated or env.truncated):
        assert env.advance_time(60)[1] == 0
    saved = json.loads(json.dumps(env.checkpoint()))
    resumed = make()
    resumed.restore(saved)
    while not (env.terminated or env.truncated):
        result = env.advance_time(60)
        assert result == resumed.advance_time(60)
        if not env.terminated:
            assert result[1] == 0
    assert env.terminated and env.minute == 1440 and not env.censored
    assert resumed.checkpoint() == env.checkpoint()
    assert len(env._containment_audit) > 0
    before = env.checkpoint()
    report = env.verify_episode()
    assert report['passed'] and report['exact_replay'] and report['independent_reward_agreement']
    assert report['outcomes']['reward_version'] == REWARD_VERSION
    burned = set().union(*(set(record['new_footprint']) for record in env._audit))
    assert burned & set(MASK) and burned - set(MASK)
    expected = recompute(env._audit, scoring_mask=MASK, terminal=True, abandoned=False, censored=False)
    assert report['outcomes']['graded_score'] == pytest.approx(expected['graded_score'])
    assert env.reward == pytest.approx(expected['graded_score'])
    graded = gate(env, identity)
    assert graded['status'] == 'full_day' and graded['score'] == pytest.approx(env.reward)
    assert env.checkpoint() == before
    assert 'audit' not in json.dumps(env.public_transcript())
    assert gate(env, dict(identity, scenario_sha256='wrong'))['status'] == 'identity_mismatch'
    assert verify_outcome(env, expected_identity=identity, scoring_mask=MASK, infrastructure_timeout=True)['status'] == 'timeout'


def test_invalid_abandonment_checkpoint_and_tampering():
    env = make()
    identity = trusted_identity(env)
    env.reset('incident-day-dev-000')
    for _ in range(3):
        env.step({'invalid': True})
    assert env.abandoned and env.truncated
    assert env.verify_episode()['passed']
    result = gate(env, identity)
    assert result['status'] == 'invalid_abandonment' and result['score'] == 0
    checkpoint = env.checkpoint()
    other = make(MASK[:-1])
    with pytest.raises(ValueError):
        other.restore(checkpoint)
    broken = copy.deepcopy(checkpoint)
    broken['scoring_mask'].pop()
    with pytest.raises(ValueError):
        make().restore(broken)
    env._audit[-1]['new_footprint'].append(0)
    assert not env.verify_episode()['passed']
    assert gate(env, identity)['status'] == 'verification_failed'


def test_host_truncation_and_identity_pin_fail_closed():
    env = make()
    identity = trusted_identity(env)
    env.reset('incident-day-dev-000')
    assert gate(env, identity)['status'] == 'incomplete'
    env.advance_time(3)
    env.truncate()
    assert env.verify_episode()['passed']
    assert gate(env, identity)['status'] == 'truncated'
    assert verify_outcome(env, expected_identity=identity, scoring_mask=MASK[:-1])['status'] == 'identity_mismatch'
    resumed = make()
    resumed.restore(json.loads(json.dumps(env.checkpoint())))
    assert resumed.verify_episode()['passed']


def test_bad_masks_and_checkpoint_isolation():
    for mask in ([1, 1], [True], [-1]):
        with pytest.raises(ValueError):
            make(mask)
    env = make([999999])
    with pytest.raises(ValueError, match='non-burnable'):
        env.reset('incident-day-dev-000')
    with pytest.raises(RuntimeError):
        env.trajectory()
    env = make()
    env.reset('incident-day-dev-000')
    saved = env.checkpoint()
    saved['scoring_mask'].pop()
    assert len(env._mask) == len(MASK)
    with pytest.raises(RuntimeError):
        env.outcomes()


@pytest.mark.parametrize('simultaneous_abandonment', [False, True])
def test_synthetic_boundary_censor_and_precedence(monkeypatch, simultaneous_abandonment):
    # Inject boundary contact at a real containment fire-step boundary. This is
    # a deterministic fault probe, not evidence of physical boundary behavior.
    from wildfire_containment_v1.fire import ContainmentDayFire
    original = ContainmentDayFire.step_with_containment
    def contact(fire, *args, **kwargs):
        result = original(fire, *args, **kwargs)
        if fire.tick >= 3:
            fire.boundary_contact_at = fire.tick
        return result
    monkeypatch.setattr(ContainmentDayFire, 'step_with_containment', contact)
    env = make()
    identity = trusted_identity(env)
    env.reset('incident-day-dev-000')
    if simultaneous_abandonment:
        for _ in range(3):
            env.step({'invalid': True})
    else:
        while not env.truncated:
            env.advance_time(1)
    assert env.censored and env.truncated
    assert env.abandoned is simultaneous_abandonment
    before = env.checkpoint()
    assert env.verify_episode()['passed']
    result = gate(env, identity)
    assert result['status'] == 'censored' and result['verified'] and result['score'] is None
    assert env.checkpoint() == before


def test_independent_accounting_disagreement_fails_closed(monkeypatch):
    import wildfire_containment_score_v1.integrated as integrated
    import wildfire_containment_score_v1.verifier as verifier
    env = make()
    identity = trusted_identity(env)
    env.reset('incident-day-dev-000')
    env.truncate()
    original = integrated.recompute
    def disagree(*args, **kwargs):
        result = original(*args, **kwargs)
        result['new_exposed_population'] += 1
        return result
    monkeypatch.setattr(integrated, 'recompute', disagree)
    monkeypatch.setattr(verifier, 'recompute', disagree)
    assert not env.verify_episode()['passed']
    assert gate(env, identity)['status'] == 'verification_failed'


def test_existing_adapter_pins_and_behavior_unchanged():
    from envs.wildfire_response.incident_day.environment import implementation_digest
    from wildfire_containment_v1.incident_day import (
        CORE, VERSION, ContainmentIncidentDayEnv, _source_digest,
    )
    inputs, network = fixture_inputs()
    def old_episode():
        env = ContainmentIncidentDayEnv(inputs, network,
            expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'],
            containment_config=CONFIG, pressure_scale=1)
        observation = env.reset('incident-day-dev-000')
        result = env.advance_time(3)
        return observation, result, env.checkpoint()
    before_pins = (CORE, VERSION, implementation_digest(), _source_digest())
    before = old_episode()
    integrated = make()
    integrated.reset('incident-day-dev-000')
    integrated.advance_time(3)
    integrated.truncate()
    assert integrated.verify_episode()['passed']
    assert (CORE, VERSION, implementation_digest(), _source_digest()) == before_pins
    assert old_episode() == before


@pytest.fixture
def weather_setup():
    from envs.wildfire_response.incident_day.scenarios import build_scenario
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.incident_eval_bridge_v1 import CATALOG_VERSION
    from wildfire_core.incident_day import IncidentDayEnv
    inputs, network = fixture_inputs()
    base = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'])
    case = build_scenario(base.inputs, base.network, 'incident-day-egress-v1-000')
    case.update(id='weather-integrated-test-000', seed=8140000, warmup_minutes=60,
        private_evaluation_note='PRIVATE-WEATHER-SENTINEL', weather_schedule=[
            dict(minute=0, wind=[1., 0.], temperature_c=27, relative_humidity_pct=40, rain_mm_per_hour=0, received_at=0),
            dict(minute=20, wind=[0., 1.], temperature_c=35, relative_humidity_pct=20, rain_mm_per_hour=0, received_at=80),
            dict(minute=25, wind=[-1., 0.], temperature_c=18, relative_humidity_pct=85, rain_mm_per_hour=8, received_at=25)])
    case['events'] = sorted([e for e in case['events'] if e['kind'] != 'wind'] + [
        dict(minute=20, kind='wind', wind=[0., 1.]), dict(minute=25, kind='wind', wind=[-1., 0.])], key=lambda e: e['minute'])
    case['sha256'] = digest({k: v for k, v in case.items() if k != 'sha256'})
    catalog = dict(version=CATALOG_VERSION, inputs_sha256=inputs['sha256'],
                   network_sha256=network['sha256'], cases=[case])
    catalog['sha256'] = digest(catalog)
    return inputs, network, case, catalog


def make_weather(setup, mask=MASK, config=CONFIG, pressure_scale=1):
    from wildfire_containment_score_v1.integrated_weather import IntegratedWeatherContainmentEnv
    inputs, network, _, catalog = setup
    return IntegratedWeatherContainmentEnv(inputs, network,
        expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'],
        containment_config=config, pressure_scale=pressure_scale, scoring_mask=mask,
        weather_catalog=catalog, expected_catalog_sha256=catalog['sha256'])


def weather_pins(setup):
    from wildfire_containment_score_v1.integrated_weather import INTERFACE_VERSION, _weather_implementation
    from wildfire_containment_weather_v1.incident_day import _sources
    from wildfire_containment_v1.incident_day import CORE, _source_digest
    inputs, network, case, catalog = setup
    identity = dict(interface_version=INTERFACE_VERSION, task_id=case['id'], seed=case['seed'],
        inputs_sha256=inputs['sha256'], network_sha256=network['sha256'], scenario_sha256=case['sha256'],
        implementation_sha256=scoring_implementation_sha256(_weather_implementation(), configuration_sha256(CONFIG, 1)),
        scoring_mask_sha256=mask_sha256(MASK))
    separate = dict(expected_configuration_sha256=configuration_sha256(CONFIG, 1),
                    expected_catalog_sha256=catalog['sha256'], expected_weather_source_sha256=_sources(),
                    expected_containment_source_sha256=_source_digest(), expected_core_source_sha256=CORE)
    return identity, separate


def test_full_day_weather_hold_checkpoint_and_fixed_score(weather_setup):
    from wildfire_containment_score_v1.integrated_weather import verify_weather_outcome
    env = make_weather(weather_setup)
    identity, pins = weather_pins(weather_setup)
    observation, info = env.reset(weather_setup[2]['id'], seed=weather_setup[2]['seed'])
    assert info['interface_version'] == identity['interface_version']
    assert 'PRIVATE-WEATHER-SENTINEL' not in json.dumps(observation)
    route = env.route_options('engine-1')['options'][1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])]})
    while not env.get_situation()['offers']:
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    env.authorize_hold(offer['id'], env.propose_hold(offer['id'])[1]['id'])
    while env.minute < 20:
        env.advance_time(1)
    assert list(env.fire.wind) == [0., 1.] and env.get_situation()['wind'] == [1., 0.]
    while env.minute < 25:
        env.advance_time(1)
    assert list(env.fire.wind) == [-1., 0.] and env.get_situation()['wind'] == [-1., 0.]
    snapshot = json.loads(json.dumps(env.checkpoint()))
    resumed = make_weather(weather_setup)
    resumed.restore(snapshot)
    while not (env.terminated or env.truncated):
        first = env.advance_time(60)
        assert first == resumed.advance_time(60)
        if not env.terminated:
            assert first[1] == 0
    assert env.terminated and env.minute == 1440 and env._containment_audit
    assert env.checkpoint() == resumed.checkpoint()
    before = env.checkpoint()
    verification = env.verify_episode()
    assert all(verification[k] is True for k in ('passed', 'exact_replay', 'independent_reward_agreement'))
    expected = recompute(env._audit, scoring_mask=MASK, terminal=True, abandoned=False, censored=False)
    assert verification['outcomes']['reward_version'] == REWARD_VERSION
    assert verification['outcomes']['graded_score'] == pytest.approx(expected['graded_score'])
    grade = verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins)
    assert grade['status'] == 'full_day' and grade['verified'] and grade['score'] == pytest.approx(env.reward)
    assert env.checkpoint() == before
    assert 'PRIVATE-WEATHER-SENTINEL' not in json.dumps(env.public_transcript())
    assert 'weather_schedule' not in json.dumps(env.public_transcript())
    wrong = dict(pins, expected_catalog_sha256='0' * 64)
    assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **wrong)['status'] == 'identity_mismatch'
    assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins,
                                  infrastructure_timeout=True)['status'] == 'timeout'


def test_weather_restore_and_replay_fail_closed(weather_setup):
    from wildfire_containment_score_v1.integrated_weather import verify_weather_outcome
    env = make_weather(weather_setup)
    identity, pins = weather_pins(weather_setup)
    env.reset(weather_setup[2]['id'])
    env.advance_time(3)
    checkpoint = json.loads(json.dumps(env.checkpoint()))
    with pytest.raises(ValueError):
        make_weather(weather_setup, mask=MASK[:-1]).restore(checkpoint)
    bad = copy.deepcopy(checkpoint)
    bad['catalog_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        make_weather(weather_setup).restore(bad)
    env.truncate()
    assert env.verify_episode()['passed']
    assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins)['status'] == 'truncated'
    for pin in pins:
        wrong = dict(pins, **{pin: '0' * 64})
        assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **wrong)['status'] == 'identity_mismatch'
    assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK[:-1], **pins)['status'] == 'identity_mismatch'
    env._weather._state['moisture_proxy'] += .01
    assert not env.verify_episode()['passed']
    assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins)['status'] == 'verification_failed'


def test_weather_censor_precedes_invalid_abandonment(weather_setup, monkeypatch):
    from wildfire_containment_v1.fire import ContainmentDayFire
    from wildfire_containment_score_v1.integrated_weather import verify_weather_outcome
    original = ContainmentDayFire.step_with_containment
    def boundary(fire, *args, **kwargs):
        result = original(fire, *args, **kwargs)
        if fire.tick >= 3:
            fire.boundary_contact_at = fire.tick
        return result
    monkeypatch.setattr(ContainmentDayFire, 'step_with_containment', boundary)
    env = make_weather(weather_setup)
    identity, pins = weather_pins(weather_setup)
    env.reset(weather_setup[2]['id'])
    for _ in range(3):
        env.step({'invalid': True})
    assert env.censored and env.abandoned and env.truncated
    assert env.verify_episode()['passed']
    result = verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins)
    assert result['status'] == 'censored' and result['verified'] and result['score'] is None


def test_weather_scoring_source_change_during_action_fails_closed(weather_setup, monkeypatch):
    import wildfire_containment_score_v1.integrated_weather as integrated_weather
    from wildfire_containment_v1.fire import ContainmentDayFire
    env = make_weather(weather_setup)
    env.reset(weather_setup[2]['id'])
    saved = env.checkpoint()
    original = ContainmentDayFire.step_with_containment
    with monkeypatch.context() as patch:
        def change_after_step(fire, *args, **kwargs):
            result = original(fire, *args, **kwargs)
            patch.setattr(integrated_weather, '_weather_implementation', lambda: '0' * 64)
            return result
        patch.setattr(ContainmentDayFire, 'step_with_containment', change_after_step)
        with pytest.raises(RuntimeError, match='implementation changed'):
            env.advance_time(1)
        assert not env._transcript_valid
    env.restore(saved)
    assert env.checkpoint() == saved


def test_combined_host_preserves_weather_adapter_pins_and_checkpoint(weather_setup):
    from wildfire_containment_weather_v1.incident_day import ContainmentWeatherIncidentDayEnv, VERSION, _sources
    inputs, network, case, catalog = weather_setup
    def make_legacy():
        return ContainmentWeatherIncidentDayEnv(inputs, network,
            expected_inputs_sha256=inputs['sha256'], expected_network_sha256=network['sha256'],
            containment_config=CONFIG, pressure_scale=1, weather_catalog=catalog,
            expected_catalog_sha256=catalog['sha256'])
    legacy = make_legacy()
    legacy.reset(case['id'])
    saved = json.loads(json.dumps(legacy.checkpoint()))
    old_pin = _sources()
    combined = make_weather(weather_setup)
    combined.reset(case['id'])
    combined.advance_time(3)
    assert _sources() == old_pin and saved['version'] == VERSION
    restored = make_legacy()
    restored.restore(saved)
    assert restored.checkpoint() == saved
    assert legacy.advance_time(3) == restored.advance_time(3)


@pytest.mark.parametrize('weather', [False, True])
@pytest.mark.parametrize('changed', ['config', 'pressure'])
def test_changed_containment_physics_fails_trusted_pin(weather_setup, weather, changed):
    from wildfire_containment_score_v1.integrated_weather import verify_weather_outcome
    config = dict(CONFIG)
    pressure = 1
    if changed == 'config':
        config['work_gain'] += 1
    else:
        pressure = 2
    env = (make_weather(weather_setup, config=config, pressure_scale=pressure) if weather
           else make(config=config, pressure_scale=pressure))
    if weather:
        identity, pins = weather_pins(weather_setup)
        env.reset(weather_setup[2]['id'])
        env.truncate()
        assert verify_weather_outcome(env, expected_identity=identity, scoring_mask=MASK, **pins)['status'] == 'identity_mismatch'
        actual_identity = {key: env.trajectory()[key] for key in identity}
        assert verify_weather_outcome(env, expected_identity=actual_identity, scoring_mask=MASK, **pins)['status'] == 'identity_mismatch'
    else:
        identity = trusted_identity(env)
        env.reset('incident-day-dev-000')
        env.truncate()
        assert gate(env, identity)['status'] == 'identity_mismatch'
        actual_identity = {key: env.trajectory()[key] for key in identity}
        assert gate(env, actual_identity)['status'] == 'identity_mismatch'
    # Even the older generic gate cannot accept the original identity with
    # altered physics, because its implementation identity binds the config.
    assert verify_outcome(env, expected_identity=identity, scoring_mask=MASK)['status'] == 'identity_mismatch'


@pytest.mark.parametrize('weather', [False, True])
def test_host_outcomes_never_write_actor_transcript(weather_setup, weather):
    env = make_weather(weather_setup) if weather else make()
    env.reset(weather_setup[2]['id'] if weather else 'incident-day-dev-000')
    for _ in range(3):
        terminal = env.step({'invalid': True})
    assert terminal[3] is True and 'outcomes' not in terminal[4]
    before = env.public_transcript()
    checkpoint = env.checkpoint()
    outcome = env.outcomes()
    assert outcome['invalid_abandonment'] and outcome['reward_version'] == REWARD_VERSION
    assert env.outcomes() == outcome
    assert env.public_transcript() == before and env.checkpoint() == checkpoint
    assert all(entry['operation'] != 'outcomes' for entry in before['calls'])
    public = json.dumps(before)
    for field in ('additional_burned_hectares', 'inherited_exposed_population',
                  'new_exposed_population', 'unresolved_evacuations', 'graded_score'):
        assert field not in public
    assert env.verify_episode()['passed']


def test_configuration_pin_canonicalizes_numeric_settings_and_rejects_invalid():
    equivalent = {key: (value if key == 'report_delay' else float(value))
                  for key, value in reversed(list(CONFIG.items()))}
    assert configuration_sha256(CONFIG, 1) == configuration_sha256(equivalent, 1.0)
    assert configuration_sha256(CONFIG, 2) != configuration_sha256(CONFIG, 1)
    with pytest.raises(ValueError):
        configuration_sha256(CONFIG, float('nan'))
    with pytest.raises(ValueError):
        configuration_sha256(dict(CONFIG, wear_factor=0), 1)
