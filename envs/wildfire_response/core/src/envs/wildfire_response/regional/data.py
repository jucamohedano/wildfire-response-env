"""Validate and archive the regional evidence pack independently of episode logic."""
import argparse
import hashlib
import json
import math
from html.parser import HTMLParser
from pathlib import Path

from ..data.collect import fetch

PACK = Path(__file__).resolve().parents[1] / 'data' / 'huelva_regional_v2.json'


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def text_digest(body):
    parser = SourceText()
    parser.feed(body.decode('utf-8'))
    return hashlib.sha256(' '.join(' '.join(parser.parts).split()).encode()).hexdigest()


def load_pack():
    pack = json.loads(PACK.read_text(encoding='utf-8'))
    facilities = {item['id'] for item in pack['facilities']}
    if len(facilities) != len(pack['facilities']) or any(item['coordinates'] is not None for item in pack['facilities']):
        raise ValueError('Review facility identity or newly supplied coordinates before using this pack')
    if any(unit['home_base'] not in facilities for unit in pack['scenario_inventory']):
        raise ValueError('Resource references unknown home base')
    return pack


def geometry(pack):
    west, south, east, north = pack['region']['bbox_lon_lat']
    longitude_km = 111.32 * math.cos(math.radians((north + south) / 2))
    cell = pack['assumptions']['cell_km']
    return dict(width=math.ceil((east-west)*longitude_km/cell),
                height=math.ceil((north-south)*111.32/cell), cell_km=cell,
                northwest_lon_lat=[west, north], longitude_km_per_degree=longitude_km,
                transform='approximate local equirectangular; padded coverage envelope, not surveyed terrain')


def grid_to_lonlat(pack, column, row):
    grid = geometry(pack)
    return [grid['northwest_lon_lat'][0] + (column+.5)*grid['cell_km']/grid['longitude_km_per_degree'],
            grid['northwest_lon_lat'][1] - (row+.5)*grid['cell_km']/111.32]


def prepare(output, offline=False):
    output = Path(output)
    if output.exists() and not offline:
        raise ValueError('Use a fresh directory for a live source snapshot, or --offline to rebuild cached evidence')
    output.mkdir(parents=True, exist_ok=True)
    pack = load_pack()
    requests = []
    for identifier, source in pack['sources'].items():
        path = output / (identifier + ('.json' if identifier == 'cems' else '.html'))
        if offline:
            body = path.read_bytes()
            evidence = dict(url=source['url'], path=str(path), bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        else:
            evidence = fetch(source['url'], path)
        if identifier != 'cems' and source.get('text_sha256'):
            evidence['text_sha256'] = text_digest(path.read_bytes())
            if evidence['text_sha256'] != source['text_sha256']:
                raise ValueError(f'{identifier} source text changed; review before updating the curated pack')
        elif source.get('snapshot_sha256') and evidence['sha256'] != source['snapshot_sha256']:
            raise ValueError(f'{identifier} source changed; review before updating the curated pack')
        requests.append(evidence)
    activation = json.loads((output/'cems.json').read_text())['results'][0]
    area = next(item for item in activation['aois'] if item['number'] == pack['region']['aoi_number'])
    if activation['code'] != 'EMSR913' or not activation['eventTime'].startswith('2026-') or area['name'] != 'Niebla':
        raise ValueError('Unexpected mapping activation identity')
    from ..data.collect import polygon
    ring = polygon(area['extent'])['coordinates'][0]
    bbox = [min(point[0] for point in ring), min(point[1] for point in ring),
            max(point[0] for point in ring), max(point[1] for point in ring)]
    if any(abs(actual-expected) > 1e-6 for actual, expected in zip(bbox, pack['region']['bbox_lon_lat'])):
        raise ValueError('Mapping coverage changed; review the regional extent')
    for name, data in [('pack.json', pack), ('provenance.json', dict(
            pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest(), source_requests=requests,
            offline_rebuild=offline, reviewed_on=pack['reviewed_on'], geometry=geometry(pack)))]:
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return dict(facilities=len(pack['facilities']), scenario_resources=len(pack['scenario_inventory']), sources=len(requests))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/huelva-regional-v2-data')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.offline)))


if __name__ == '__main__':
    main()
