#!/usr/bin/env python3
"""Build an offline fuel-estimate document and a spatial holdout diagnostic."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import _check_hash, _crop
from envs.wildfire_response.incident_day.fuel_estimation import DOMAIN_PIN, PREPARED_PIN, _features, _category, _vote, build_estimates
from audit_incident_day_fuels import verify_siose_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    prepared = json.loads((arguments.runtime / 'malaga-prepared-simulation-v1/malaga-prepared-inputs-v1/inputs.json').read_text())
    domain = json.loads((arguments.runtime / 'topographic-experiment-v1/topographic-inputs-v1/inputs.json').read_text())
    if _check_hash(prepared, 'prepared') != PREPARED_PIN or _check_hash(domain, 'domain') != DOMAIN_PIN:
        raise ValueError('Unexpected pinned packs')
    probe_path = ROOT / 'data/incident_day_v2/fuel_review/siose2020_wfs_probe.json'
    ledger_path = ROOT / 'data/incident_day_v2/fuel_review/unknown_cells.json'
    probe_bytes = probe_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    if hashlib.sha256(probe_bytes).hexdigest() != '224ce89157e5a13d075a7b9d587ef16964d5dcb88da5e82f614523efaf318242' or hashlib.sha256(ledger_bytes).hexdigest() != 'f9f437c68cf165ddff1920b783d13ecff7fb5abfed0e21f3f71df44024108146':
        raise ValueError('Unexpected evidence file hash')
    verify_siose_probe(probe_path, ledger_path)
    probe = json.loads(probe_bytes)
    document = build_estimates(prepared, domain, probe)
    arguments.output.mkdir(parents=True, exist_ok=True)
    (arguments.output / 'estimates.json').write_text(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    classes = _crop(prepared['fuel_classes'], domain['crop'])
    known = _crop(prepared['fuel_valid_mask'], domain['crop'])
    grid = domain['grid']
    barriers = [feature for feature in _features(probe) if _category(feature['properties'])[0] == 'urban_mixed']
    rows = []
    for group_row in range(2):
        for group_column in range(2):
            group = [(row, column) for row in range(grid['height']) for column in range(grid['width']) if known[row][column] and int(row >= grid['height'] // 2) == group_row and int(column >= grid['width'] // 2) == group_column]
            masked = [[value if (int(row >= grid['height'] // 2), int(column >= grid['width'] // 2)) != (group_row, group_column) else 0 for column, value in enumerate(line)] for row, line in enumerate(known)]
            samples = group[::max(1, len(group) // 10)][:10]
            for row, column in samples:
                neighbours, alternatives = _vote(row, column, classes, masked, grid, list(range(1, 11)), barriers)
                predicted = alternatives[0]['class'] if alternatives else 4
                total = sum(item['weight'] for item in alternatives)
                rows.append({'row': row, 'column': column, 'spatial_group': [group_row, group_column], 'actual': classes[row][column], 'predicted': predicted, 'support_count': len(neighbours), 'vote_share': round(alternatives[0]['weight'] / total, 6) if total else None})
    report = {'version': 'incident-day-fuel-spatial-holdout-v1', 'estimate_sha256': document['sha256'],
              'design': 'Up to 10 deterministic known cells per quadrant; all known cells in their quadrant are held out. Unconstrained categorical vote, not a land-cover crosswalk test.',
              'sample_count': len(rows), 'correct': sum(item['actual'] == item['predicted'] for item in rows),
              'accuracy': round(sum(item['actual'] == item['predicted'] for item in rows) / len(rows), 4) if rows else None,
              'uncertainty_groups': [{'minimum_vote_share': minimum, 'maximum_vote_share_exclusive': maximum,
                                      'sample_count': len(group), 'correct': sum(item['actual'] == item['predicted'] for item in group)}
                                     for minimum, maximum in [(0.0, 0.5), (0.5, 0.75), (0.75, 1.01)]
                                     for group in [[item for item in rows if item['vote_share'] is not None and minimum <= item['vote_share'] < maximum]]],
              'limitations': 'Exploratory spatial holdout on known source classes only; vote share is not calibrated uncertainty and does not establish accuracy for missing cells.', 'samples': rows}
    (arguments.output / 'holdout.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({'estimate_sha256': document['sha256'], 'cells': len(document['cells']), 'holdout_accuracy': report['accuracy']}))


if __name__ == '__main__':
    main()
