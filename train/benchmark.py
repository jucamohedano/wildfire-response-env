"""Opt-in benchmark demonstrations, frozen retrieval, and offline LoRA training."""
import argparse
import copy
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal


MODEL_ID = 'Qwen/Qwen3-1.7B'
MODEL_REVISION = '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
HORIZON = 120
DEMONSTRATION_VERSION = 'wildfire-benchmark-demonstration-v1'
VALIDATION_VERSION = 'wildfire-benchmark-validation-v1'
PINNED = dict(torch='2.11.0', transformers='5.20.1', trl='1.16.0',
              peft='0.21.2', huggingface_hub='2.2.0')
READINESS_FAMILIES = ('uncertain_smoke', 'wind_shift', 'overlapping_fires', 'failed_protection')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def seal(value):
    result = copy.deepcopy(value)
    result['sha256'] = checksum({key: item for key, item in result.items() if key != 'sha256'})
    return result


def verify_seal(value):
    if not isinstance(value, dict) or value.get('sha256') != checksum({
            key: item for key, item in value.items() if key != 'sha256'}):
        raise ValueError('Artifact checksum mismatch')
    return value


def split_ids(catalog, split):
    from envs.wildfire_response.regional.benchmark_v1.catalog import task_ids
    return tuple(task_ids(catalog, split))


def catalog_pin(environment):
    pin = environment.catalog.get('sha256')
    if not isinstance(pin, str) or len(pin) != 64 or any(char not in '0123456789abcdef' for char in pin):
        raise ValueError('A checksum-pinned benchmark catalog is required')
    return pin


def require_training_task(environment, task_id):
    if task_id not in split_ids(environment.catalog, 'train'):
        raise ValueError('Training, demonstrations and retrieval accept frozen train tasks only')


def project(observation):
    from envs.wildfire_response.regional.benchmark_v1.model import public_context
    context = public_context(observation)
    for district in context.get('districts', []):
        district.pop('exposed', None)
    return context


def instructions():
    from envs.wildfire_response.regional.benchmark_v1.model import INSTRUCTIONS
    return INSTRUCTIONS


def parse_tool(name, arguments):
    from wildfire_core.compact_ops_model import parse_action
    action = parse_action(arguments if isinstance(arguments, str) else canonical(arguments), tool=name)
    action.pop('explanation', None)
    return action


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


class RolloutRejected(ValueError):
    """The episode cannot supply a learning label."""


class BenchmarkRollout:
    """Train-only compact-tool episode. Only projected actor data leaves its tools."""

    def __init__(self, factory, bank=None):
        self._factory = factory
        self._bank = bank
        self._core = None
        self._finished = False
        self._aborted = None
        self.reward = 0.0

    def reset(self, task_id, seed=None, **kwargs):
        if kwargs:
            raise ValueError('Unknown training reset options')
        if self._core is not None and not self._finished and self._aborted is None:
            raise RolloutRejected('Finish the active episode before reset')
        environment = self._factory()
        self._require_task(environment, task_id, seed)
        self._catalog_sha256 = catalog_pin(environment)
        if self._bank is not None and self._bank.catalog_sha256 != self._catalog_sha256:
            raise ValueError('Retrieval catalog mismatch')
        observation, info = environment.reset(task_id, seed)
        self._core, self._observation = environment, observation
        self.task_id, self.seed = task_id, seed
        self._terminated = self._truncated = self._finished = False
        self._aborted = None
        self._origins, self._calls = [], []
        self._parse_diagnostics = []
        self.model_commits = self.valid_commits = 0
        self._invalid_streak = 0
        self._last_action_error = self._last_action_valid = None
        self.label_origin = None
        self._queries_remaining = 3
        self.reward = 0.0
        self._guard(info)
        return self._public()

    def _require_task(self, environment, task_id, seed):
        require_training_task(environment, task_id)

    def _guard(self, info):
        status = self._observation.get('topographic_status', {})
        if (info.get('boundary_censored') is True or status.get('boundary_censored') is True
                or info.get('suitable_for_training') is False):
            self.abort('boundary_censored')
            raise RolloutRejected('Boundary-censored episode has no learning label')

    def abort(self, reason='provider_failure'):
        self._aborted = reason

    def _live(self):
        if (self._core is None or self._finished or self._aborted is not None
                or self._terminated or self._truncated):
            raise RolloutRejected('Reset a live, unaborted training episode first')

    def _public(self):
        context = project(self._observation)
        data = dict(observation=context, done=self._terminated or self._truncated,
            last_action_error=self._last_action_error, last_action_valid=self._last_action_valid,
            remaining_time_rule='Generation stop in a live episode preserves standing missions; evaluator passively advances to minute 120 with no new orders.')
        if self._bank is not None:
            data['training_cases'] = self._bank.nearest(context)
        return canonical(data)

    def dispatch(self, name, arguments):
        self._live()
        try:
            if name in ('get_reports', 'get_events'):
                from wildfire_core.compact_ops_model import public_context as full_context
                from envs.wildfire_response.regional.benchmark_v1.model import query
                if self._queries_remaining == 0:
                    raise ValueError('At most three bounded evidence queries per decision')
                self._queries_remaining -= 1
                field = 'reports' if name == 'get_reports' else 'incident_events'
                raw = arguments if isinstance(arguments, str) else canonical(arguments)
                unused_arguments, result = query(raw, full_context(self._observation).get(field, []))
                return canonical(dict(result, remaining_queries=self._queries_remaining))
            from envs.wildfire_response.regional.ops.compact_model import FOCUSED_FIELDS
            committing = name in ('submit_action', 'advance_time', *FOCUSED_FIELDS)
            parse_error = None
            try:
                action = parse_tool(name, arguments)
                recorded_arguments = json.loads(arguments) if isinstance(arguments, str) else copy.deepcopy(arguments)
            except (ValueError, TypeError) as error:
                if not committing:
                    raise
                action = {'_invalid_model_action': True}
                parse_error = f'{type(error).__name__}: {error}'[:200]
                recorded_arguments = arguments if isinstance(arguments, str) else repr(arguments)[:65536]
                self._parse_diagnostics.append(dict(tool=name, minute=self._observation['minute'], error=parse_error))
            if name == 'check_action':
                checked = self._core.check_orders(action)
                return canonical({key: checked[key] for key in ('valid', 'error', 'remaining_checks') if key in checked})
            self.model_commits += 1
            valid = self._advance(action, 'model')
            self.valid_commits += valid
            if parse_error is not None:
                if valid:
                    raise RolloutRejected('Engine accepted an explicitly invalid model action')
                self._last_action_error = f'Action parse failed: {parse_error}; {self._last_action_error}'[:400]
            self._calls.append(dict(name=name, arguments=recorded_arguments))
            return self._public()
        except Exception:
            self.abort('invalid_or_failed_tool')
            raise

    def _advance(self, action, origin):
        before = self._observation['minute']
        observation, reward, terminated, truncated, info = self._core.step(action)
        self._observation = observation
        self._terminated, self._truncated = terminated, truncated
        self._origins.append(origin)
        self._queries_remaining = 3
        self._guard(info)
        valid = info.get('error') is None and info.get('action_valid') is not False
        self._invalid_streak = 0 if valid else self._invalid_streak + 1
        self._last_action_valid = valid
        self._last_action_error = None if valid else str(info.get('error') or 'Invalid committed action')[:200]
        if not finite_number(reward) or observation['minute'] <= before or observation['minute'] > HORIZON:
            self.abort('invalid_transition')
            raise RolloutRejected('Nonfinite reward or invalid clock')
        self.reward += reward
        if truncated and (valid or self._invalid_streak < 3):
            self.abort('truncated')
            raise RolloutRejected('Unexplained truncation has no learning label')
        return valid

    def finish(self):
        if self._aborted is not None:
            raise RolloutRejected('Aborted rollout has no reward or expert label')
        if self._finished:
            return self.reward
        if self._core is None:
            raise RolloutRejected('Reset before finalization')
        for unused in range(60):
            if self._terminated or self._truncated:
                break
            self._advance({}, 'passive_finalization')
        abandoned = self._truncated and not self._terminated and self._invalid_streak >= 3
        if not abandoned and (not self._terminated or self._truncated or self._observation['minute'] != HORIZON):
            self.abort('incomplete_horizon')
            raise RolloutRejected('A complete uncensored minute-120 outcome is required')
        try:
            verified = self._core.verify_episode()
        except Exception as error:
            self.abort('unverified')
            raise RolloutRejected('Episode verification failed') from error
        outcomes = verified.get('outcomes', verified)
        if (verified.get('passed') is not True or verified.get('suitable_for_training') is not True
                or outcomes.get('boundary_censored') is not False
                or outcomes.get('invalid_abandonment', False) is not abandoned):
            self.abort('unverified')
            raise RolloutRejected('Episode verification failed or episode is unsuitable')
        if not finite_number(self._core.reward) or not math.isclose(self.reward, self._core.reward, rel_tol=0, abs_tol=1e-10):
            self.abort('reward_mismatch')
            raise RolloutRejected('Verified reward accounting mismatch')
        if abandoned:
            transitions = self._core.trajectory()['transitions']
            if (len(transitions) < 3 or any(transition.get('action_valid') is not False for transition in transitions[-3:])
                    or self.reward != 0 or self._core.reward != 0 or outcomes.get('graded_score') != 0):
                self.abort('unverified_abandonment')
                raise RolloutRejected('Abandonment must independently verify three invalid commits and zero engine reward')
        self.label_origin = 'failed_rl' if abandoned else 'rl'
        self._verification = verified
        self._finished = True
        return self.reward


def outcome_reward(environments, **kwargs):
    return [environment._finish() for environment in environments]


def build_demonstration(factory, task_id, *, policy=None, recorded=None, seed=None):
    """Replay a complete recording or run a policy that sees projected observations only."""
    if (policy is None) == (recorded is None):
        raise ValueError('Supply exactly one causal policy or recorded training demonstration')
    if recorded is not None:
        verify_seal(recorded)
        if recorded.get('version') != DEMONSTRATION_VERSION or recorded.get('task_id') != task_id:
            raise ValueError('Wrong demonstration version or task')
        if recorded.get('source') not in ('causal_policy', 'recorded_training_trajectory'):
            raise ValueError('Provider failures and fallback completions are not expert labels')
        if any(origin != 'model' for origin in recorded.get('origins', [])):
            raise ValueError('Passive completions are scoring only, never expert demonstrations')
        seed = recorded['seed']
    rollout = BenchmarkRollout(factory)
    rollout.reset(task_id, seed)
    if recorded is not None and recorded['catalog_sha256'] != rollout._catalog_sha256:
        raise ValueError('Demonstration catalog checksum mismatch')
    examples = []
    calls = recorded['calls'] if recorded is not None else None
    for decision in range(60):
        if rollout._terminated or rollout._truncated:
            break
        context = project(rollout._observation)
        if calls is not None:
            if decision >= len(calls):
                raise ValueError('Incomplete recorded policy')
            call = copy.deepcopy(calls[decision])
        else:
            call = policy(copy.deepcopy(context))
        if (not isinstance(call, dict) or set(call) != {'name', 'arguments'}
                or call['name'] in ('check_action', 'get_reports', 'get_events')):
            raise ValueError('A demonstration needs one committing compact tool per decision')
        rollout.dispatch(call['name'], call['arguments'])
        if rollout.valid_commits != rollout.model_commits:
            raise RolloutRejected('Expert demonstrations reject every invalid committed action')
        examples.append(dict(observation=context, call=copy.deepcopy(rollout._calls[-1])))
    if not rollout._terminated or rollout._truncated or rollout.model_commits == 0:
        raise ValueError('A complete causal policy is required; no tactical fallback or passive labels')
    rollout.finish()
    if rollout.label_origin == 'failed_rl' or any(transition.get('action_valid') is not True
                                               for transition in rollout._core.trajectory()['transitions']):
        raise RolloutRejected('Failed RL samples and invalid transitions are never expert labels')
    result = seal(dict(version=DEMONSTRATION_VERSION, source=recorded['source'] if recorded is not None else 'causal_policy',
        task_id=task_id, seed=seed, catalog_sha256=rollout._catalog_sha256,
        calls=rollout._calls, origins=rollout._origins, examples=examples,
        trajectory=rollout._core.trajectory(), reward=rollout.reward))
    if recorded is not None:
        for field in ('calls', 'origins', 'examples', 'trajectory', 'reward'):
            if canonical(recorded[field]) != canonical(result[field]):
                raise ValueError('Recorded demonstration does not match independent causal replay: ' + field)
    return result


def verify_demonstration(factory, demonstration):
    return build_demonstration(factory, demonstration['task_id'], recorded=demonstration)


def demonstration_from_trajectory(factory, trajectory, *, expected_sha256):
    if checksum(trajectory) != expected_sha256:
        raise ValueError('Recorded trajectory checksum mismatch')
    recording = trajectory
    if (recording.get('version') != 'wildfire-recorded-training-trajectory-v1'
            or recording.get('source') != 'recorded_training_trajectory'
            or type(recording.get('provider_failures')) is not int or recording['provider_failures'] != 0
            or type(recording.get('fallback_actions')) is not int or recording['fallback_actions'] != 0):
        raise ValueError('Recording needs explicit failure-free policy provenance; raw traces cannot certify expert labels')
    trajectory = recording.get('trajectory', {})
    environment = factory()
    task_id = trajectory.get('task_id')
    require_training_task(environment, task_id)
    if trajectory.get('catalog_sha256') != catalog_pin(environment):
        raise ValueError('Recorded trajectory catalog checksum mismatch')
    transitions = trajectory.get('transitions')
    if (not isinstance(transitions, list) or not 1 <= len(transitions) <= 60
            or recording.get('origins') != ['model'] * len(transitions)
            or any(transition.get('action_valid') is not True
                   or transition.get('origin', 'model') != 'model' for transition in transitions)):
        raise ValueError('Recording must contain only complete valid policy commits')
    environment.reset(task_id, trajectory.get('seed'))
    for transition in transitions:
        environment.step(transition['action'])
    if canonical(environment.trajectory()) != canonical(trajectory):
        raise ValueError('Recorded trajectory differs from independent replay')
    calls = iter([dict(name='submit_action', arguments=transition['action']) for transition in transitions])
    demonstration = build_demonstration(factory, task_id, seed=trajectory.get('seed'),
        policy=lambda context: next(calls))
    demonstration['source'] = 'recorded_training_trajectory'
    return seal(demonstration)


def baseline_demonstration(factory, task_id, policy='cooperative'):
    if policy not in ('cooperative', 'dispatcher'):
        raise ValueError('Use a complete cooperative or dispatcher causal policy')
    from envs.wildfire_response.regional.benchmark_v1.baselines import PublicDispatcher
    dispatcher = PublicDispatcher(policy=policy)
    return build_demonstration(factory, task_id,
        policy=lambda context: dict(name='submit_action', arguments=dispatcher(context)))


def public_features(context):
    """Fixed, bounded features of already-delivered actor data, with unknowns separate."""
    context = project(context)
    resources = context.get('resources', [])
    districts = context.get('districts', [])
    reports = context.get('reports', [])
    values = (min(context['minute'], HORIZON) / HORIZON,
        min(len(reports), 20) / 20,
        min(sum(report.get('location') is None for report in reports), 20) / 20,
        min(sum(unit.get('state') == 'available' for unit in resources), 12) / 12,
        min(sum(unit.get('state') is None for unit in resources), 12) / 12,
        min(sum(group.get('smoke_present') is True for group in districts), 4) / 4,
        min(sum(group.get('smoke_present') is None for group in districts), 4) / 4,
        min(len(context.get('active_incidents', [])), 20) / 20)
    return tuple(values)


def case_context(context):
    fields = dict(resources=('id', 'kind', 'state', 'sector_id', 'mission', 'target', 'water_litres', 'observed_at', 'received_at'),
        districts=('id', 'population', 'home', 'refuge', 'status', 'smoke_present', 'observed_at', 'received_at'),
        reports=('id', 'location', 'observed_at', 'received_at'),
        offers=('id', 'resource_id', 'sector_id', 'mission', 'status', 'expires_at'),
        readbacks=('id', 'offer_id', 'resource_id', 'sector_id', 'mission', 'status'),
        sectors=('id', 'center'))
    result = {key: copy.deepcopy(context[key]) for key in ('minute', 'wind', 'daylight', 'aircraft_operable') if key in context}
    for name, keys in fields.items():
        records = context.get(name, [])
        result[name] = [{key: copy.deepcopy(record[key]) for key in keys if key in record}
                        for record in records[:12 if name == 'resources' else 4]]
    return result


@dataclass(frozen=True)
class FrozenRetrievalBank:
    catalog_sha256: str
    entries: tuple[str, ...]

    @classmethod
    def from_demonstrations(cls, factory, demonstrations, *, max_cases=2000):
        if type(max_cases) is not int or not 1 <= max_cases <= 2000:
            raise ValueError('Retrieval permits 1–2000 frozen training cases')
        environment = factory()
        pin = catalog_pin(environment)
        cases = {}
        for demonstration in demonstrations:
            verified = verify_demonstration(factory, demonstration)
            if verified['catalog_sha256'] != pin:
                raise ValueError('Retrieval catalog mismatch')
            for index, example in enumerate(verified['examples']):
                case = dict(id=checksum(dict(task=verified['task_id'], index=index, example=example)),
                    features=public_features(example['observation']), observation=case_context(example['observation']),
                    call=example['call'])
                encoded = canonical(case)
                if len(encoded.encode()) <= 8192:
                    cases[case['id']] = encoded
        return cls(pin, tuple(cases[key] for key in sorted(cases)[:max_cases]))

    def nearest(self, observation, *, limit=3, max_bytes=16384):
        if type(limit) is not int or not 1 <= limit <= 4 or type(max_bytes) is not int or not 2 <= max_bytes <= 16384:
            raise ValueError('Retrieval permits at most four cases and 16 KiB')
        target = public_features(observation)
        ranked = []
        for encoded in self.entries:
            case = json.loads(encoded)
            distance = sum((left - right) ** 2 for left, right in zip(target, case['features']))
            ranked.append((distance, case['id'], case))
        selected = []
        for distance, identifier, case in sorted(ranked)[:limit]:
            public_case = {key: case[key] for key in ('id', 'observation', 'call')}
            if len(canonical([*selected, public_case]).encode()) <= max_bytes:
                selected.append(public_case)
        return selected


def readiness_plan(environment):
    plan = []
    for family in READINESS_FAMILIES:
        group = next((group for group in environment.catalog['groups']
                      if group['split'] == 'validation' and group.get('family') == family), None)
        if group is None:
            raise ValueError('Catalog lacks a preregistered readiness family')
        case = next((case for case in group['cases'] if case.get('variant') == 0), None)
        if case is None or type(case.get('seed')) is not int:
            raise ValueError('Readiness requires the frozen variant-0 task and seed')
        plan.append(dict(group_id=group['id'], family=family, split='validation',
            task_id=case['id'], seed=case['seed'], variant=0, rollouts=4))
    return plan


def validation_gate(report, environment, *, expected_sha256, expected_adapter_files_sha256):
    if checksum(report) != expected_sha256 or report.get('version') != VALIDATION_VERSION:
        raise ValueError('Validation report version/checksum mismatch')
    if report.get('status') != 'complete':
        raise ValueError('Only a complete actual model evaluation can establish readiness')
    if report.get('catalog_sha256') != catalog_pin(environment) or report.get('verified') is not True:
        raise ValueError('Validation report must be verified against this catalog')
    if (not isinstance(expected_adapter_files_sha256, dict) or not expected_adapter_files_sha256
            or not {'adapter_model.safetensors', 'adapter_config.json'} <= set(expected_adapter_files_sha256)
            or any(not isinstance(value, str) or len(value) != 64
                   or any(character not in '0123456789abcdef' for character in value)
                   for value in expected_adapter_files_sha256.values())
            or report.get('adapter_files_sha256') != expected_adapter_files_sha256):
        raise ValueError('Readiness must evaluate the exact accepted SFT adapter checkpoint')
    provenance = expected_model_provenance(environment)
    if any(report.get(key) != value for key, value in provenance.items()):
        raise ValueError('Readiness requires actual model_evaluation provenance and matching model/contract/environment/reward pins')
    if (report.get('model_loaded') is not True or type(report.get('model_inference_calls')) is not int
            or report['model_inference_calls'] <= 0 or type(report.get('scripted_actions')) is not int
            or report['scripted_actions'] != 0 or type(report.get('provider_failures')) is not int
            or report['provider_failures'] != 0):
        raise ValueError('CPU scripted metrics and failed-provider rollouts cannot establish model readiness')
    plan = readiness_plan(environment)
    if report.get('readiness_plan_sha256') != checksum(plan):
        raise ValueError('Readiness report does not pin the preregistered task/seed plan')
    groups = report.get('groups')
    if not isinstance(groups, list) or len(groups) != 4:
        raise ValueError('Readiness requires the four preregistered groups, separate from full validation')
    group_ids = set()
    committed = valid = diverse = inference_calls = 0
    expected_groups = {item['group_id']: item for item in plan}
    for group in groups:
        if (group.get('split') != 'validation' or not isinstance(group.get('group_id'), str)
                or group['group_id'] in group_ids or group.get('verified') is not True
                or group.get('boundary_censored') is not False):
            raise ValueError('Unverified, repeated, censored or non-validation group')
        group_ids.add(group['group_id'])
        expected = expected_groups.get(group['group_id'])
        if (expected is None or any(group.get(key) != expected[key] for key in ('family', 'task_id', 'seed', 'variant'))
                or type(group.get('seed')) is not int or type(group.get('variant')) is not int):
            raise ValueError('Readiness requires each preregistered variant-0 task and fixed environment seed')
        rollouts = group.get('rollouts')
        if not isinstance(rollouts, list) or len(rollouts) != 4:
            raise ValueError('Four model rollouts of the SAME task and seed are required per group')
        rewards, rollout_ids = [], set()
        for rollout in rollouts:
            if (type(rollout.get('rollout_id')) is not int or not 0 <= rollout['rollout_id'] < 4
                    or rollout['rollout_id'] in rollout_ids or rollout.get('task_id') != expected['task_id']
                    or type(rollout.get('seed')) is not int or rollout['seed'] != expected['seed']
                    or rollout.get('verified') is not True or rollout.get('boundary_censored') is not False
                    or rollout.get('source') != 'model_evaluation'):
                raise ValueError('Unverified, repeated, censored, wrong-source, different-task or different-seed model rollout')
            rollout_ids.add(rollout['rollout_id'])
            reward = rollout.get('reward')
            if not finite_number(reward) or not 0 <= reward <= 1:
                raise ValueError('Each model rollout requires a finite verified outcome reward')
            rewards.append(reward)
            count, successes = rollout.get('committed_actions'), rollout.get('valid_committed_actions')
            calls = rollout.get('model_inference_calls')
            if (type(count) is not int or type(successes) is not int or not 0 <= successes <= count or count == 0
                    or type(calls) is not int or calls <= 0
                    or type(rollout.get('scripted_actions')) is not int or rollout['scripted_actions'] != 0
                    or type(rollout.get('provider_failures')) is not int or rollout['provider_failures'] != 0):
                raise ValueError('Invalid model-only committed-action or provider evidence')
            committed += count
            valid += successes
            inference_calls += calls
        diverse += max(rewards) - min(rewards) > 1e-10
    if group_ids != set(expected_groups) or report['model_inference_calls'] != inference_calls:
        raise ValueError('Missing preregistered group or inconsistent model inference accounting')
    if valid / committed < .95 or diverse / len(groups) < .25:
        raise ValueError('Training gate requires >=95% valid commits and reward variation in >=25% of groups')
    return dict(model_quality_gate_required=True, valid_commit_fraction=valid / committed, reward_variation_fraction=diverse / len(groups),
                validation_groups=len(groups), rollouts=16, readiness_plan_sha256=checksum(plan),
                validation_report_sha256=expected_sha256)


def expected_model_provenance(environment):
    from envs.wildfire_response.regional.benchmark_v1.model import CONTRACT_VERSION
    from envs.wildfire_response.regional.benchmark_v1.observations import VERSION as OBSERVATION_VERSION
    from envs.wildfire_response.regional.benchmark_v1.calibration import public_profile
    from envs.wildfire_response.regional.benchmark_v1.environment import VERSION, implementation_digest
    from envs.wildfire_response.regional.benchmark_v1.outcomes import VERSION as REWARD_VERSION, RULES
    catalog = environment.catalog
    profile = getattr(environment, '_profile', 'central')
    root = Path(__file__).resolve().parents[1]
    contract_files = ('benchmark_v1/model.py', 'ops/compact_model.py', 'ops/model.py', 'director/policy.py')
    contract_sha256 = checksum({name: file_digest(root/'envs/wildfire_response/core/src/envs/wildfire_response/regional'/name)
                               for name in contract_files})
    return dict(source='model_evaluation', model_id=MODEL_ID, model_revision=MODEL_REVISION,
        model_contract_sha256=contract_sha256,
        contract_version=CONTRACT_VERSION, env_version=VERSION, reward_version=REWARD_VERSION,
        observation_version=OBSERVATION_VERSION,
        catalog_sha256=catalog_pin(environment), environment_implementation_sha256=implementation_digest(),
        reward_rules_sha256=checksum(RULES), prepared_inputs_sha256=catalog['prepared_inputs_sha256'],
        topographic_inputs_sha256=catalog['topographic_inputs_sha256'], profile=profile,
        profile_sha256=checksum(public_profile(profile)))


def require_host_timeout(wall_seconds):
    try:
        arguments = Path(f'/proc/{os.getppid()}/cmdline').read_bytes().split(b'\0')
    except OSError as error:
        raise RuntimeError('Cannot verify the required host timeout wrapper') from error
    if (Path(os.fsdecode(arguments[0])).name != 'timeout' or len(arguments) < 4
            or arguments[1] != b'--signal=KILL' or arguments[2] != f'{wall_seconds}s'.encode()):
        raise RuntimeError('Run actual training under: timeout --signal=KILL <wall-seconds>s python train/benchmark.py ...')


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def adapter_files(directory):
    directory = Path(directory)
    return {path.name: file_digest(path) for path in sorted(directory.iterdir()) if path.is_file()}


def make_factory(config):
    allowed = {'prepared_inputs', 'topographic_inputs', 'expected_prepared_sha256',
        'expected_topographic_sha256', 'slope_enabled', 'allow_synthetic_conditions',
        'catalog', 'expected_catalog_sha256', 'profile'}
    if not isinstance(config, dict) or set(config) - allowed or config.get('allow_synthetic_conditions') is not True:
        raise ValueError('Use explicit pinned local benchmark inputs and synthetic opt-in')
    def factory():
        from wildfire_core.benchmark_v1 import BenchmarkEnv
        return BenchmarkEnv(**config, max_decisions=60, ticks_per_action=2)
    return factory


def inspect(factory):
    environment = factory()
    return dict(catalog_sha256=catalog_pin(environment), model=MODEL_ID, revision=MODEL_REVISION,
        splits={split: len(split_ids(environment.catalog, split))
                for split in ('train', 'validation', 'test', 'development')},
        horizon=HORIZON, training_dependencies=PINNED, model_loaded=False, provider_calls=0,
        first_training_task=split_ids(environment.catalog, 'train')[0], readiness=False,
        grpo_readiness_plan=readiness_plan(environment))


def dry_run(factory, task_id):
    rollout = BenchmarkRollout(factory)
    public = json.loads(rollout.reset(task_id))
    rollout.dispatch('advance_time', {})
    reward = rollout.finish()
    return dict(mode='cpu_dry_run', task_id=task_id, catalog_sha256=rollout._catalog_sha256,
        initial_public_keys=sorted(public['observation']), reward=reward,
        model_commits=rollout.model_commits,
        passive_finalization_decisions=rollout._origins.count('passive_finalization'),
        verified=True, expert_label=False, model_loaded=False, provider_calls=0)


class ToolRollout:
    """TRL's inspected method tools dispatch through the same compact v2 parser."""

    def __init__(self, factory, bank=None):
        self._rollout = BenchmarkRollout(factory, bank=bank)

    def reset(self, task_id, seed=None, **kwargs):
        kwargs.pop('prompt', None)
        return self._rollout.reset(task_id, seed, **kwargs)

    def _dispatch(self, name, arguments):
        return self._rollout.dispatch(name, arguments)

    def _finish(self):
        return self._rollout.finish()

    def submit_action(self, requests: list[dict] = None, authorizations: list[str] = None,
                      releases: list[str] = None, messages: list[dict] = None,
                      readback_confirmations: list[str] = None, readback_corrections: list[dict] = None,
                      triage: list[dict] = None, clarifications: list[str] = None,
                      protection_orders: list[dict] = None, explanation: str = '') -> str:
        """Commit compatible sparse orders and advance two simulated minutes.

        Args:
            requests: Resource, sector and mission records.
            authorizations: Offered allocation IDs.
            releases: Resource IDs to return.
            messages: Recipient and purpose records.
            readback_confirmations: Matching readback IDs.
            readback_corrections: Readback ID, resource, sector and mission records.
            triage: Report IDs and assessment records.
            clarifications: Unlocated report IDs.
            protection_orders: Recipient, action, channel, resource_id and supersedes records.
            explanation: Optional unscored explanation.
        """
        arguments = {key: value for key, value in locals().items() if key != 'self' and value is not None}
        return self._dispatch('submit_action', arguments)

    def check_action(self, requests: list[dict] = None, authorizations: list[str] = None,
                     releases: list[str] = None, messages: list[dict] = None,
                     readback_confirmations: list[str] = None, readback_corrections: list[dict] = None,
                     triage: list[dict] = None, clarifications: list[str] = None,
                     protection_orders: list[dict] = None, explanation: str = '') -> str:
        """Validate a sparse draft without advancing time.

        Args:
            requests: Resource, sector and mission records.
            authorizations: Offered allocation IDs.
            releases: Resource IDs to return.
            messages: Recipient and purpose records.
            readback_confirmations: Matching readback IDs.
            readback_corrections: Readback ID, resource, sector and mission records.
            triage: Report IDs and assessment records.
            clarifications: Unlocated report IDs.
            protection_orders: Recipient, action, channel, resource_id and supersedes records.
            explanation: Optional unscored explanation.
        """
        arguments = {key: value for key, value in locals().items() if key != 'self' and value is not None}
        return self._dispatch('check_action', arguments)

    def get_reports(self, ids: list[str] = None, offset: int = 0, limit: int = 20) -> str:
        """Read at most twenty received reports without advancing time.

        Args:
            ids: Optional received report IDs.
            offset: Offset in the selected received reports.
            limit: Number of records, between one and twenty.
        """
        arguments = dict(offset=offset, limit=limit)
        if ids is not None:
            arguments['ids'] = ids
        return self._dispatch('get_reports', arguments)

    def get_events(self, ids: list[str] = None, offset: int = 0, limit: int = 20) -> str:
        """Read at most twenty delivered events without advancing time.

        Args:
            ids: Optional received event IDs.
            offset: Offset in the selected delivered events.
            limit: Number of records, between one and twenty.
        """
        arguments = dict(offset=offset, limit=limit)
        if ids is not None:
            arguments['ids'] = ids
        return self._dispatch('get_events', arguments)

    def advance_time(self) -> str:
        """Advance two simulated minutes with no new orders."""
        return self._dispatch('advance_time', {})

    def request_resources(self, requests: list[dict]) -> str:
        """Request allocations from owning bases.

        Args:
            requests: Records with resource_id, sector_id and mission.
        """
        return self._dispatch('request_resources', dict(requests=requests))

    def authorize_dispatch(self, authorizations: list[str], readback_confirmations: list[str] = None) -> str:
        """Authorize offered allocations after matching readbacks.

        Args:
            authorizations: Offered allocation IDs.
            readback_confirmations: Matching readback IDs.
        """
        return self._dispatch('authorize_dispatch', dict(authorizations=authorizations,
                                                       readback_confirmations=readback_confirmations or []))

    def protect_population(self, protection_orders: list[dict]) -> str:
        """Issue protection orders through the public protocol.

        Args:
            protection_orders: Records with recipient, action, channel, resource_id and supersedes.
        """
        return self._dispatch('protect_population', dict(protection_orders=protection_orders))

    def assess_reports(self, triage: list[dict]) -> str:
        """Assess received reports against fresh public sensor evidence.

        Args:
            triage: Records with report_ids and assessment.
        """
        return self._dispatch('assess_reports', dict(triage=triage))

    def clarify_reports(self, clarifications: list[str]) -> str:
        """Request clarification of unlocated claims.

        Args:
            clarifications: Received report IDs.
        """
        return self._dispatch('clarify_reports', dict(clarifications=clarifications))

    def release_resources(self, releases: list[str]) -> str:
        """Release assigned resources.

        Args:
            releases: Resource IDs.
        """
        return self._dispatch('release_resources', dict(releases=releases))

    def send_updates(self, messages: list[dict]) -> str:
        """Send bounded operational updates.

        Args:
            messages: Records with recipient and purpose.
        """
        return self._dispatch('send_updates', dict(messages=messages))

    def correct_readbacks(self, readback_corrections: list[dict]) -> str:
        """Restate original allocations after readback mismatches.

        Args:
            readback_corrections: Records with readback_id, resource_id, sector_id and mission.
        """
        return self._dispatch('correct_readbacks', dict(readback_corrections=readback_corrections))


class _ReadinessRollout(BenchmarkRollout):
    def _require_task(self, environment, task_id, seed):
        if not any(item['task_id'] == task_id and item['seed'] == seed
                   and type(seed) is int for item in readiness_plan(environment)):
            raise ValueError('Readiness accepts only preregistered validation tasks and their exact seeds')


def model_tools(observation, *, allow_check=True, allow_queries=True):
    from wildfire_core.compact_ops_model import action_schema
    from envs.wildfire_response.regional.ops.compact_model import FOCUSED_FIELDS
    from envs.wildfire_response.regional.benchmark_v1.model import read_schema
    names = ['submit_action', 'advance_time', *FOCUSED_FIELDS]
    if allow_check:
        names.append('check_action')
    tools = [dict(type='function', function=dict(name=name, description='Compact operations action',
        parameters=action_schema(observation, tool=name))) for name in names]
    if allow_queries:
        tools.extend(dict(type='function', function=dict(name=name, description='Read delivered evidence',
            parameters=read_schema())) for name in ('get_reports', 'get_events'))
    return tools


def decode_named_call(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise ValueError('Expected one bounded model tool call')
    raw = raw.strip()
    if raw.startswith('<tool_call>') and raw.endswith('</tool_call>'):
        raw = raw[len('<tool_call>'):-len('</tool_call>')].strip()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate model tool-call field')
            result[key] = value
        return result

    call = json.loads(raw, object_pairs_hook=unique)
    if (not isinstance(call, dict) or set(call) != {'name', 'arguments'}
            or not isinstance(call['name'], str) or not isinstance(call['arguments'], (dict, str))):
        raise ValueError('Expected exactly one named tool call with arguments')
    canonical(call)
    return call


class _LocalQwenTransport:
    def __init__(self, model, tokenizer, torch, completion_tokens):
        if model.device.type != 'cuda':
            raise RuntimeError('Actual readiness inference requires the explicitly provisioned CUDA model')
        self.model, self.tokenizer, self.torch = model, tokenizer, torch
        self.completion_tokens = completion_tokens

    def __call__(self, messages, tools):
        encoded = self.tokenizer.apply_chat_template(messages, tools=tools, enable_thinking=False,
            add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors='pt')
        if encoded['input_ids'].shape[-1] > 16384:
            raise ValueError('Readiness prompt exceeds the bounded context; truncation is disabled')
        encoded = encoded.to(self.model.device)
        with self.torch.inference_mode():
            generated = self.model.generate(**encoded, max_new_tokens=self.completion_tokens,
                do_sample=True, temperature=.7, top_p=.8, top_k=20,
                pad_token_id=self.tokenizer.eos_token_id, return_dict_in_generate=False)
        completion = generated[0, encoded['input_ids'].shape[-1]:]
        endings = self.model.generation_config.eos_token_id
        endings = endings if isinstance(endings, list) else [endings]
        if len(completion) == 0 or completion[-1].item() not in endings:
            raise ValueError('Incomplete model completion; no fallback or readiness label')
        return decode_named_call(self.tokenizer.decode(completion, skip_special_tokens=True))


def assemble_readiness_report(factory, transport_factory, *, adapter_files_sha256,
                              bank=None, tool_iterations=64, audit_directory=None):
    """Exercise report assembly with injected transports; only the CUDA transport attests inference."""
    if type(tool_iterations) is not int or not 1 <= tool_iterations <= 240:
        raise ValueError('Readiness tool calls must be bounded between one and 240 per rollout')
    environment = factory()
    provenance = expected_model_provenance(environment)
    plan = readiness_plan(environment)
    report = dict(provenance, version=VALIDATION_VERSION, verified=True, model_loaded=False,
        source='fake_model_transport', adapter_files_sha256=copy.deepcopy(adapter_files_sha256),
        readiness_plan_sha256=checksum(plan), groups=[], model_inference_calls=0,
        scripted_actions=0, provider_failures=0, transport_calls=0, status='complete',
        retrieval_sha256=checksum(list(bank.entries) if bank is not None else []),
        sampling=dict(temperature=.7, top_p=.8, top_k=20, generation_seed_base=20261026),
        full_validation_benchmark=False)
    actual_transports = []
    for group_index, planned in enumerate(plan):
        group = dict(planned, verified=True, boundary_censored=False, rollouts=[])
        for rollout_id in range(4):
            index = group_index * 4 + rollout_id
            transport = transport_factory(index)
            actual = isinstance(transport, _LocalQwenTransport)
            actual_transports.append(actual)
            rollout = _ReadinessRollout(factory, bank=bank)
            public = rollout.reset(planned['task_id'], planned['seed'])
            messages = [dict(role='system', content=instructions()), dict(role='user', content=public)]
            calls = checks = 0
            for turn in range(tool_iterations):
                if rollout._terminated or rollout._truncated:
                    break
                context = project(rollout._observation)
                tools = model_tools(context, allow_check=checks < 3, allow_queries=rollout._queries_remaining > 0)
                call = transport(copy.deepcopy(messages), tools)
                calls += 1
                if (not isinstance(call, dict) or set(call) != {'name', 'arguments'}
                        or call['name'] not in {tool['function']['name'] for tool in tools}):
                    rollout.abort('invalid_model_tool')
                    raise RolloutRejected('Readiness model returned an unavailable or malformed tool')
                before = rollout.model_commits
                result = rollout.dispatch(call['name'], call['arguments'])
                if rollout.model_commits > before:
                    checks = 0
                    messages = [dict(role='system', content=instructions()), dict(role='user', content=result)]
                else:
                    checks += call['name'] == 'check_action'
                    identifier = f'call-{index}-{turn}'
                    arguments = json.loads(call['arguments']) if isinstance(call['arguments'], str) else call['arguments']
                    messages.extend([dict(role='assistant', content=None, tool_calls=[dict(id=identifier,
                        type='function', function=dict(name=call['name'], arguments=arguments))]),
                        dict(role='tool', tool_call_id=identifier, content=result)])
            reward = rollout.finish()
            if expected_model_provenance(rollout._core) != provenance:
                raise ValueError('Environment or model-contract pins changed during readiness evaluation')
            row = dict(rollout_id=rollout_id, task_id=planned['task_id'], seed=planned['seed'],
                generation_seed=20261026 + index, source='model_evaluation' if actual else 'fake_model_transport',
                verified=True, boundary_censored=False, reward=reward,
                model_inference_calls=calls if actual else 0, transport_calls=calls,
                scripted_actions=0 if actual else rollout.model_commits, provider_failures=0,
                committed_actions=rollout.model_commits, valid_committed_actions=rollout.valid_commits,
                label_origin=rollout.label_origin, invalid_abandonment=rollout.label_origin == 'failed_rl',
                passive_finalization_decisions=rollout._origins.count('passive_finalization'),
                trajectory_sha256=checksum(rollout._core.trajectory()))
            if audit_directory is not None:
                directory = Path(audit_directory) / f'group-{group_index}-rollout-{rollout_id}'
                directory.mkdir(parents=True)
                (directory / 'trajectory.json').write_text(canonical(rollout._core.trajectory()) + '\n')
                (directory / 'origins.json').write_text(canonical(rollout._origins) + '\n')
                (directory / 'label.json').write_text(canonical(dict(origin=rollout.label_origin,
                    reward=reward, expert=False)) + '\n')
                (directory / 'parse_diagnostics.json').write_text(canonical(rollout._parse_diagnostics) + '\n')
                (directory / 'private').mkdir()
                (directory / 'private' / 'verification.json').write_text(canonical(rollout._verification) + '\n')
            group['rollouts'].append(row)
            report['model_inference_calls'] += row['model_inference_calls']
            report['transport_calls'] += calls
            report['scripted_actions'] += row['scripted_actions']
        report['groups'].append(group)
    if all(actual_transports):
        report.update(source='model_evaluation', model_loaded=True)
    return report


def train(factory, *, stage, output, validation_report=None, validation_sha256=None, demonstrations=(),
          enable_gpu=False, agreed_budget_seconds=None, wall_seconds=7200, steps=2,
          completion_tokens=1536, tool_iterations=64, sft_adapter=None, agreed_budget_eur=None):
    if enable_gpu is not True:
        raise ValueError('Actual training requires explicit --enable-gpu')
    if (type(agreed_budget_seconds) is not int or not 1 <= agreed_budget_seconds <= 7200
            or type(wall_seconds) is not int or not 1 <= wall_seconds <= agreed_budget_seconds
            or not finite_number(agreed_budget_eur) or agreed_budget_eur < 0):
        raise ValueError('Explicit agreed time/spending budgets and a hard wall of at most two hours are required')
    if (stage not in ('sft', 'validate', 'grpo') or type(steps) is not int or not 1 <= steps <= 256
            or type(completion_tokens) is not int or not 256 <= completion_tokens <= 4096
            or type(tool_iterations) is not int or not 1 <= tool_iterations <= 240):
        raise ValueError('Invalid bounded training configuration')
    if not hasattr(signal, 'setitimer'):
        raise RuntimeError('Training requires the POSIX process hard-wall timer')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh training output directory')
    require_host_timeout(wall_seconds)
    verified = [verify_demonstration(factory, demonstration) for demonstration in demonstrations]
    if stage == 'sft' and not verified:
        raise ValueError('SFT requires complete independently replayed train demonstrations')
    gate = dict(model_quality_gate_required=False, verified_training_demonstrations=len(verified))
    if stage in ('validate', 'grpo'):
        if stage == 'grpo' and (not isinstance(validation_report, dict) or not validation_sha256):
            raise ValueError('GRPO requires an actual SFT-adapter model validation report and checksum')
        if sft_adapter is None:
            raise ValueError('Validation and GRPO require the accepted SFT output directory')
        warm_start = verify_seal(json.loads((Path(sft_adapter) / 'summary.json').read_text()))
        if (warm_start.get('stage') != 'sft' or warm_start.get('model') != MODEL_ID
                or warm_start.get('revision') != MODEL_REVISION
                or warm_start.get('catalog_sha256') != catalog_pin(factory())
                or warm_start.get('adapter_changed') is not True):
            raise ValueError('SFT warm-start metadata does not match this staged run')
        if warm_start.get('adapter_files_sha256') != adapter_files(Path(sft_adapter) / 'adapter'):
            raise ValueError('SFT adapter files do not match their accepted checksums')
        expected_pins = {key: value for key, value in expected_model_provenance(factory()).items() if key != 'source'}
        if warm_start.get('pins') != expected_pins:
            raise ValueError('SFT warm start has stale environment/model/profile pins')
        if stage == 'grpo':
            gate = validation_gate(validation_report, factory(), expected_sha256=validation_sha256,
                expected_adapter_files_sha256=warm_start['adapter_files_sha256'])
    bank = FrozenRetrievalBank.from_demonstrations(factory, verified)
    if stage == 'grpo' and validation_report.get('retrieval_sha256') != checksum(list(bank.entries)):
        raise ValueError('GRPO readiness must use the same frozen training retrieval bank')
    for name, expected in PINNED.items():
        if importlib.metadata.version(name).split('+')[0] != expected:
            raise RuntimeError('Install existing pinned training requirements before opting in: ' + name)
    for key, value in dict(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                           DO_NOT_TRACK='1', WANDB_DISABLED='true', TOKENIZERS_PARALLELISM='false').items():
        os.environ[key] = value
    previous_handler = signal.signal(signal.SIGALRM, signal.SIG_DFL)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, wall_seconds)
    try:
        output.mkdir(parents=True)
        pins = {key: value for key, value in expected_model_provenance(factory()).items() if key != 'source'}
        run_metadata = dict(source='benchmark_validation_request' if stage == 'validate' else 'benchmark_training', stage=stage, **pins,
            thinking=False, dependency_versions=PINNED, gate=gate, steps=steps,
            completion_tokens=completion_tokens, tool_iterations=tool_iterations,
            agreed_budget_seconds=agreed_budget_seconds, wall_seconds=wall_seconds,
            agreed_budget_eur=agreed_budget_eur, demonstrations=[item['sha256'] for item in verified],
            sft_warm_start_sha256=warm_start['sha256'] if stage in ('validate', 'grpo') else None,
            published=False, downloads=False, status='running')
        (output / 'run.json').write_text(canonical(seal(run_metadata)) + '\n')
        (output / 'status.json').write_text(canonical(dict(stage=stage, status='incomplete')) + '\n')
        (output / 'retrieval.json').write_text(canonical(seal(dict(version='wildfire-frozen-training-cases-v1',
            catalog_sha256=bank.catalog_sha256, demonstrations=[item['sha256'] for item in verified],
            entries=[json.loads(entry) for entry in bank.entries]))) + '\n')
        import torch
        from peft import LoraConfig, PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError('A BF16 CUDA GPU is required; CPU training is disabled')
        set_seed(20261026)
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
            local_files_only=True, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
            local_files_only=True, trust_remote_code=False, dtype=torch.bfloat16, attn_implementation='sdpa')
        model.to('cuda')
        if stage in ('validate', 'grpo'):
            model = PeftModel.from_pretrained(model, str(Path(sft_adapter) / 'adapter'),
                                              is_trainable=stage == 'grpo', local_files_only=True)
        if stage == 'validate':
            model.eval()

            def transport_factory(index):
                set_seed(20261026 + index)
                return _LocalQwenTransport(model, tokenizer, torch, completion_tokens)

            report = assemble_readiness_report(factory, transport_factory,
                adapter_files_sha256=warm_start['adapter_files_sha256'], bank=bank,
                tool_iterations=tool_iterations, audit_directory=output / 'episodes')
            if report.get('source') != 'model_evaluation' or report.get('model_loaded') is not True:
                raise RuntimeError('Only complete actual CUDA inference can produce a readiness report')
            if adapter_files(Path(sft_adapter) / 'adapter') != warm_start['adapter_files_sha256']:
                raise ValueError('SFT adapter files changed during readiness evaluation')
            report.update(sft_summary_sha256=warm_start['sha256'], completion_tokens=completion_tokens,
                          tool_iterations=tool_iterations, dependency_versions=PINNED)
            report_sha256 = checksum(report)
            try:
                accepted = validation_gate(report, factory(), expected_sha256=report_sha256,
                    expected_adapter_files_sha256=warm_start['adapter_files_sha256'])
                readiness_passed, rejection = True, None
            except ValueError as error:
                accepted, readiness_passed, rejection = None, False, str(error)
            (output / 'validation.json').write_text(canonical(report) + '\n')
            (output / 'validation.sha256').write_text(report_sha256 + '\n')
            result = seal(dict(stage='validate', status='complete', model_loaded=True,
                source='model_evaluation', report_sha256=report_sha256, readiness_passed=readiness_passed,
                gate=accepted, rejection=rejection, pins=pins, sft_summary_sha256=warm_start['sha256'],
                optimizer_steps=0, weights_updated=False,
                agreed_budget_seconds=agreed_budget_seconds, wall_seconds=wall_seconds,
                agreed_budget_eur=agreed_budget_eur, published=False))
            (output / 'summary.json').write_text(canonical(result) + '\n')
            (output / 'status.json').write_text(canonical(dict(stage=stage, status='complete')) + '\n')
            return result
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import GRPOConfig, GRPOTrainer, SFTConfig, SFTTrainer
        gradient_evidence = dict(finite_steps=0, nonzero_steps=0)

        class GradientGuard(TrainerCallback):
            def on_pre_optimizer_step(self, args, state, control, model=None, **kwargs):
                gradients = [parameter.grad for parameter in model.parameters() if parameter.requires_grad and parameter.grad is not None]
                if not gradients or not all(torch.isfinite(gradient).all().item() for gradient in gradients):
                    raise RuntimeError('Missing or nonfinite training gradients')
                gradient_evidence['finite_steps'] += 1
                gradient_evidence['nonzero_steps'] += any(torch.count_nonzero(gradient).item() for gradient in gradients)

        lora = LoraConfig(r=8, lora_alpha=16, lora_dropout=0,
            target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj'], task_type='CAUSAL_LM')
        common = dict(output_dir=str(output / 'trainer'), max_steps=steps,
            per_device_train_batch_size=1, gradient_accumulation_steps=2,
            learning_rate=1e-5, bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs=dict(use_reentrant=False),
            report_to='none', save_strategy='no', push_to_hub=False,
            seed=20261026, data_seed=20261026, dataloader_num_workers=0, logging_steps=1)
        if stage == 'sft':
            rows = []
            for demonstration in verified:
                for example in demonstration['examples']:
                    context, call = example['observation'], example['call']
                    tools = model_tools(context)
                    prompt = [dict(role='system', content=instructions()),
                              dict(role='user', content=canonical(dict(observation=context)))]
                    answer = dict(role='assistant', tool_calls=[dict(type='function', function=dict(
                        name=call['name'], arguments=call['arguments']))])
                    prefix = tokenizer.apply_chat_template(prompt, tools=tools, tokenize=False,
                        add_generation_prompt=True, enable_thinking=False)
                    full = tokenizer.apply_chat_template([*prompt, answer], tools=tools, tokenize=False,
                        enable_thinking=False)
                    if not full.startswith(prefix):
                        raise RuntimeError('Tokenizer completion boundary does not match nonthinking prompt')
                    if len(tokenizer.encode(full)) > 8192:
                        raise ValueError('SFT example exceeds 8192 tokens; silent truncation is disabled')
                    rows.append(dict(prompt=prefix, completion=full[len(prefix):]))
            trainer = SFTTrainer(model=model, processing_class=tokenizer,
                args=SFTConfig(**common, max_length=8192, completion_only_loss=True, packing=False),
                train_dataset=Dataset.from_list(rows), peft_config=lora, callbacks=[GradientGuard()])
        else:
            class TrainingEnvironment(ToolRollout):
                def __init__(self):
                    super().__init__(factory, bank=bank)

            rows = [dict(task_id=identifier, prompt=[dict(role='system', content=instructions()),
                dict(role='user', content='Manage the synthetic incident using tools and delivered public observations. '
                     'If generation stops, evaluator finalization adds no tactical decisions.')])
                for identifier in split_ids(factory().catalog, 'train')]
            trainer = GRPOTrainer(model=model, processing_class=tokenizer,
                args=GRPOConfig(**common, num_generations=2, max_completion_length=completion_tokens,
                    max_tool_calling_iterations=tool_iterations, chat_template_kwargs=dict(enable_thinking=False),
                    temperature=.7, top_p=.8, top_k=20, beta=0.0, use_vllm=False,
                    log_completions=False, mask_truncated_completions=True),
                train_dataset=Dataset.from_list(rows), reward_funcs=outcome_reward,
                environment_factory=TrainingEnvironment, callbacks=[GradientGuard()])

        def adapter_digest():
            digest = hashlib.sha256()
            for name, parameter in trainer.model.named_parameters():
                if parameter.requires_grad:
                    digest.update(name.encode())
                    digest.update(parameter.detach().float().cpu().numpy().tobytes())
            return digest.hexdigest()

        before = adapter_digest()
        trained = trainer.train()
        after = adapter_digest()
        if not all(torch.isfinite(parameter).all().item() for parameter in trainer.model.parameters() if parameter.requires_grad):
            raise RuntimeError('Nonfinite trained adapter weights')
        if before == after or not gradient_evidence['finite_steps'] or not gradient_evidence['nonzero_steps']:
            raise RuntimeError('Training failed finite/nonzero gradient or changed-weight acceptance gate')
        trainer.save_model(str(output / 'adapter'))
        tokenizer.save_pretrained(output / 'adapter')
        result = seal(dict(stage=stage, model=MODEL_ID, revision=MODEL_REVISION, thinking=False,
            catalog_sha256=catalog_pin(factory()), gate=gate, gradients=gradient_evidence,
            before_adapter_sha256=before, after_adapter_sha256=after, adapter_changed=True,
            optimizer_steps=trainer.state.global_step, metrics=trained.metrics,
            wall_seconds=wall_seconds, agreed_budget_seconds=agreed_budget_seconds,
            agreed_budget_eur=agreed_budget_eur, pins=pins, dependency_versions=PINNED,
            adapter_files_sha256=adapter_files(output / 'adapter'),
            training_tasks_only=True, frozen_retrieval_cases=len(bank.entries), published=False))
        (output / 'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        (output / 'status.json').write_text(canonical(dict(stage=stage, status='complete')) + '\n')
        return result
    except BaseException as error:
        if output.exists():
            (output / 'status.json').write_text(canonical(dict(stage=stage, status='incomplete',
                error_type=type(error).__name__)) + '\n')
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=('inspect', 'dry-run', 'export-demo', 'import-trajectory', 'verify-demo', 'freeze-bank', 'sft', 'validate', 'grpo'))
    parser.add_argument('--config', required=True, help='JSON with pinned local BenchmarkEnv constructor arguments')
    parser.add_argument('--allow-archived-catalog', action='store_true',
                        help='Explicitly use the retired night-response training recipe; independent spending gates still apply')
    parser.add_argument('--task-id')
    parser.add_argument('--demonstrations', nargs='*', default=[])
    parser.add_argument('--validation-report')
    parser.add_argument('--validation-sha256')
    parser.add_argument('--output')
    parser.add_argument('--enable-gpu', action='store_true')
    parser.add_argument('--agreed-budget-seconds', type=int)
    parser.add_argument('--agreed-budget-eur', type=float)
    parser.add_argument('--wall-seconds', type=int, default=7200)
    parser.add_argument('--steps', type=int, default=2)
    parser.add_argument('--completion-tokens', type=int, default=1536)
    parser.add_argument('--tool-iterations', type=int, default=64)
    parser.add_argument('--sft-adapter')
    parser.add_argument('--policy', choices=('cooperative', 'dispatcher'), default='cooperative')
    parser.add_argument('--trajectory')
    parser.add_argument('--trajectory-sha256')
    args = parser.parse_args(argv)
    if args.command in ('export-demo', 'import-trajectory', 'freeze-bank', 'sft', 'grpo', 'validate'):
        from wildfire_core.catalog_lifecycle import require_archived_opt_in
        try:
            require_archived_opt_in('benchmark_v1', allow_archived=args.allow_archived_catalog)
        except ValueError as error:
            parser.error(str(error))
    factory = make_factory(json.loads(Path(args.config).read_text()))
    demonstrations = [json.loads(Path(path).read_text()) for path in args.demonstrations]
    if args.command == 'inspect':
        result = inspect(factory)
    elif args.command == 'dry-run':
        if not args.task_id:
            parser.error('dry-run requires --task-id from the training split')
        result = dry_run(factory, args.task_id)
    elif args.command == 'verify-demo':
        if not demonstrations:
            parser.error('verify-demo requires --demonstrations')
        result = dict(verified_demonstrations=len([verify_demonstration(factory, item) for item in demonstrations]))
    elif args.command == 'freeze-bank':
        if not args.output or not demonstrations:
            parser.error('freeze-bank requires --demonstrations and a fresh --output JSON path')
        bank = FrozenRetrievalBank.from_demonstrations(factory, demonstrations)
        payload = seal(dict(version='wildfire-frozen-training-cases-v1', catalog_sha256=bank.catalog_sha256,
            demonstrations=[item['sha256'] for item in demonstrations], entries=[json.loads(item) for item in bank.entries]))
        with Path(args.output).open('x') as destination:
            destination.write(canonical(payload) + '\n')
        result = dict(output=args.output, sha256=payload['sha256'], cases=len(bank.entries), readiness=False)
    elif args.command in ('export-demo', 'import-trajectory'):
        if not args.output:
            parser.error('Export requires a fresh --output JSON path')
        if args.command == 'export-demo':
            if not args.task_id:
                parser.error('export-demo requires a training --task-id')
            demonstration = baseline_demonstration(factory, args.task_id, args.policy)
        else:
            if not args.trajectory or not args.trajectory_sha256:
                parser.error('import-trajectory requires --trajectory and --trajectory-sha256')
            demonstration = demonstration_from_trajectory(factory,
                json.loads(Path(args.trajectory).read_text()), expected_sha256=args.trajectory_sha256)
        with Path(args.output).open('x') as destination:
            destination.write(canonical(demonstration) + '\n')
        result = dict(output=args.output, task_id=demonstration['task_id'], sha256=demonstration['sha256'],
                      verified=True, model_loaded=False, provider_calls=0, readiness=False)
    else:
        if not args.output:
            parser.error('Training requires --output')
        if args.command == 'grpo' and (not args.validation_report or not args.validation_sha256):
            parser.error('GRPO requires --validation-report and --validation-sha256 for the SFT adapter')
        if args.command in ('validate', 'grpo') and not args.sft_adapter:
            parser.error('Validation and GRPO require --sft-adapter')
        result = train(factory, stage=args.command, output=args.output,
            validation_report=json.loads(Path(args.validation_report).read_text()) if args.validation_report else None,
            validation_sha256=args.validation_sha256, demonstrations=demonstrations,
            enable_gpu=args.enable_gpu, agreed_budget_seconds=args.agreed_budget_seconds,
            wall_seconds=args.wall_seconds, steps=args.steps, completion_tokens=args.completion_tokens,
            tool_iterations=args.tool_iterations, sft_adapter=args.sft_adapter,
            agreed_budget_eur=args.agreed_budget_eur)
    print(canonical(result))


if __name__ == '__main__':
    main()
