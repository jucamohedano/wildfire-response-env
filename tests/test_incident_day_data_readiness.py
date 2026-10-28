import copy
import hashlib
import json

import pytest

from eval import check_incident_day_data_readiness as readiness
from envs.wildfire_response.incident_day.data import digest, load_inputs, prepare_inputs


def rehash(document):
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})


@pytest.fixture
def unapproved_pack():
    fixture = json.loads((readiness.Path(__file__).resolve().parents[1] /
                          'data/incident_day_v2/synthetic_fixture.json').read_text())
    prepared, domain, network = (copy.deepcopy(fixture[name]) for name in ('prepared', 'domain', 'network'))
    prepared['fuel_classes'][0][0] = 0
    prepared['fuel_valid_mask'][0][0] = 0
    rehash(prepared)
    domain['prepared_inputs_sha256'] = prepared['sha256']
    rehash(domain)
    return prepare_inputs(prepared, domain, network, fuel_resolution={'kind': 'synthetic', 'class': 4})


def test_source_document_requires_caller_pin_and_rejects_tampering(tmp_path):
    document = {'value': 'original'}
    rehash(document)
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(document))
    assert readiness.read_pinned(path, document['sha256']) == document
    document['value'] = 'altered'
    rehash(document)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='caller pin'):
        readiness.read_pinned(path, '0' * 64)
    with pytest.raises(ValueError, match='caller pin'):
        readiness.read_pinned(path, digest({'value': 'original'}))


def test_measured_report_requires_raw_byte_hash(tmp_path):
    path = tmp_path / 'report.json'
    path.write_text('{"status": "complete"}\n')
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    assert readiness.read_run_report(path, original)['status'] == 'complete'
    path.write_text('{"status":"complete"}\n')
    with pytest.raises(ValueError, match='raw-file pin'):
        readiness.read_run_report(path, original)


def test_audit_rejects_changed_source_mask_before_route_checks(unapproved_pack):
    documents = {name: {'value': name} for name in ('prepared', 'domain', 'network', 'estimates')}
    for document in documents.values():
        rehash(document)
    documents['pack'] = copy.deepcopy(unapproved_pack)
    pins = {name: document['sha256'] for name, document in documents.items()}
    documents['pack']['fuel_known_mask'][0][0] = 1
    rehash(documents['pack'])
    with pytest.raises(ValueError, match='caller pin: pack'):
        readiness.audit(**documents, pins=pins, routes=False)


def test_unapproved_pack_and_unresolved_land_guards_fail_closed(unapproved_pack, monkeypatch):
    estimates = {'cells': [dict(row=0, column=0, land_kind='unresolved')]}
    with pytest.raises(ValueError, match='unapproved'):
        load_inputs(unapproved_pack, unapproved_pack['sha256'])
    assert readiness.verify_guards(unapproved_pack, unapproved_pack['sha256'], estimates) == {(0, 0)}
    monkeypatch.setattr(readiness.IncidentDayEnv, '_land_guard', lambda self: False)
    with pytest.raises(ValueError, match='guard did not stop'):
        readiness.verify_guards(unapproved_pack, unapproved_pack['sha256'], estimates)


def test_guard_audit_rejects_unexpected_default_acceptance(unapproved_pack, monkeypatch):
    monkeypatch.setattr(readiness, 'load_inputs', lambda *args, **kwargs: unapproved_pack)
    with pytest.raises(ValueError, match='unexpectedly accepts'):
        readiness.verify_guards(unapproved_pack, unapproved_pack['sha256'], {'cells': []})


def test_candidate_crop_reports_unsupported_dimensions_and_never_masks_unknowns():
    grid = dict(height=36, width=37)
    mask = [[1] * 37 for _ in range(36)]
    mask[16][0] = 0
    mask[17][1] = 0
    pack = dict(grid=grid, fuel_known_mask=mask, study_mask=[[1] * 37 for _ in range(36)],
                heights=[[1.] * 37 for _ in range(36)])
    estimates = {'cells': [dict(row=16, column=0, land_kind='unresolved', land_category='unknown'),
                           dict(row=17, column=1, land_kind='vegetation', land_category='shrub')]}
    result = readiness.crop_summary(pack, estimates, readiness.CANDIDATE_CROP)
    assert result['cells'] == 520 and result['source_known_cells'] == 518
    assert result['estimated_cells'] == 2 and result['unresolved_land_water_cells'] == 1
    assert result['current_large_fire_dimensions_supported'] is False
    with pytest.raises(ValueError, match='bounded rectangle'):
        readiness.crop_summary(pack, estimates, dict(top=16, left=0, width=38, height=20))


def test_measured_report_requires_its_own_core_and_input_pins_and_omits_private_state():
    report = dict(version='incident-day-plausibility-report-v2', input_sha256='pack',
        network_sha256='network', implementation_sha256='old-core', implementation_unchanged=True,
        status='complete', policy='dispatcher', workers=1, allow_unapproved_data=True,
        input_approval_status='unapproved_source_pack', land_mask_supplied=False,
        gates=dict(exercise_consistent='not established', physically_validated='no',
                   contradiction_count=0, blocking_count=0, human_review='pending',
                   intended_constraint_coverage={'case': 'no_constraint_evidence_observed'}),
        static_cases=[dict(case='case', initial_route_options=[dict(status='unmapped')],
                           counterfactual=dict(status='not_demonstrated'))],
        dynamic_cases=[dict(case='case', status='completed', complete=True, minutes_observed=1440,
                            verification=dict(exact_replay=True, independent_reward_agreement=True),
                            outcome=dict(boundary_censored=False, unresolved_evacuations=1),
                            applied_work_counts={'engine-0': 0, 'plane-0': 4},
                            resource_timeline={'private': 'not for summary'})])
    arguments = dict(report_sha256='report', pack_sha256='pack', network_sha256='network',
                     implementation_sha256='old-core')
    summary = readiness.summarize_run(report, **arguments)
    assert summary['implementation_sha256'] == 'old-core'
    assert summary['cases'][0]['applied_ground_work'] == 0
    assert summary['cases'][0]['unresolved_evacuations'] == 1
    assert 'resource_timeline' not in summary['cases'][0]
    with pytest.raises(ValueError, match='identity'):
        readiness.summarize_run(report, **dict(arguments, implementation_sha256='different-core'))
    with pytest.raises(ValueError, match='identity'):
        readiness.summarize_run(report, **dict(arguments, pack_sha256='different-pack'))
