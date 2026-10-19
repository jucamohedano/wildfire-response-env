"""Extract provisional large-fire rows from an archived official MITECO dashboard."""
import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re

SOURCE_URL = 'https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/temas/incendios-forestales/estimacion_provisional.html'


def extract(html, cutoff='2026-10-18'):
    match = re.search(r'\bvar\s+D\s*=\s*', html)
    if not match:
        raise ValueError('Official dashboard structure changed; review before importing')
    data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    metadata = data['meta']
    if metadata['curYear'] != '2026' or date.fromisoformat(metadata['fechaGen']) > date.fromisoformat(cutoff):
        raise ValueError('Wrong year or future dashboard version')
    rows = []
    for index, item in enumerate(data['gif']):
        incident_date = datetime.strptime(item['fecha'], '%d/%m/%Y').date()
        if incident_date.year != 2026 or incident_date > date.fromisoformat(cutoff):
            raise ValueError('Unexpected incident date')
        if not isinstance(item['forr'], (int, float)) or item['forr'] < 0:
            raise ValueError('Invalid provisional forest area')
        rows.append(dict(provider_row_id=f'miteco-2026-gif-{index:03d}',
                         municipality=item['muni'], province=item['prov'], community=item['ccaa'],
                         incident_date=incident_date.isoformat(), provisional_forest_area_ha=item['forr'],
                         source_url=SOURCE_URL, provider_fields=item,
                         actor_input_eligible=False, role='retrospective_provisional_large_fire_row',
                         warning='Not a full incident census. Geometry may be matched/shared; not exact ignition. '
                                 'Do not sum overlapping perimeters or equate forest area with total affected area.'))
    if len(rows) != data['cur']['nG']:
        raise ValueError('Large-fire table count does not match dashboard summary')
    return metadata, rows


def export(snapshot, output, cutoff='2026-10-18'):
    snapshot, output = Path(snapshot), Path(output)
    metadata, rows = extract(snapshot.read_text(encoding='utf-8'), cutoff)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'large_fires.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    summary = dict(schema_version='miteco-provisional-large-fires-v1', rows=len(rows), cutoff=cutoff,
                   source_url=SOURCE_URL, snapshot=str(snapshot),
                   snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(), provider_metadata=metadata,
                   limitations='Provisional advance, not final EGIF. Dashboard generation and advance dates differ. '
                               'Geometry match metadata retained; underlying geometry not independently certified.')
    (output / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dict(rows=len(rows), communities=len({row['community'] for row in rows}),
                provinces=len({row['province'] for row in rows}))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--output', default='.runtime/spain-miteco-2026')
    parser.add_argument('--cutoff', default='2026-10-18')
    args = parser.parse_args()
    print(json.dumps(export(args.snapshot, args.output, args.cutoff)))


if __name__ == '__main__':
    main()
