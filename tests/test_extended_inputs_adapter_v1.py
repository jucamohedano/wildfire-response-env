"""Synthetic protocol tests; this is not incident-day integration coverage."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_containment_v1/src'))
from wildfire_containment_v1 import ContainmentKernel
from envs.wildfire_extended_inputs_v1 import VERSION, canonical_sha256
from envs.wildfire_extended_inputs_v1.adapter_v1 import (
    CensoredContainmentSeamV1, CensoredError, ExtendedContainmentBackendV1,
)


@pytest.fixture
def doc():
    n = 7
    return dict(version=VERSION,
                grid=dict(width=n, height=n, crs='synthetic', transform=[1, 0, 0, 0, -1, 7]),
                domain_mask=[[True] * n for _ in range(n)],
                surface=[['land'] * n for _ in range(n)],
                fuel=[[4] * n for _ in range(n)],
                elevation=[[100.] * n for _ in range(n)],
                access=[['unknown'] * n for _ in range(n)],
                source_pins={name: char * 64 for name, char in
                             [('surface', 'a'), ('fuel', 'b'), ('elevation', 'c'), ('access', 'd')]})


class SyntheticBackend(ExtendedContainmentBackendV1):
    """Tiny synthetic one-step stencil using the actual containment ledger."""
    def __init__(self, inputs):
        self.inputs = inputs
        self.fire = []
        self.rng = 17
        self.minute = 0
        self.kernel = ContainmentKernel(work_gain=2, max_strength=5, decay=.5,
                                        wind_factor=1, report_delay=2)

    @property
    def identity(self):
        return {'version': 'synthetic-backend-1'}

    def burning(self):
        return copy.deepcopy(self.fire)

    def change(self, command):
        self.fire = copy.deepcopy(command['fire'])
        if command.get('fail'):
            raise RuntimeError('partial mutation')

    def step(self, command):
        target = command['target']
        assert any(max(abs(x - target[0]), abs(y - target[1])) == 1 for x, y in self.fire)
        self.minute += 1
        self.rng = (self.rng * 13 + 7) % 101
        self.kernel.tick(self.minute, assignments=[], attacks=[], wind=[0, 0], suppressing=[])
        x, y = target
        if self.inputs.surface[y][x] == 'sea':
            return
        assert self.inputs.fire_support[y][x]
        if target not in self.fire and self.rng % 2 == 0:
            self.fire.append(target[:])

    def snapshot(self):
        return dict(fire=copy.deepcopy(self.fire), rng=self.rng, minute=self.minute,
                    kernel=self.kernel.checkpoint())

    def restore(self, snapshot):
        self.fire = copy.deepcopy(snapshot['fire'])
        self.rng = snapshot['rng']
        self.minute = snapshot['minute']
        self.kernel = ContainmentKernel.restore(snapshot['kernel'])

    def outcomes(self):
        return {'minute': self.minute, 'fire': copy.deepcopy(self.fire)}


def seam(doc, **kwargs):
    return CensoredContainmentSeamV1(doc, canonical_sha256(doc),
        expected_source_pins=doc['source_pins'], backend_factory=kwargs.pop('factory', SyntheticBackend),
        expected_backend_identity={'version': 'synthetic-backend-1'}, opt_in=True, **kwargs)


def test_sea_is_supported_nonburnable_without_fabricated_land_values(doc):
    doc['surface'][3][4] = 'sea'
    doc['fuel'][3][4] = None
    doc['elevation'][3][4] = None
    driver = seam(doc)
    driver.change({'fire': [[3, 3]]})
    driver.step({'target': [4, 3]})
    assert driver.outcomes() == {'minute': 1, 'fire': [[3, 3]]}
    assert driver._backend.inputs.fuel[3][4] is None
    assert driver._backend.inputs.elevation[3][4] is None
    with pytest.raises(CensoredError, match='after'):
        driver.change({'fire': [[4, 3]]})
    assert driver.reasons == ('invalid_burning_cell',)
    with pytest.raises(CensoredError, match='outcomes'):
        driver.outcomes()


@pytest.mark.parametrize('layer,value', [('surface', 'unknown'), ('surface', 'mixed'),
                                          ('fuel', None), ('elevation', None),
                                          ('domain_mask', False)])
def test_unknown_censors_before_spread_and_stays_sticky(doc, layer, value):
    doc[layer][4][4] = value
    driver = seam(doc)
    with pytest.raises(CensoredError, match='after'):
        driver.change({'fire': [[3, 3]]})
    checkpoint = driver.checkpoint()
    assert checkpoint['state']['minute'] == 0
    assert driver.reasons == ('unknown_fire_state',)
    with pytest.raises(CensoredError, match='before'):
        driver.step({'target': [3, 4]})
    restored = seam(doc)
    restored.restore(json.loads(json.dumps(checkpoint)))
    assert restored.censored and restored.reasons == driver.reasons
    with pytest.raises(CensoredError):
        restored.change({'fire': []})


def test_post_step_boundary_censors_and_suppresses_outcome(doc):
    driver = seam(doc)
    driver.change({'fire': [[1, 3]]})
    # The first synthetic draw is even: the step ignites the boundary.
    with pytest.raises(CensoredError, match='after'):
        driver.step({'target': [0, 3]})
    assert 'grid_boundary' in driver.reasons
    assert driver.checkpoint()['state']['minute'] == 1
    with pytest.raises(CensoredError):
        driver.outcomes()


def test_post_step_detects_newly_reached_unknown_neighbour(doc):
    doc['fuel'][3][3] = None
    driver = seam(doc)
    driver.change({'fire': [[1, 3]]})
    with pytest.raises(CensoredError, match='after'):
        driver.step({'target': [2, 3]})
    assert driver.reasons == ('unknown_fire_state',)


def test_pre_step_checks_current_state_before_any_step(doc):
    doc['fuel'][4][4] = None
    driver = seam(doc)
    # Simulate an out-of-band state change to verify the defensive pre-check.
    driver._backend.fire = [[3, 3]]
    with pytest.raises(CensoredError, match='before'):
        driver.step({'target': [3, 4]})
    assert driver.checkpoint()['state']['minute'] == 0


def test_exact_joint_checkpoint_and_deterministic_continuation(doc):
    a = seam(doc)
    a.change({'fire': [[3, 3]]})
    a.step({'target': [4, 3]})
    checkpoint = json.loads(json.dumps(a.checkpoint()))
    b = seam(doc)
    b.restore(checkpoint)
    assert b.checkpoint() == checkpoint
    for target in ([3, 4], [2, 3], [3, 2]):
        a.step({'target': target})
        b.step({'target': target})
        assert a.checkpoint() == b.checkpoint()
        assert a.outcomes() == b.outcomes()


def test_identity_tamper_restore_and_partial_failure_fail_closed(doc):
    a = seam(doc)
    a.change({'fire': [[3, 3]]})
    prior = a.checkpoint()
    tampered = copy.deepcopy(prior)
    tampered['state']['rng'] += 1
    with pytest.raises(ValueError, match='integrity'):
        a.restore(tampered)
    with pytest.raises(RuntimeError, match='indeterminate'):
        a.outcomes()
    a.restore(prior)
    with pytest.raises(RuntimeError, match='partial mutation'):
        a.change({'fire': [[0, 0]], 'fail': True})
    with pytest.raises(RuntimeError, match='indeterminate'):
        a.step({'target': [3, 4]})
    a.restore(prior)
    assert a.checkpoint() == prior


def test_restore_requires_exact_backend_snapshot(doc):
    class BrokenRestore(SyntheticBackend):
        def restore(self, snapshot):
            super().restore(snapshot)
            self.rng += 1

    a = seam(doc, factory=BrokenRestore)
    checkpoint = a.checkpoint()
    with pytest.raises(ValueError, match='exact snapshot'):
        a.restore(checkpoint)
    with pytest.raises(RuntimeError, match='indeterminate'):
        a.outcomes()


def test_requires_reviewed_backend_identity_and_empty_start(doc):
    with pytest.raises(TypeError, match='backend'):
        seam(doc, factory=lambda _: object())
    def already_burning(inputs):
        backend = SyntheticBackend(inputs)
        backend.fire = [[3, 3]]
        return backend
    with pytest.raises(ValueError, match='without burning'):
        seam(doc, factory=already_burning)
    with pytest.raises(ValueError, match='identity'):
        CensoredContainmentSeamV1(doc, canonical_sha256(doc), expected_source_pins=doc['source_pins'],
            backend_factory=SyntheticBackend, expected_backend_identity={'version': 'other'}, opt_in=True)


def test_top_level_seam_import_coexists_with_core_package():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((
        str(root / 'envs/wildfire_extended_inputs_v1'),
        str(root / 'envs/wildfire_response/core/src'),
        str(root / 'envs/wildfire_containment_v1/src'))), PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable, '-c',
        'import wildfire_core.incident_day, wildfire_containment_v1.incident_day, adapter_v1; '
        'assert adapter_v1.VERSION == "wildfire-extended-containment-seam-v1"'],
        cwd=root, env=env, check=True, capture_output=True, text=True)
