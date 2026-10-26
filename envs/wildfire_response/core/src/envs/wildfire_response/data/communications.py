"""Source summaries and explicitly synthetic report reconstructions, not transcripts."""
import argparse
import copy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse


VERSION = 'wildfire-communication-evidence-v1'
CATALOG = Path(__file__).with_name('communications_2026.json')
_TEMPLATES = {
    'flames_near_road': 'Veo llamas cerca de una carretera. No puedo confirmar la causa.',
    'flames_near_woodland': 'Veo fuego en una zona de monte. Necesito ayuda para precisar la ubicación.',
}


def communication_catalog():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    validate_catalog(catalog)
    return catalog


def validate_catalog(catalog):
    if (catalog.get('version') != VERSION or catalog.get('exhaustive') is not False
            or catalog.get('historical_replay_ready') is not False
            or catalog.get('split') != 'development'):
        raise ValueError('This is a bounded development evidence pack, not historical replay')
    reviewed = date.fromisoformat(catalog['reviewed_on'])
    sources = {source['id']: source for source in catalog['sources']}
    if not sources or len(sources) != len(catalog['sources']):
        raise ValueError('Source IDs must be unique and nonempty')
    for source in sources.values():
        address = urlparse(source['url'])
        if address.scheme != 'https' or address.hostname != 'www.juntadeandalucia.es':
            raise ValueError('Only the reviewed official publisher is admitted in v1')
        if not date.fromisoformat(source['published_date']) <= date.fromisoformat(source['retrieved_on']) <= reviewed:
            raise ValueError('Source dates must precede the review cutoff')
        if (not re.fullmatch('[a-f0-9]{64}', source['snapshot_sha256'])
                or type(source['snapshot_bytes']) is not int or source['snapshot_bytes'] <= 0):
            raise ValueError('Each source needs a byte-counted snapshot hash')
        path = Path(source['snapshot_path'])
        if path.is_absolute() or '..' in path.parts or path.parts[:1] != ('.runtime',):
            raise ValueError('Snapshots must stay in the ignored local runtime directory')
        if (source['publication_time_known'] is not False
                or source['archive_at_event_time_verified'] is not False):
            raise ValueError('No precise publication or event-time archive proof was admitted')
    records = catalog['records']
    if not records or len({record['id'] for record in records}) != len(records):
        raise ValueError('Evidence IDs must be unique and nonempty')
    for record in records:
        if record['source_id'] not in sources:
            raise ValueError('Unknown source reference')
        if (record['actor_input_eligible'] is not False
                or record['evidence_kind'] != 'official_retrospective_summary'
                or record['individual_received_at'] is not None):
            raise ValueError('Retrospective summaries are not timestamped actor messages')
        timing = record['event_time']
        if timing['value'] is None:
            if timing['precision'] != 'unknown':
                raise ValueError('Unknown time needs unknown precision')
        else:
            instant = datetime.fromisoformat(timing['value'])
            if (instant.utcoffset() is None or timing['precision'] not in (
                    'aggregate_window_start_not_each_call', 'approximate_first_receipt_not_observation')
                    or instant.date() > date.fromisoformat(sources[record['source_id']]['published_date'])):
                raise ValueError('Retain the uncertainty and timezone of historical timing')
        template = record['exercise_template']
        if template is not None and (template not in _TEMPLATES
                                    or record['purpose'] != 'report_fire' or record['channel'] != 'phone'):
            raise ValueError('Only citizen fire claims can seed report exercises')
    return True


def verify_snapshots(root):
    """Opt-in byte verification; snapshots are local and never bundled in the wheel."""
    root = Path(root).resolve()
    results = []
    for source in communication_catalog()['sources']:
        path = (root / source['snapshot_path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Snapshot must resolve inside the requested root')
        content = path.read_bytes()
        if (len(content) != source['snapshot_bytes']
                or hashlib.sha256(content).hexdigest() != source['snapshot_sha256']):
            raise ValueError(f"Snapshot mismatch: {source['id']}")
        results.append(source['id'])
    return results


def reconstruct_report(evidence_id, *, report_id, observed_at, received_at,
                       location=None, mapping_note=None):
    """Build one assumed exercise report plus separate evaluator provenance.

    Historical aggregate counts never determine a queue distribution. The caller
    supplies exercise timing and an explicit hypothetical location mapping.
    """
    records = {record['id']: record for record in communication_catalog()['records']}
    if evidence_id not in records or records[evidence_id]['exercise_template'] is None:
        raise ValueError('Choose a supported citizen-claim reconstruction')
    if not isinstance(report_id, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', report_id):
        raise ValueError('Use an opaque exercise report ID')
    if (type(observed_at) is not int or type(received_at) is not int
            or not 0 <= observed_at <= received_at <= 100):
        raise ValueError('Exercise times must be ordered integer model minutes from zero to 100')
    if location is not None:
        if (not isinstance(location, list) or len(location) != 2
                or any(type(value) is not int or value < 0 for value in location)
                or not isinstance(mapping_note, str) or not mapping_note.strip()):
            raise ValueError('Located reports need integer grid coordinates and a mapping assumption')
    record = records[evidence_id]
    report = dict(id=report_id, observed_at=observed_at, received_at=received_at,
                  location=copy.deepcopy(location), source=f'synthetic-caller:{report_id}',
                  channel='synthetic_phone', text=_TEMPLATES[record['exercise_template']])
    provenance = dict(evidence_version=VERSION, evidence_id=evidence_id,
                      source_id=record['source_id'], incident_group=record['incident_id'],
                      authenticity='source_inspired_synthetic_not_transcript',
                      timing='assumed_exercise_minutes_not_historical_timing',
                      geography='hypothetical_grid_mapping' if location is not None else 'unlocated',
                      mapping_note=mapping_note, split='development')
    return dict(report=report, evaluator_provenance=provenance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-snapshots', metavar='REPOSITORY_ROOT')
    options = parser.parse_args()
    catalog = communication_catalog()
    result = dict(version=VERSION, sources=len(catalog['sources']),
                  evidence_records=len(catalog['records']), authentic_transcripts=0,
                  historical_replay_ready=False, coverage_gaps=catalog['coverage_gaps'])
    if options.verify_snapshots:
        result['verified_snapshots'] = verify_snapshots(options.verify_snapshots)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
