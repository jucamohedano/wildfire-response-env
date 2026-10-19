"""Bundle reviewed evidence and CEMS candidates without treating them as training replays."""
import argparse
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def consolidate(paths, cutoff='2026-10-18'):
    sources, incidents, facts = {}, {}, {}
    for path in paths:
        document = read_json(path)
        namespace = Path(path).stem
        raw_sources = document['sources']
        entries = raw_sources.items() if isinstance(raw_sources, dict) else [(item['id'], item) for item in raw_sources]
        source_refs = {}
        for identifier, source in entries:
            reference = namespace + ':' + identifier
            sources[reference] = copy.deepcopy(source)
            source_refs[identifier] = reference
        for incident in document['incidents']:
            identifier = incident['id']
            if not identifier.startswith('es-2026-'):
                raise ValueError('Only explicitly identified 2026 Spanish incidents may be consolidated')
            record = incidents.setdefault(identifier, dict(id=identifier, name=incident['name'],
                                                          actor_input_eligible=False, evidence_documents=[]))
            record['evidence_documents'].append(str(path))
            for fact in incident['facts']:
                references = fact.get('source_ids') or [fact['source']]
                normalized_refs = [source_refs[reference] for reference in references]
                for reference in normalized_refs:
                    published = sources[reference].get('published_date')
                    if not published or not published.startswith('2026-') or date.fromisoformat(published) > date.fromisoformat(cutoff):
                        raise ValueError('Fact lacks a qualifying dated source')
                key = (identifier, fact['field'], json.dumps(fact['value'], sort_keys=True, ensure_ascii=False))
                normalized = facts.setdefault(key, dict(incident_id=identifier, field=fact['field'],
                                                        value=copy.deepcopy(fact['value']), source_refs=[],
                                                        qualifiers=[], actor_input_eligible=False,
                                                        role='retrospective_evaluator_evidence'))
                normalized['source_refs'] = sorted(set(normalized['source_refs'] + normalized_refs))
                for qualifier in [fact.get('qualifier'), fact.get('uncertainty')]:
                    if qualifier and qualifier not in normalized['qualifiers']:
                        normalized['qualifiers'].append(qualifier)
    return sources, list(incidents.values()), list(facts.values())


def assemble(output, inventory, evidence, raw_roots=(), cutoff='2026-10-18', attachments=(), national=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new output directory to preserve evidence versions')
    inventory = Path(inventory)
    discovered = read_json(inventory / 'inventory.json')
    if discovered['cutoff'] != cutoff:
        raise ValueError('Inventory and evidence cutoff differ')
    sources, incidents, facts = consolidate(evidence, cutoff)
    national_rows, national_metadata = [], None
    if national:
        national = Path(national)
        national_metadata = read_json(national / 'manifest.json')
        national_rows = [json.loads(line) for line in (national / 'large_fires.jsonl').read_text(encoding='utf-8').splitlines()]
        if national_metadata['cutoff'] != cutoff or national_metadata['rows'] != len(national_rows):
            raise ValueError('National table cutoff or count mismatch')
        if any(row.get('actor_input_eligible') is not False or date.fromisoformat(row['incident_date']).year != 2026
               or date.fromisoformat(row['incident_date']) > date.fromisoformat(cutoff) for row in national_rows):
            raise ValueError('Unexpected national table eligibility or date')
        raw_roots = [*raw_roots, national]
    output.mkdir(parents=True)
    copied, seen = [], set()
    input_paths = [Path(path) for path in [*evidence, *attachments]]
    input_paths += [path for root in [inventory, *map(Path, raw_roots)] for path in sorted(root.rglob('*')) if path.is_file()]
    for path in input_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 25_000_000:
            raise ValueError('Expected regular evidence files no larger than 25 MB')
        relative = Path('evidence') / f'{len(copied):04d}-{path.name}'
        destination = output / relative
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, destination)
        copied.append(dict(original_path=str(path), bundle_path=str(relative), bytes=path.stat().st_size,
                           sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
    for filename, rows in [('incidents.jsonl', incidents), ('facts.jsonl', facts),
                           ('activations.jsonl', discovered['activations']), ('products.jsonl', discovered['products']),
                           ('layers.jsonl', discovered['layers']), ('national_large_fires.jsonl', national_rows)]:
        (output / filename).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    (output / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    manifest = dict(schema_version='spain-wildfire-evidence-bundle-v1', cutoff=cutoff,
                    counts=dict(reviewed_incidents=len(incidents), reviewed_fact_values=len(facts),
                                source_references=len(sources), national_provisional_rows=len(national_rows),
                                **discovered['summary']), national_metadata=national_metadata,
                    files=copied, actor_input_eligible=False,
                    publication_status='local research bundle; redistribution rights not cleared',
                    training_status='not historical trajectories; no train/validation/test split assigned',
                    coverage='Bounded official-source evidence plus public CEMS index; not all Spanish fires',
                    linkage='Incident IDs deduplicated; no automatic matching of incidents to CEMS activations',
                    path_policy='Resolve original source paths through files[].original_path -> bundle_path')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest['counts']


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--evidence', nargs='+', required=True)
    parser.add_argument('--raw-root', action='append', default=[])
    parser.add_argument('--cutoff', default='2026-10-18')
    parser.add_argument('--attachment', action='append', default=[])
    parser.add_argument('--national', help='Directory produced by the MITECO extractor')
    args = parser.parse_args()
    print(json.dumps(assemble(args.output, args.inventory, args.evidence, args.raw_root, args.cutoff, args.attachment, args.national)))


if __name__ == '__main__':
    main()
