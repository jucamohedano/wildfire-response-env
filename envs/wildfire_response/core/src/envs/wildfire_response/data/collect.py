"""Fetch small, source-backed CEMS metadata and build an incident evidence catalog."""
import argparse
import copy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CATALOG = Path(__file__).with_name('spain_2026.json')
CEMS_API = 'https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/'


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def available_after(source):
    """Conservative policy bound; it is not a claimed publication timestamp."""
    following = date.fromisoformat(source['published_date']) + timedelta(days=1)
    return datetime.combine(following, time.min, ZoneInfo(source['timezone'])).isoformat()


def facts_as_of(incident, sources, instant):
    cutoff = datetime.fromisoformat(instant)
    if cutoff.tzinfo is None:
        raise ValueError('An explicit time-zone offset is required')
    return [dict(copy.deepcopy(fact), actor_input_eligible=False) for fact in incident['facts']
            if datetime.fromisoformat(available_after(sources[fact['source']])) <= cutoff]


def polygon(wkt):
    if not isinstance(wkt, str) or not wkt.strip().startswith('POLYGON ((') or re.search(r'\)\s*,\s*\(', wkt):
        raise ValueError('Expected a simple CEMS AOI polygon; do not guess other geometries')
    pairs = re.findall(r'([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)', wkt)
    ring = [[float(longitude), float(latitude)] for longitude, latitude in pairs]
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise ValueError('Invalid or unclosed AOI polygon')
    return dict(type='Polygon', coordinates=[ring])


def normalize(case, response):
    matches = [item for item in response.get('results', []) if item.get('code') == case['cems']['code']]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous activation')
    activation = matches[0]
    countries = [item.get('name') if isinstance(item, dict) else item for item in activation.get('countries', [])]
    if 'Spain' not in countries:
        raise ValueError('Activation is not in Spain')
    if activation.get('category') != 'Wildfire' or not activation.get('eventTime', '').startswith('2026-'):
        raise ValueError('Activation is not a verified 2026 wildfire')
    expected = dict(zip(case['cems']['aoi_numbers'], case['cems']['expected_aoi_names']))
    areas, features, products = [], [], []
    for area in activation.get('aois', []):
        number = area['number']
        if number not in expected:
            continue
        if area['name'] != expected[number]:
            raise ValueError('AOI identity changed; review the association before importing')
        geometry = polygon(area['extent'])
        features.append(dict(type='Feature', geometry=geometry, properties=dict(
            incident_id=case['id'], activation_code=activation['code'], aoi_number=number,
            name=area['name'], geometry_role='mapping_coverage_not_fire_perimeter')))
        areas.append(dict(number=number, name=area['name']))
        for product in area.get('products', []):
            products.append(dict(incident_id=case['id'], activation_code=activation['code'], aoi_number=number,
                product_id=product['id'], type=product.get('type'), version=product.get('version', {}).get('number'),
                image_acquisition_times_raw=[image.get('acquisitionTime') for image in product.get('images', [])],
                delivered_at_raw=product.get('version', {}).get('deliveryTime'),
                timestamp_timezone='provider values preserved; offset not asserted',
                statistics=copy.deepcopy(product.get('stats', {})), download_url=product.get('downloadPath'),
                role='retrospective_evaluator_metadata_not_early_agent_input'))
    if {area['number'] for area in areas} != set(expected):
        raise ValueError('Expected mapping area missing')
    return dict(code=activation['code'], name=activation['name'],
                event_time_raw=activation.get('eventTime'), activation_time_raw=activation.get('activationTime'),
                activation_scope='may include other incidents; do not use as a local ignition timestamp',
                areas=areas), features, products


def fetch(url, destination):
    request = Request(url, headers={'User-Agent':'WildfireResponseResearch/0.1'})
    with urlopen(request, timeout=30) as response:
        body = response.read(10_000_001)
    if len(body) > 10_000_000:
        raise ValueError('Metadata unexpectedly exceeds 10 MB')
    destination.write_bytes(body)
    return dict(url=url, path=str(destination), bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def collect(output, offline=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    raw = output/'raw'
    raw.mkdir(exist_ok=True)
    seed = catalog()
    records, features, products, downloads = [], [], [], []
    for case in seed['incidents']:
        code = case['cems']['code']
        path = raw/f'{code}.json'
        url = CEMS_API+'?code='+code
        if offline:
            body = path.read_bytes()
            downloads.append(dict(url=url, path=str(path), bytes=len(body), sha256=hashlib.sha256(body).hexdigest()))
        else:
            downloads.append(fetch(url, path))
        details, coverage, mapped = normalize(case, json.loads(path.read_text(encoding='utf-8')))
        record = copy.deepcopy(case)
        record['cems_metadata'] = details
        for fact in record['facts']:
            fact['available_after'] = available_after(seed['sources'][fact['source']])
            fact['availability_policy'] = 'end_of_publication_day_conservative; actual release time unknown'
            fact['role'] = 'retrospective_source_fact'
            fact['actor_input_eligible'] = False
        records.append(record)
        features.extend(coverage)
        products.extend(mapped)
    for name, value in [('incidents.json',dict(schema_version=seed['schema_version'], sources=seed['sources'], incidents=records)),
                        ('mapping_coverage.geojson',dict(type='FeatureCollection',features=features)),
                        ('products.json',products),
                        ('provenance.json',dict(retrieved_at=datetime.now(timezone.utc).isoformat(),
                             offline_rebuild=offline, requests=downloads, seed_sha256=hashlib.sha256(CATALOG.read_bytes()).hexdigest()))]:
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return dict(incidents=len(records), mapping_areas=len(features), products=len(products))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/spain-fires-2026')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    print(json.dumps(collect(args.output, args.offline)))


if __name__ == '__main__':
    main()
