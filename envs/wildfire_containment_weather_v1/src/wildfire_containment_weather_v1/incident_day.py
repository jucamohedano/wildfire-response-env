"""Separately versioned weather/containment incident lifecycle.

Reset body follows the pinned incident-day core, with explicit scenario, fire
and event bus injection. No process-wide hooks are installed.
"""
import copy
import hashlib
import math
from pathlib import Path

from envs.wildfire_response.incident_day import environment as core
from envs.wildfire_response.incident_day.environment import IncidentDayEnv, implementation_digest
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter, recompute
from envs.wildfire_response.regional.ops.generator import digest
from eval import incident_eval_bridge_v1 as bridge, incident_weather_v1 as weather
from wildfire_containment_v1.incident_day import (ContainmentIncidentDayEnv, CORE,
                                                   _check_runtime, _source_digest)
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.transcript import (PUBLIC_CALLS, arguments, json_equal, json_result)
from .weather import ContainmentWeatherOwner, _validate_case, _payload, _deliver

VERSION = 'wildfire-containment-weather-incident-adapter-v1'
TRANSCRIPT = 'wildfire-containment-weather-public-transcript-v1'


def _sources():
    root = Path(__file__).parent
    paths = list(root.glob('*.py')) + [Path(weather.__file__), Path(bridge.__file__)]
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})


class WeatherEventBus(EventBus):
    def __init__(self, seed, centers, case):
        super().__init__(seed, centers)
        self._case = copy.deepcopy(case)
        self._profile = _validate_case(case)

    def emit(self, source, kind, payload, minute, **kwargs):
        if (source, kind) == ('weather', 'wind_update'):
            return None
        if (source, kind) == ('handover', 'handover'):
            payload = copy.deepcopy(payload)
            states = ', '.join(f"{u['id']}: {u['state']}" for u in
                               sorted(self._case['resources'], key=lambda u: u['id']))
            payload['text'] = ('EXERCISE. Large-fire handover. Reported perimeter is partial. '
                               f"Initial resource states: {states}. " + self._case.get('handover_notice', ''))
        elif (source, kind) == ('prior-shift', 'handover_followup'):
            payload = copy.deepcopy(payload)
            unit = payload['resource']
            payload['text'] = (f"EXERCISE. Handover resource snapshot: {unit['id']} "
                               f"was reported as {unit['state']}. Resource details are included. "
                               'Prehistory is synthetic initialization, not an operational record.')
        return super().emit(source, kind, payload, minute, **kwargs)

    def tick(self, minute, center_views):
        for row, record in zip(self._case['weather_schedule'], self._profile.records):
            if record.minute == minute:
                delay = 0 if minute == 0 else row['received_at'] - minute if 'received_at' in row else None
                self.emit('weather', 'weather_update', _payload(record), minute, channel='radio', delay=delay)
        for report in self._case.get('incoming_reports', []):
            if report['minute'] == minute:
                self.emit(report['source'], 'incoming_report', {'text': report['text']}, minute,
                          role=report['role'], channel=report['channel'],
                          location=copy.deepcopy(report['location']), delay=report['delay'])
        return _deliver(self, minute, center_views)

    @classmethod
    def from_snapshot(cls, snapshot, case):
        base = EventBus.restore(snapshot)
        obj = cls(base.seed, base.centers, case)
        obj.__dict__.update(base.__dict__)
        return obj


class ContainmentWeatherIncidentDayEnv(ContainmentIncidentDayEnv):
    """Trusted opt-in weather catalog with the existing actor-facing hold API."""
    def __init__(self, *args, weather_catalog, expected_catalog_sha256, **kwargs):
        super().__init__(*args, **kwargs)
        self._catalog = bridge._catalog_copy(weather_catalog)
        if (self._catalog['sha256'] != expected_catalog_sha256
                or self._catalog['inputs_sha256'] != self.inputs['sha256']
                or self._catalog['network_sha256'] != self.network.sha256):
            raise ValueError('Trusted weather catalog or input pins differ')
        for case in self._catalog['cases']:
            _validate_case(case)
        self._weather_source = _sources()
        self._weather = None
        self._selected_case = None

    def _check_public_runtime(self):
        _check_runtime()
        if self._weather_source != _sources() or self._source_pin != _source_digest():
            raise RuntimeError('Weather or containment source changed')

    def reset(self, scenario_id=None, seed=None):
        self._check_public_runtime()
        reset_args = arguments((), dict(scenario_id=scenario_id, seed=seed))
        cases = {case['id']: case for case in self._catalog['cases']}
        if scenario_id is None and len(cases) == 1:
            scenario_id = next(iter(cases))
        if scenario_id not in cases:
            raise ValueError('Choose a case from the trusted weather catalog')
        selected = cases[scenario_id]
        if seed is not None and (type(seed) is not int or seed != selected['seed']):
            raise ValueError('Scenario seed differs from catalog pin')
        self._transcript_enabled = False
        self._transcript, self._transcript_reset = [], None
        self._transcript_valid = True
        self._missions, self._proposal_cache, self._observed, self._requested = {}, {}, {}, {}
        self._pending_hold, self._tick_work, self._containment_audit = None, [], []
        try:
            self._selected_case = copy.deepcopy(selected)
            self._weather = ContainmentWeatherOwner(self.inputs, selected,
                containment_config=self._config, pressure_scale=self._scale)
            observation, info = self._reset_host()
            self._transcript_reset = dict(arguments=reset_args, result=json_result((observation, info)))
        except BaseException:
            self._transcript_valid = False
            self._transcript_enabled = True
            raise
        self._transcript_enabled = True
        return observation, info

    def _reset_host(self):
        self._scenario = copy.deepcopy(self._selected_case)
        self._configure_router()
        self.minute = self.decision = self.invalid_streak = 0
        self.terminated = self.truncated = self.censored = self.abandoned = False
        self._input_censored = False
        self.reward = 0.
        self.fire = self._weather._fire
        self.bus = WeatherEventBus(self._scenario['seed'], self._scenario['centers'], self._scenario)
        self._weather._bus = self.bus
        self.ops = Operations(self._scenario['resources'], self._scenario['destinations'],
                              self._scenario['groups'], self._scenario['profile'], return_planner=self._return_route)
        self._known_resources = {key: self._resource_view(unit, 0) for key, unit in self.ops.resources.items()}
        self._known_groups = copy.deepcopy(self.ops.groups)
        self._known_destinations = copy.deepcopy(self.ops.destinations)
        self._known_wind = self._scenario['wind'][:]
        self._known_wind_at = 0
        self._memory = {}
        self._known_revision = 0
        self._offers, self._issued_orders, self._commands, self._approaches = {}, {}, [], {}
        self._offer_serial = 0
        self._query_log = []
        self._signals = {}
        self._invalid_log = []
        self._work_log = []
        self._event_effects = []
        self._closed_centers, self._restrictions = {}, set()
        self._known_restrictions = set()
        self._air_unavailable_until = self._known_air_unavailable_until = 0
        self._pending_requests = set()
        self._dose = {key: 0 for key in self.ops.groups}
        self._events = copy.deepcopy(self._scenario['events'])
        self._footprint = self._burned()
        self._audit = []
        self._actions = []
        self._counter = OutcomeCounter(self._footprint,
            [row * self.fire.width + column for row, cells in enumerate(self.fire.cells)
             for column, cell in enumerate(cells) if cell['initial_fuel'] > 0],
            self.ops.groups, self.inputs['grid']['cell_km'] ** 2 * 100)
        self._prehistory = dict(minutes=self._scenario['warmup_minutes'],
            checkpoint_sha256=digest(self.fire.checkpoint()),
            footprint_hectares=len(self._footprint) * self.inputs['grid']['cell_km'] ** 2 * 100,
            role='Synthetic initialization, not an expert demonstration')
        if self._prehistory['footprint_hectares'] <= 500:
            raise ValueError('Incident-day handover must already represent a large fire')
        initial_seen = sorted(self._burning())[:max(1, len(self._burning()) // 2)]
        self._capture_cells(initial_seen, 0)
        self.bus.emit('handover', 'handover', dict(text='EXERCISE. Large-fire operational handover. '
            'Reported perimeter is partial; resource stocks and assignments are carried forward. '
            + self._scenario.get('handover_notice', ''),
            cells=self._cells(initial_seen)), 0, role='center', delay=0)
        self.bus.emit('prior-shift', 'handover_followup', dict(text='EXERCISE. Confirmation of the carried '
            'ground assignment and remaining stock. Prehistory is initialization, not an expert policy.',
            resource=self._resource_view(self.ops.resources['engine-0'], 0)), 0, role='center', delay=15)
        self._receive(self.bus.tick(0, self._center_views()))
        self._record(initial=True)
        self._initial_observation = self.get_situation()
        if self.artifact_dir:
            self._start_artifacts()
        return self.get_situation(), self._info(None, 'handover')

    def _tick(self):
        self._check_public_runtime()
        fire = self.fire
        if 'step_with_containment' in fire.__dict__:
            raise RuntimeError('Unsupported containment-step override')
        original = fire.step_with_containment

        def weather_step(*, assignments=(), suppressing=()):
            minute = self.minute
            if self._weather._state['minute'] + 1 != minute or fire.tick + 1 != minute:
                raise RuntimeError('Weather and fire clocks differ')
            row = weather.conditions(self._weather._profile, minute)
            if list(fire.wind) != row['wind']:
                raise RuntimeError('True wind and weather schedule disagree')
            row.update(minute=0, received_at=0)
            selected = weather.WeatherProfile.from_records([row])
            prior = weather.initial_state(selected, moisture_proxy=self._weather._state['moisture_proxy'])
            updated = weather.advance(selected, prior, 1)
            next_state = weather.initial_state(self._weather._profile, moisture_proxy=updated['moisture_proxy'])
            next_state['minute'] = minute
            fire.rules['spread_factor'] = weather.spread_factor(next_state, base_factor=.5)
            result = original(assignments=assignments, suppressing=suppressing)
            self._weather._state = next_state
            return result

        fire.step_with_containment = weather_step
        try:
            return super()._tick()
        finally:
            del fire.step_with_containment

    def checkpoint(self):
        self._check_public_runtime()
        base = super().checkpoint()
        if (self._weather is None or self.fire.tick != self.minute
                or self._weather._state['minute'] != self.minute or self.bus._last_tick != self.minute):
            raise RuntimeError('Joint checkpoint requires matching clocks')
        value = dict(version=VERSION, source=self._weather_source,
                     catalog_sha256=self._catalog['sha256'], base=base,
                     weather=weather.checkpoint(self._weather._profile, self._weather._state))
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        self._check_public_runtime()
        value = copy.deepcopy(checkpoint)
        if (type(value) is not dict or set(value) != {'version', 'source', 'catalog_sha256', 'base', 'weather', 'sha256'}
                or value['version'] != VERSION or value['source'] != self._weather_source
                or value['catalog_sha256'] != self._catalog['sha256']
                or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
            raise ValueError('Weather checkpoint identity or integrity differs')
        case = value['base']['base']['state']['_scenario']
        match = next((item for item in self._catalog['cases'] if item['id'] == case.get('id')), None)
        if match is None or not json_equal(case, match):
            raise ValueError('Checkpoint scenario differs from trusted catalog')
        fresh = ContainmentWeatherOwner(self.inputs, match, containment_config=self._config,
                                         pressure_scale=self._scale)
        state = weather.restore(fresh._profile, value['weather'])
        base_state = value['base']['base']['state']
        if (state['minute'] != base_state['minute']
                or state['minute'] != base_state['communications']['last_tick']
                or base_state['communications']['seed'] != match['seed']):
            raise ValueError('Weather, host and communication clocks differ')
        try:
            super().restore(value['base'])
            if self.fire.tick != self.minute:
                raise ValueError('Weather and fire clocks differ')
            self.bus = WeatherEventBus.from_snapshot(self.bus.checkpoint(), match)
            fresh._fire = self.fire
            fresh._bus = self.bus
            fresh._state = state
            self._weather, self._selected_case = fresh, copy.deepcopy(match)
            return IncidentDayEnv.get_situation(self)
        except BaseException:
            self._transcript_valid = False
            self._transcript_enabled = True
            raise

    def trajectory(self):
        self._check_public_runtime()
        if self._scenario is None or not self._transcript_valid or self._transcript_reset is None:
            raise RuntimeError('A valid reset transcript is required')
        return copy.deepcopy(dict(version=TRANSCRIPT, adapter_version=VERSION,
                                  reset=self._transcript_reset, calls=self._transcript))

    def private_replay_pins(self):
        return dict(expected_transcript_sha256=digest(self.trajectory()),
                    expected_scenario_sha256=self._scenario['sha256'], expected_seed=self._scenario['seed'],
                    expected_core_sha256=CORE, expected_adapter_sha256=self._source_pin,
                    expected_weather_source_sha256=self._weather_source,
                    expected_catalog_sha256=self._catalog['sha256'],
                    expected_inputs_sha256=self.inputs['sha256'], expected_network_sha256=self.network.sha256,
                    expected_containment_config=copy.deepcopy(self._config), expected_pressure_scale=self._scale)

    def replay_transcript(self, transcript, *, expected_transcript_sha256, expected_scenario_sha256,
                          expected_seed, expected_core_sha256, expected_adapter_sha256,
                          expected_weather_source_sha256, expected_catalog_sha256,
                          expected_inputs_sha256, expected_network_sha256,
                          expected_containment_config, expected_pressure_scale):
        self._check_public_runtime()
        if type(self) is not ContainmentWeatherIncidentDayEnv or self._scenario is not None:
            raise ValueError('Replay requires a fresh, unmodified weather adapter')
        if (expected_core_sha256 != CORE or expected_adapter_sha256 != self._source_pin
                or expected_weather_source_sha256 != self._weather_source
                or expected_catalog_sha256 != self._catalog['sha256']
                or expected_inputs_sha256 != self.inputs['sha256']
                or expected_network_sha256 != self.network.sha256
                or not json_equal(expected_containment_config, self._config)
                or not json_equal(expected_pressure_scale, self._scale)
                or not isinstance(transcript, dict) or digest(transcript) != expected_transcript_sha256
                or set(transcript) != {'version', 'adapter_version', 'reset', 'calls'}
                or transcript['version'] != TRANSCRIPT or transcript['adapter_version'] != VERSION
                or not isinstance(transcript['calls'], list)):
            raise ValueError('Transcript or trusted implementation pins differ')
        reset = transcript['reset']
        if not isinstance(reset, dict) or set(reset) != {'arguments', 'result'}:
            raise ValueError('Invalid public reset entry')
        try:
            self.reset(*reset['arguments']['args'], **reset['arguments']['kwargs'])
        except Exception as error:
            self._transcript_valid = False
            raise ValueError('Transcript reset failed') from error
        if (self._scenario['sha256'] != expected_scenario_sha256
                or type(expected_seed) is not type(self._scenario['seed'])
                or self._scenario['seed'] != expected_seed or not json_equal(self._transcript_reset, reset)):
            self._transcript_valid = False
            raise ValueError('Scenario pin or public reset differs')
        for entry in transcript['calls']:
            if (not isinstance(entry, dict) or entry.get('operation') not in PUBLIC_CALLS
                    or not isinstance(entry.get('arguments'), dict)
                    or set(entry['arguments']) != {'args', 'kwargs'}
                    or not isinstance(entry['arguments']['args'], list)
                    or not isinstance(entry['arguments']['kwargs'], dict)):
                self._transcript_valid = False
                raise ValueError('Unsupported public transcript entry')
            count = len(self._transcript)
            try:
                getattr(self, entry['operation'])(*entry['arguments']['args'], **entry['arguments']['kwargs'])
            except Exception:
                pass
            if len(self._transcript) != count + 1 or not json_equal(self._transcript[-1], entry):
                self._transcript_valid = False
                raise ValueError('Public call or result did not replay exactly')
        if not json_equal(self.trajectory(), transcript):
            self._transcript_valid = False
            raise ValueError('Public transcript differs after replay')
        return IncidentDayEnv.get_situation(self)

    def verify_episode(self):
        self._check_public_runtime()
        if type(self) is not ContainmentWeatherIncidentDayEnv:
            raise RuntimeError('Verification requires the exact weather adapter class')
        if not self.terminated and not self.truncated:
            raise RuntimeError('Finish the episode before verification')
        replay = type(self)(self.inputs, self.network.document,
            expected_inputs_sha256=self.inputs['sha256'], expected_network_sha256=self.network.sha256,
            allow_unapproved_data=self.inputs['approval_status'] != 'approved_synthetic_fixture',
            containment_config=self._config, pressure_scale=self._scale,
            weather_catalog=self._catalog, expected_catalog_sha256=self._catalog['sha256'])
        replay.replay_transcript(self.trajectory(), **self.private_replay_pins())
        if digest(replay.checkpoint()) != digest(self.checkpoint()):
            raise ValueError('Private fire, weather, operations, communications or audit differ from replay')
        independent = recompute(replay._audit, terminal=replay.terminated,
                                abandoned=replay.abandoned, censored=replay.censored)
        actual = replay.outcomes()
        if set(actual) != set(independent):
            raise ValueError('Independent outcome accounting fields differ')
        for key, value in independent.items():
            other = actual.get(key)
            if (type(value) is not type(other) or
                    (not math.isclose(value, other, rel_tol=1e-12, abs_tol=1e-12)
                     if type(value) is float else value != other)):
                raise ValueError('Independent outcome accounting differs')
        if type(self.reward) is not type(independent['graded_score']) or not math.isclose(
                self.reward, independent['graded_score'], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError('Recorded reward differs from independent accounting')
        return dict(passed=True, exact_replay=True, independent_reward_agreement=True, outcomes=independent)
