"""Independent end-to-end checks for deterministic local session recovery.

The child deliberately dies rather than raising an exception.  The oracle uses
v3 control and the existing policy/observer directly, without the session driver.
These tests do not imply recovery of remote model calls or learner delivery.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import make_policy
from eval.session_recovery_v1 import SessionRecoveryError, SessionRecoveryV1
from wildfire_control_v3.control import create_population_control, interface_digest
from wildfire_control_v3.verifier_v3 import IDENTITY_FIELDS
from wildfire_core.incident_day import fixture_inputs


CASE = 'incident-day-egress-v1-000'
SEED = 3866400
RUN = 'integration-episode-unique'
POLICY = 'integration-custom-conservative'
SECRET = 'private-evaluation-canary-9b6cde'
EXIT = 73


def control():
    inputs, network = fixture_inputs()
    c = create_population_control(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                  expected_network_sha256=network['sha256'])
    c.reset(CASE, seed=SEED)
    return c


def evaluate(state, action, result):
    state['actions'].append(copy.deepcopy(action))
    state['minutes'].append(result['observation']['minute'])


class DeterministicPolicy:
    """Custom local policy with decision state in addition to the built-in state."""
    def __init__(self):
        self.inner = make_policy('conservative')
        self.decisions = 0

    def action(self, public, observation):
        self.decisions += 1
        return self.inner.action(public, observation)

    def checkpoint(self):
        return {'version': 1, 'decisions': self.decisions, 'inner': self.inner.checkpoint()}

    def restore(self, state):
        if (set(state) != {'version', 'decisions', 'inner'} or state['version'] != 1
                or type(state['decisions']) is not int or state['decisions'] < 0):
            raise ValueError('invalid policy state')
        self.inner.restore(copy.deepcopy(state['inner']))
        self.decisions = state['decisions']


def driver(directory):
    return SessionRecoveryV1(control(), directory, run_identity=RUN, policy_name=POLICY,
                             evaluation={'secret': SECRET, 'actions': [], 'minutes': []},
                             evaluator=evaluate, evaluator_identity='integration-evaluator-v1',
                             policy_factory=DeterministicPolicy, policy_identity='custom-conservative-v1')


def json_copy(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


class OraclePublic:
    """Independent public-tool adapter and private evidence ledger."""
    def __init__(self, control, calls):
        self.control, self.calls = control, calls

    def execute(self, name, arguments):
        env = self.control.environment
        minute, decision = env.minute, env.decision
        result = self.control.execute(name, copy.deepcopy(arguments))
        retained = {k: copy.deepcopy(result[k]) for k in
                    ('info', 'reward', 'terminated', 'truncated', 'error', 'committed', 'valid')
                    if isinstance(result, dict) and k in result}
        self.calls.append(dict(name=name, arguments=copy.deepcopy(arguments), before_minute=minute,
                               after_minute=env.minute, before_decision=decision,
                               after_decision=env.decision, result=retained))
        return result

    def get_situation(self):
        return self.execute('get_situation', {})

    def get_reports(self, **arguments):
        return self.execute('get_reports', arguments)

    def route_options(self, resource_id, sector_id='incident'):
        return self.execute('route_options', {'resource_id': resource_id, 'sector_id': sector_id})

    def evacuation_options(self, group_id, destination_id):
        return self.execute('evacuation_options', {'group_id': group_id, 'destination_id': destination_id})

    def check_action(self, action):
        return self.execute('check_action', {'action': action})


class Oracle:
    def __init__(self):
        self.control = control()
        self.observation = self.control.environment.get_situation()
        self.observer = MovementObserver(self.control.environment)
        self.policy = DeterministicPolicy()
        self.calls = []
        self.public = OraclePublic(self.control, self.calls)
        self.evaluation = {'secret': SECRET, 'actions': [], 'minutes': []}
        trajectory = self.control.environment.trajectory()
        # Established before either run, rather than supplied by the run under test.
        self.identity = {key: trajectory[key] for key in IDENTITY_FIELDS}
        self.interface_pin = interface_digest()

    def step(self):
        action = self.policy.action(self.public, copy.deepcopy(self.observation))
        if set(action) == {'advance_minutes'}:
            result = self.public.execute('advance', {'max_minutes': action['advance_minutes']})
        elif set(action) == {'status_requests'} and len(action['status_requests']) == 1:
            result = self.public.execute('request_status', action['status_requests'][0])
        else:
            result = self.public.execute('commit_orders', {'action': action})
        assert isinstance(result.get('observation'), dict)
        self.observation = copy.deepcopy(result['observation'])
        evaluate(self.evaluation, action, result)
        return json_copy(result)

    def snapshot(self):
        return json_copy(dict(control=self.control.private_checkpoint(), policy=self.policy.checkpoint(),
                              observation=self.observation, observer=self.observer.snapshot(),
                              calls=self.calls, evaluation=self.evaluation))


def session_snapshot(session):
    # Inspect the individual components rather than use the driver's snapshot helper.
    return json_copy(dict(control=session.control.private_checkpoint(), policy=session.policy.checkpoint(),
                          observation=session.observation, observer=session.observer.snapshot(),
                          calls=session.calls, evaluation=session.evaluation))


def assert_public(value):
    encoded = json.dumps(value, sort_keys=True)
    assert SECRET not in encoded
    for name in ('engine_checkpoint', 'last_failed_check', 'option_snapshots',
                 'run_identity', 'evaluation', 'observer', '_audit', 'future_events'):
        assert '"' + name + '"' not in encoded


def no_network(*args, **kwargs):
    raise AssertionError('local recovery attempted external network access')


def state_digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def make_reference(limit):
    """Drive an independent control for a short prefix or an entire day."""
    oracle = Oracle()
    initial = oracle.snapshot()
    results, states = [], []
    for _ in range(1999 if limit is None else limit):
        if oracle.control.environment.terminated or oracle.control.environment.truncated:
            break
        results.append(oracle.step())
        states.append(state_digest(oracle.snapshot()))
    else:
        if limit is None:
            pytest.fail('reference episode did not reach a terminal state')
    actions = oracle.evaluation['actions']
    assert any(set(a) == {'advance_minutes'} for a in actions)
    assert any(set(a) != {'advance_minutes'} for a in actions)
    assert oracle.policy.checkpoint()['inner']['status_sent'] is True
    assert oracle.policy.checkpoint()['decisions'] == len(actions)
    final_state = oracle.snapshot()
    outcome = None
    if limit is None:
        assert oracle.control.environment.terminated is True
        assert oracle.control.environment.truncated is False
        assert oracle.control.environment.minute == 1440
        outcome = oracle.control.finalize(expected_identity=oracle.identity,
                                          expected_interface_sha256=oracle.interface_pin)
        assert outcome['status'] == 'full_day' and outcome['verified'] is True
    return dict(initial=initial, results=results, states=states, final=final_state,
                identity=oracle.identity, interface_pin=oracle.interface_pin, outcome=outcome)


@pytest.fixture(scope='module')
def reference():
    # The default CI cases compare all components through several post-crash calls.
    return make_reference(8)


def progress_to_end(reference, session, start):
    for sequence in range(start, len(reference['results']) + 1):
        actual = session.step(sequence)
        assert actual == reference['results'][sequence - 1]
        assert_public(actual)
        assert state_digest(session_snapshot(session)) == reference['states'][sequence - 1]


def assert_complete(reference, session):
    assert session.control.environment.terminated is True
    assert session.control.environment.truncated is False
    assert session.control.environment.minute == 1440
    assert session.observer.snapshot()['coverage']['ticks_observed'] == 1440
    assert session_snapshot(session) == reference['final']
    actual = session.control.finalize(expected_identity=reference['identity'],
                                      expected_interface_sha256=reference['interface_pin'])
    assert actual == reference['outcome']
    assert_public(actual)


@pytest.fixture
def private(tmp_path):
    path = tmp_path / 'private'
    path.mkdir(mode=0o700)
    return path


def test_corrupt_private_journal_has_no_public_error_or_output_leak(private, capsys):
    session = driver(private)
    session.initialize()
    (private / 'session-recovery-v1.json').write_text(SECRET)
    fresh = driver(private)
    before = session_snapshot(fresh)
    with pytest.raises(SessionRecoveryError) as error:
        fresh.recover()
    assert SECRET not in str(error.value)
    assert session_snapshot(fresh) == before
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err


# Re-import this test module in a child to use the exact same pinned evaluator.
# Capture no private checkpoint or traceback in child output.
CHILD = r'''
import importlib.util, os, socket, sys
spec = importlib.util.spec_from_file_location('integration_test', sys.argv[1])
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
socket.socket.connect = t.no_network
socket.create_connection = t.no_network
session = t.driver(sys.argv[2]); session.initialize()
sequence = int(sys.argv[3]); phase = sys.argv[4]
for n in range(1, sequence): session.step(n)
write = session._write
execute = session.control.execute

def crash_write(directory, record):
    write(directory, record)
    if record['sequence'] == sequence and record['phase'] == phase:
        os._exit(t.EXIT)

def crash_execute(name, arguments):
    if phase == 'during_tick' and name == 'advance':
        env = session.control.environment
        tick = env._tick
        def interrupted_tick():
            tick()
            with open(os.path.join(sys.argv[2], 'tick.marker'), 'w') as f:
                f.write(str(env.minute)); f.flush(); os.fsync(f.fileno())
            os._exit(t.EXIT)
        env._tick = interrupted_tick
    result = execute(name, arguments)
    if phase == 'after_execute' and name in ('advance', 'commit_orders', 'request_status'):
        os._exit(t.EXIT)
    return result

session._write = crash_write
session.control.execute = crash_execute
session.step(sequence)
raise RuntimeError('crash point was not reached')
'''


def exercise_crash(private, reference, sequence, action_kind, phase):
    expected = reference['results'][sequence - 1]
    action = reference['final']['evaluation']['actions'][sequence - 1]
    assert (set(action) == {'advance_minutes'}) == (action_kind == 'wait')
    child = subprocess.run([sys.executable, '-c', CHILD, str(Path(__file__).resolve()),
                            str(private), str(sequence), phase], env=os.environ.copy(),
                           capture_output=True, text=True, timeout=120)
    assert child.returncode == EXIT, 'child did not reach the required crash point'
    assert child.stdout == '' and child.stderr == ''
    journal = json.loads((private / 'session-recovery-v1.json').read_text())['record']
    assert journal['sequence'] == sequence
    assert journal['phase'] == ('complete' if phase == 'complete' else 'selected')
    assert journal['action'] == action
    if phase == 'during_tick':
        assert int((private / 'tick.marker').read_text()) == journal['state']['observation']['minute'] + 1
    resumed = driver(private)
    old_environment = resumed.control.environment
    result = resumed.recover()
    assert resumed.control.environment is not old_environment
    assert resumed.observer.env is resumed.control.environment
    assert resumed.observer.ops is resumed.control.environment.ops
    assert result == expected
    assert_public(result)
    assert state_digest(session_snapshot(resumed)) == reference['states'][sequence - 1]
    assert resumed.step(sequence) == result  # Redelivery does not repeat the action or evidence.
    assert state_digest(session_snapshot(resumed)) == reference['states'][sequence - 1]
    return resumed


@pytest.mark.parametrize('sequence,action_kind,phase', [
    (sequence, kind, phase)
    for sequence, kind in [(1, 'non-wait'), (4, 'wait')]
    for phase in ['selected', 'after_execute', 'complete']
] + [(4, 'wait', 'during_tick')])
def test_process_death_recovery_and_continuation(private, monkeypatch, reference, sequence, action_kind, phase):
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    resumed = exercise_crash(private, reference, sequence, action_kind, phase)
    recovered_ticks = resumed.observer.snapshot()['coverage'].get('ticks_observed', 0)
    progress_to_end(reference, resumed, sequence + 1)
    assert resumed.observer.snapshot()['coverage']['ticks_observed'] > recovered_ticks
    assert session_snapshot(resumed) == reference['final']
    assert (private / 'session-recovery-v1.json').stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.environ.get('WILDFIRE_FULL_DAY_INTEGRATION') != '1',
                    reason='set WILDFIRE_FULL_DAY_INTEGRATION=1 for the separate full-day integration')
def test_full_day_recovery_matches_independent_episode(private, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    reference = make_reference(None)
    resumed = exercise_crash(private, reference, 4, 'wait', 'during_tick')
    recovered_ticks = resumed.observer.snapshot()['coverage'].get('ticks_observed', 0)
    progress_to_end(reference, resumed, 5)
    assert resumed.observer.snapshot()['coverage']['ticks_observed'] > recovered_ticks
    assert_complete(reference, resumed)
