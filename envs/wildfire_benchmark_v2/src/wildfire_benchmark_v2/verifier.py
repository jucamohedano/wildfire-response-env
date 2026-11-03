"""Terminal benchmark verification for a trusted evaluator, never an actor tool.

The manifest is supplied as a JSON-compatible dictionary plus an externally
expected canonical SHA256. Only this package's code is read to bind source hashes;
there are no runtime, policy, actor-tool or source-evidence-file accesses.
The evidence extractor owns execution/accounting eligibility. This verifier
adds frozen anchor, task-domain, initial-world and release-gate binding.
Incomplete runs raise ValueError. Only a separately verified invalid-action
abandonment can receive a policy-failure zero; it has no fabricated Outcome.
"""
import copy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from . import _capture_source

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)

from .evidence import IDENTITY_KEYS, OPERATIONAL_ADVERSE, extract_verified_outcome
from .grading import (
    BURNED_AREA_WEIGHT, EXPECTED_TASK_COUNT, GRADE_VERSION, MAXIMUM_WEAK_ACTIVE_GRADE,
    OBJECTIVE_VERSION, Outcome, POPULATION_WEIGHT, PROTOCOL_VERSION,
    REFERENCE_KIND, aggregate, cost, grade, qualify, weighted_components,
)

MANIFEST_VERSION = 'wildfire-benchmark-anchors-v2'
VERSION = 'wildfire-benchmark-verifier-v2'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_SHARED_PINS = ('implementation_sha256', 'inputs_sha256', 'network_sha256',
                'observation_sha256', 'interface_version')
_SOURCE_FILES = ('__init__.py', 'evidence.py', 'grading.py', 'verifier.py')


def grading_sources() -> dict[str, str]:
    """Bind all four imported modules to their captured and current disk bytes."""
    try:
        root = Path(__file__).parent
        sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _SOURCE_FILES}
    except OSError as error:
        raise ValueError('Grading source files unavailable') from error
    for name in _SOURCE_FILES:
        module_name = __package__ if name == '__init__.py' else __package__ + '.' + name[:-3]
        module = sys.modules.get(module_name)
        if (module is None or getattr(module, '_LOADED_CODE_MATCH', False) is not True
                or getattr(module, '_LOADED_SOURCE_SHA256', None) != sources[name]):
            raise ValueError('Grading loaded code or source differs: ' + module_name)
    return sources


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def _hash(value, label):
    _require(type(value) is str and _HASH.fullmatch(value) is not None, label + ' must be a canonical SHA256')


def _registration(value):
    required = {'name', 'factory', 'configuration', 'source_sha256', 'dependency_sha256',
                'implementation_sha256', 'observation_sha256', 'interface_version'}
    _require(isinstance(value, dict) and required <= set(value), 'Weak active registration is missing required fields')
    name = value['name']
    _require(type(name) is str and bool(name.strip()) and name == name.strip() and name != 'passive',
             'Invalid registered weak active name')
    factory = value['factory']
    _require(type(factory) is str and re.fullmatch(
        r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', factory) is not None,
        'Invalid registered weak active factory')
    configuration = value['configuration']
    _require(isinstance(configuration, dict) and configuration.get('name') == name,
             'Weak active registration configuration must identify its registered name')
    _require(type(value['interface_version']) is str and bool(value['interface_version'].strip()),
             'Registered weak active interface missing')
    for key in ('source_sha256', 'implementation_sha256', 'observation_sha256'):
        _hash(value[key], 'Weak active registration ' + key)
    dependencies = value['dependency_sha256']
    _require(isinstance(dependencies, dict) and 'baselines.py' in dependencies
             and all(type(key) is str and key not in ('', '.', '..') and '/' not in key and '\\' not in key
                     for key in dependencies), 'Weak active registration dependencies must include baselines.py')
    for key, digest in dependencies.items():
        _hash(digest, 'Weak active dependency ' + key)


def _protocol(protocol):
    _require(isinstance(protocol, dict), 'Full benchmark protocol required')
    for key, wanted in (('version', PROTOCOL_VERSION), ('expected_tasks', EXPECTED_TASK_COUNT),
                        ('expected_families', 6), ('tasks_per_family', 4),
                        ('reference_kind', REFERENCE_KIND), ('reference_proven_optimal', False),
                        ('operational_outcomes', 'retain_verified_score_and_report_adverse_flags'),
                        ('training', False), ('publication', False)):
        actual = protocol[key]
        _require(type(actual) is type(wanted) and actual == wanted, 'Unsupported protocol field: ' + key)
    _require(type(protocol['status']) is str and protocol['status'] in ('candidate_not_released', 'released'),
             'Unsupported protocol release status')
    _registration(protocol['weak_active_registration'])
    objective = protocol['objective']
    _require(objective['version'] == OBJECTIVE_VERSION
             and type(objective['population_weight']) in (int, float)
             and objective['population_weight'] == POPULATION_WEIGHT
             and type(objective['burned_area_weight']) in (int, float)
             and objective['burned_area_weight'] == BURNED_AREA_WEIGHT, 'Unsupported benchmark objective')
    rule = protocol['grade']
    _require(rule['version'] == GRADE_VERSION
             and rule['formula'] == 'clip((passive_cost-agent_cost)/(passive_cost-reference_cost),0,1)'
             and type(rule['completion_bonus']) is int and rule['completion_bonus'] == 0
             and rule['retain_unclipped_gain'] is True, 'Unsupported benchmark grade')
    qualification = protocol['qualification']
    for key in ('minimum_exposure_improvement_people', 'minimum_equal_exposure_burn_improvement_cells'):
        _require(type(qualification[key]) is int and qualification[key] == 1,
                 'Unsupported qualification threshold: ' + key)
    _require(type(qualification['maximum_weak_active_grade']) in (int, float)
             and qualification['maximum_weak_active_grade'] == MAXIMUM_WEAK_ACTIVE_GRADE
             and qualification['require_weak_active_reference_for_release'] is True
             and qualification['require_all_expected_tasks_for_official_aggregate'] is True,
             'Unsupported benchmark release gate')
    if 'families' in protocol:
        families = protocol['families']
        _require(isinstance(families, list) and len(families) == 6
                 and all(type(family) is str and family.strip() for family in families)
                 and len(set(families)) == 6, 'Invalid protocol family domain')
    if 'source_observation_contract' in protocol:
        _require(type(protocol['source_observation_contract']) is str
                 and bool(protocol['source_observation_contract'].strip()), 'Invalid protocol observation contract')


def _identity(identity, task):
    _require(isinstance(identity, dict) and set(identity) == set(IDENTITY_KEYS), 'Case identity fields differ')
    _require(identity['task_id'] == task, 'Case identity task_id differs from its manifest key')
    _require(type(identity['seed']) is int and identity['seed'] >= 0, 'Case seed must be a nonnegative integer')
    _require(type(identity['interface_version']) is str and bool(identity['interface_version'].strip()),
             'Case interface_version missing')
    for key in IDENTITY_KEYS[2:-1]:
        _hash(identity[key], 'Case ' + key)


def _outcome(value):
    _require(isinstance(value, dict) and set(value) == set(Outcome.__dataclass_fields__),
             'Anchor outcome must contain exactly the Outcome fields')
    return Outcome(**value)


def _anchor_metadata(metadata, identity, initial_audit, outcome):
    _require(isinstance(metadata, dict), 'Anchor metadata missing')
    _identity(metadata['identity'], identity['task_id'])
    _require(isinstance(metadata, dict) and metadata['identity'] == identity
             and metadata['initial_audit_sha256'] == initial_audit,
             'Anchor metadata identity or initial audit differs')
    _require(metadata['source_execution_verified'] is True and metadata['terminal_counts_verified'] is True,
             'Anchor metadata must establish verified execution and counts')
    for key, wanted in (('cost', cost(outcome)), ('raw_reward', 1.0 - cost(outcome))):
        actual = metadata[key]
        _require(type(actual) in (int, float) and math.isfinite(actual)
                 and math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12), 'Anchor metadata ' + key + ' differs')
    flags = metadata['adverse_flags']
    _require(isinstance(flags, list) and all(type(flag) is str and flag in OPERATIONAL_ADVERSE for flag in flags)
             and len(set(flags)) == len(flags), 'Invalid anchor adverse flags')


class BenchmarkVerifier:
    """Pinned evaluator-only anchors and terminal verification.

    Construction recomputes the 24-task aggregate using reference/passive
    outcomes and optional weak-active outcomes, ignoring any cached release
    booleans. Public properties return detached copies. A candidate run can
    retain a diagnostic grade; it never receives an official benchmark_score.
    """

    def __init__(self, manifest: dict, expected_sha256: str):
        try:
            self._initialize(manifest, expected_sha256)
        except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
            raise ValueError('Missing or malformed anchor manifest: ' + str(error)) from error

    def _initialize(self, manifest, expected_sha256):
        _require(isinstance(manifest, dict), 'Anchor manifest must be a dictionary')
        _hash(expected_sha256, 'Expected manifest identity')
        _hash(manifest['sha256'], 'Manifest identity')
        digest = _sha({key: value for key, value in manifest.items() if key != 'sha256'})
        _require(manifest['sha256'] == expected_sha256 == digest, 'Anchor manifest identity or integrity differs')
        _require(manifest['version'] == MANIFEST_VERSION, 'Unsupported anchor manifest version')
        _hash(manifest['source_report_sha256'], 'Source report identity')
        _require(manifest['grading_sources'] == grading_sources(), 'Grading source identity differs or is incomplete')
        protocol = manifest['protocol']
        _protocol(protocol)
        _hash(manifest['protocol_sha256'], 'Protocol identity')
        _require(manifest['protocol_sha256'] == _sha(protocol), 'Protocol identity differs')
        tasks = manifest['expected_tasks']
        _require(isinstance(tasks, list) and len(tasks) == EXPECTED_TASK_COUNT
                 and all(type(task) is str and task.strip() for task in tasks)
                 and len(set(tasks)) == EXPECTED_TASK_COUNT, 'Expected domain must contain 24 unique task IDs')
        cases = manifest['cases']
        _require(isinstance(cases, dict) and set(cases) == set(tasks), 'Anchor cases must cover exactly the expected tasks')
        families, pins = {}, set()
        passives, references, weak = {}, {}, {}
        for task in tasks:
            case = cases[task]
            _identity(case['identity'], task)
            pins.add(tuple(case['identity'][key] for key in _SHARED_PINS))
            family = case['family']
            _require(type(family) is str and bool(family.strip()), 'Case family missing')
            families[family] = families.get(family, 0) + 1
            _hash(case['initial_audit_sha256'], 'Initial audit identity')
            passives[task] = _outcome(case['passive_outcome'])
            references[task] = _outcome(case['reference_outcome'])
            _require(type(case['reference_policy']) is str and bool(case['reference_policy'].strip()),
                     'Reference policy missing')
            _require(case['reference_kind'] == REFERENCE_KIND and case['reference_proven_optimal'] is False,
                     'Reference must be labeled best observed, not proven optimal')
            sources = case['source_evidence_sha256']
            _require(isinstance(sources, dict) and set(sources) == {'passive', case['reference_policy']},
                     'Source evidence must identify passive and reference executions')
            for name, value in sources.items():
                _hash(value, 'Source evidence ' + name)
            _anchor_metadata(case['passive_metadata'], case['identity'], case['initial_audit_sha256'], passives[task])
            _anchor_metadata(case['reference_metadata'], case['identity'], case['initial_audit_sha256'], references[task])
            weak_fields = {'weak_active_outcome', 'weak_active_metadata',
                           'weak_active_evidence_sha256', 'weak_active_policy'}
            if weak_fields & set(case):
                _require(weak_fields <= set(case), 'Weak active outcome requires verified metadata, evidence hash and policy')
                policy = case['weak_active_policy']
                _require(type(policy) is str and bool(policy.strip()) and policy == policy.strip()
                         and policy not in ('passive', case['reference_policy']),
                         'Weak active policy must be distinct from passive/reference')
                registration = protocol['weak_active_registration']
                _require(policy == registration['name'], 'Weak active policy differs from the frozen registration')
                for key in ('implementation_sha256', 'observation_sha256', 'interface_version'):
                    _require(registration[key] == case['identity'][key],
                             'Weak active registration differs from case identity: ' + key)
                _hash(case['weak_active_evidence_sha256'], 'Weak active evidence identity')
                weak[task] = _outcome(case['weak_active_outcome'])
                _anchor_metadata(case['weak_active_metadata'], case['identity'], case['initial_audit_sha256'], weak[task])
                receipt = case['weak_active_metadata'].get('policy_registration')
                _require(isinstance(receipt, dict) and receipt == registration,
                         'Weak active policy registration receipt differs or is missing')
                receipt_sha = case['weak_active_metadata'].get('policy_registration_sha256')
                _hash(receipt_sha, 'Weak active policy registration receipt')
                _require(receipt_sha == _sha(receipt) == _sha(registration),
                         'Weak active policy registration receipt hash differs')
                orders = case['weak_active_metadata'].get('operational_order_count')
                _require(type(orders) is int and orders > 0,
                         'Weak active metadata requires a positive integer operational_order_count')
        _require(len(families) == 6 and set(families.values()) == {4}, 'Expected four tasks in each of six families')
        if 'families' in protocol:
            _require(set(families) == set(protocol['families']), 'Case families differ from protocol')
        _require(len(pins) == 1, 'Cases must share core/input/network/observation/interface pins')
        if 'source_observation_contract' in protocol:
            _require(all(case['identity']['interface_version'] == protocol['source_observation_contract']
                         for case in cases.values()), 'Case interface differs from protocol observation contract')
        self._gate = aggregate(references, passives, references, expected_tasks=tasks, weak_active=weak,
                               protocol_released=protocol['status'] == 'released')
        self._manifest = copy.deepcopy(manifest)
        self._manifest_sha256 = digest

    @property
    def manifest_sha256(self):
        return self._manifest_sha256

    @property
    def protocol_sha256(self):
        return self._manifest['protocol_sha256']

    @property
    def release_ready(self):
        return self.qualification_ready and self._manifest['protocol']['status'] == 'released'

    @property
    def qualification_ready(self):
        """Numeric qualification only; this grants no release/publication authority."""
        return self._gate['qualification_ready']

    @property
    def release_gate(self):
        gate = copy.deepcopy(self._gate)
        gate['qualification_ready'] = self.qualification_ready
        gate['protocol_status'] = self._manifest['protocol']['status']
        gate['release_ready'] = self.release_ready
        if not self.release_ready:
            gate['official_mean'] = None
        return gate

    def _policy_failure(self, task_id, case, failure, allow_candidate):
        qualification = qualify(case['passive_outcome'], case['reference_outcome'])
        eligible = qualification['qualified']
        return dict(version=VERSION, task_id=task_id, manifest_sha256=self._manifest_sha256,
            protocol_sha256=self._manifest['protocol_sha256'], source_report_sha256=self._manifest['source_report_sha256'],
            released=self.release_ready, candidate=not self.release_ready,
            qualification_ready=self.qualification_ready, protocol_status=self._manifest['protocol']['status'], qualified=eligible,
            qualification_reason=qualification['reason'],
            benchmark_score=0.0 if eligible and self.release_ready else None,
            diagnostic_grade=0.0 if eligible and not self.release_ready and allow_candidate else None,
            unscored_reason=None if eligible and self.release_ready else
                (qualification['reason'] if not eligible else 'benchmark_not_released'),
            raw_reward_diagnostic=0.0, raw_gain=None, reference_exceeded=None,
            cost_gap=qualification['cost_gap'], cost_improvement=None,
            costs=dict(agent=None, passive=qualification['passive_cost'], reference=qualification['reference_cost']),
            weighted_components=dict(agent=None, passive=weighted_components(case['passive_outcome']),
                                     reference=weighted_components(case['reference_outcome'])),
            outcome=None, policy_failure=copy.deepcopy(failure),
            reference_policy=case['reference_policy'], reference_kind=REFERENCE_KIND,
            reference_proven_optimal=False, adverse_flags=[], operational_status='policy_failure', operational_evidence=[],
            identity=copy.deepcopy(case['identity']), initial_audit_sha256=case['initial_audit_sha256'])

    def verify_run(self, task_id: str, result: dict, saved: dict, *, allow_candidate: bool = False) -> dict:
        if type(task_id) is not str or task_id not in self._manifest['cases']:
            raise ValueError('Task is not in the frozen benchmark domain')
        if type(allow_candidate) is not bool:
            raise ValueError('allow_candidate must be bool')
        _require(self._manifest['grading_sources'] == grading_sources(), 'Grading source identity changed')
        case = self._manifest['cases'][task_id]
        expected = dict(case['identity'], initial_audit_sha256=case['initial_audit_sha256'])
        # The helper returns None absent an explicit abandonment claim. A crash
        # still reaches the fail-closed terminal path, never a policy-failure zero.
        from .evidence import extract_verified_policy_failure
        failure = extract_verified_policy_failure(result, saved, expected)
        if failure is not None:
            _require(isinstance(failure, dict) and failure.get('version') == 'wildfire-verified-policy-failure-v1'
                     and failure.get('failure_kind') == 'invalid_action_abandonment'
                     and failure.get('verified_policy_failure') is True
                     and failure.get('source_execution_verified') is True
                     and failure.get('identity') == case['identity']
                     and failure.get('initial_audit_sha256') == case['initial_audit_sha256']
                     and type(failure.get('raw_reward')) in (int, float) and failure['raw_reward'] == 0.0,
                     'Claimed policy failure lacks verified abandonment evidence')
            _require(isinstance(saved.get('audit'), list) and saved['audit']
                     and _sha(saved['audit'][0]) == case['initial_audit_sha256'], 'Initial audit identity differs')
            return self._policy_failure(task_id, case, failure, allow_candidate)
        outcome, metadata = extract_verified_outcome(result, saved, expected)
        graded = grade(outcome, case['passive_outcome'], case['reference_outcome'], adverse_flags=metadata['adverse_flags'])
        return dict(version=VERSION, task_id=task_id, manifest_sha256=self._manifest_sha256,
            protocol_sha256=self._manifest['protocol_sha256'], source_report_sha256=self._manifest['source_report_sha256'],
            released=self.release_ready, qualification_ready=self.qualification_ready,
            protocol_status=self._manifest['protocol']['status'],
            candidate=not self.release_ready, qualified=graded['qualified'],
            qualification_reason=graded['qualification_reason'],
            benchmark_score=graded['grade'] if self.release_ready else None,
            diagnostic_grade=graded['grade'] if not self.release_ready and allow_candidate else None,
            unscored_reason=None if self.release_ready else
                (graded['qualification_reason'] if not graded['qualified'] else 'benchmark_not_released'),
            raw_reward_diagnostic=metadata['raw_reward'],
            raw_gain=graded['raw_gain'] if self.release_ready or allow_candidate else None,
            reference_exceeded=graded['reference_exceeded'] if self.release_ready or allow_candidate else None,
            cost_gap=graded['cost_gap'], cost_improvement=graded['cost_improvement'], costs=graded['costs'],
            weighted_components=graded['weighted_components'], outcome=asdict(outcome), policy_failure=None,
            reference_policy=case['reference_policy'], reference_kind=graded['reference_kind'],
            reference_proven_optimal=False, adverse_flags=metadata['adverse_flags'],
            operational_status=metadata['operational_status'], operational_evidence=metadata['operational_evidence'],
            identity=metadata['identity'], initial_audit_sha256=metadata['initial_audit_sha256'])
