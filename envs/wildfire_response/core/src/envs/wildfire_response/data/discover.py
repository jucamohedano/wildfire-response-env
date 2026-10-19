"""Inventory public CEMS Spain wildfire candidates and bounded observed-event vectors."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .collect import CEMS_API

LIST_API = CEMS_API.replace('public-activations/', 'public-activations-info/')
ALLOWED_HOSTS = {'rapidmapping.emergency.copernicus.eu',
                 'rapidmapping-viewer.s3.eu-west-1.amazonaws.com'}
KNOWN_CONFLICTS = {
    'EMSR932': dict(reason='News body says 2025; API eventTime and news heading say 2026',
                    url='https://mapping.emergency.copernicus.eu/news/wildfire-in-huelva-province-spain-emsr932/'),
}


def trusted_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ValueError('Expected an approved public CEMS HTTPS endpoint')
    return url


def retrieve(url, destination, offline=False, max_bytes=10_000_000, reuse_cache=False):
    trusted_url(url)
    cached = offline or (reuse_cache and destination.exists())
    if not cached:
        request = Request(url, headers={'User-Agent': 'WildfireResponseResearch/0.2'})
        with urlopen(request, timeout=45) as response:
            trusted_url(response.geturl())
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError('Response exceeds configured byte limit')
        json.loads(body)
        destination.write_bytes(body)
    body = destination.read_bytes()
    if len(body) > max_bytes:
        raise ValueError('Cached response exceeds configured byte limit')
    return json.loads(body), dict(url=url, path=str(destination), bytes=len(body), cached=cached,
                                  sha256=hashlib.sha256(body).hexdigest())


def candidates(items, cutoff):
    selected = {}
    for item in items:
        countries = [country.get('name') if isinstance(country, dict) else country
                     for country in item.get('countries', [])]
        event_date = str(item.get('eventTime', ''))[:10]
        if ('Spain' not in countries or item.get('category') != 'Wildfire'
                or not event_date.startswith('2026-')):
            continue
        if date.fromisoformat(event_date) > date.fromisoformat(cutoff):
            continue
        code = item['code']
        if not re.fullmatch(r'EMSR\d+', code):
            raise ValueError('Invalid activation identifier')
        if code in selected and selected[code] != item:
            raise ValueError('Conflicting duplicate inventory entries')
        selected[code] = item
    return sorted(selected.values(), key=lambda item: item['code'])


def validate_layer(layer):
    if layer.get('type') != 'FeatureCollection' or not isinstance(layer.get('features'), list):
        raise ValueError('Expected a GeoJSON FeatureCollection')
    crs = layer.get('crs', {}).get('properties', {}).get('name')
    if crs not in (None, 'urn:ogc:def:crs:OGC:1.3:CRS84', 'urn:ogc:def:crs:EPSG::4326'):
        raise ValueError('Unsupported coordinate reference system; do not guess')
    for feature in layer['features']:
        geometry = feature.get('geometry') or {}
        if geometry.get('type') not in ('Polygon', 'MultiPolygon'):
            raise ValueError('Expected polygonal observed-event geometry')
        if feature.get('properties', {}).get('event_type') != '8-Wildfire':
            raise ValueError('Observed-event feature is not explicitly classified as wildfire')
        polygons = [geometry.get('coordinates')] if geometry['type'] == 'Polygon' else geometry.get('coordinates')
        if not polygons:
            raise ValueError('Empty observed-event geometry')
        for polygon in polygons:
            if not polygon:
                raise ValueError('Empty polygon')
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError('Unclosed polygon ring')
                for point in ring:
                    if (len(point) < 2 or not all(isinstance(value, (int, float)) and math.isfinite(value)
                                                   for value in point[:2])
                            or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
                        raise ValueError('Invalid longitude/latitude coordinates')
    return len(layer['features'])


def discover(output, cutoff='2026-10-18', offline=False, max_layers=20, reuse_cache=False):
    if date.fromisoformat(cutoff).year != 2026 or max_layers < 0:
        raise ValueError('Expected a 2026 cutoff and nonnegative layer limit')
    output = Path(output)
    raw = output / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    downloads, entries, seen = [], [], set()
    url = LIST_API + '?limit=100'
    page = 0
    while url:
        if url in seen or page >= 100:
            raise ValueError('Cyclic or unexpectedly long pagination')
        seen.add(url)
        payload, provenance = retrieve(url, raw / f'page-{page:03d}.json', offline, reuse_cache=reuse_cache)
        downloads.append(provenance)
        entries.extend(payload['results'])
        url = payload.get('next')
        page += 1
    selected = candidates(entries, cutoff)
    activations, layers, products, failures = [], [], [], []
    pending = defaultdict(list)
    for entry in selected:
        code = entry['code']
        payload, provenance = retrieve(CEMS_API + '?code=' + code, raw / f'{code}.json', offline, reuse_cache=reuse_cache)
        downloads.append(provenance)
        matches = [item for item in payload['results'] if item['code'] == code]
        if len(matches) != 1 or candidates(matches, cutoff) == []:
            raise ValueError('Activation details do not match inventory filters')
        activation = matches[0]
        conflict = KNOWN_CONFLICTS.get(code)
        record = dict(code=code, name=activation['name'], event_time_raw=activation['eventTime'],
                      activation_time_raw=activation.get('activationTime'), countries=activation['countries'],
                      reason_raw=activation.get('reason'), source_url=provenance['url'],
                      status='quarantined_date_conflict' if conflict else 'provider_reported_candidate',
                      date_conflict=conflict, actor_input_eligible=False,
                      scope='activation, not necessarily one incident; AOIs may extend outside Spain',
                      areas=[dict(number=area['number'], name=area['name']) for area in activation.get('aois', [])])
        activations.append(record)
        for area in activation.get('aois', []):
            for product in area.get('products', []):
                metadata = dict(activation_code=code, aoi_number=area['number'], aoi_name=area['name'],
                                product_id=product['id'], type=product.get('type'),
                                version=product.get('version'), images=product.get('images', []),
                                statistics=product.get('stats', {}), download_url=product.get('downloadPath'),
                                actor_input_eligible=False, source_status=record['status'])
                products.append(metadata)
                if conflict or activation.get('sensitive'):
                    continue
                for layer in product.get('layers', []):
                    if not re.search(r'_observedEventA_v\d+_VT$', layer.get('name', '')) or not layer.get('json'):
                        continue
                    pending[code].append((area['number'], product['id'], layer['json']))
    ordered = [dict(code=code, number=items[index][0], product_id=items[index][1], url=items[index][2])
               for index in range(max((len(items) for items in pending.values()), default=0))
               for code, items in pending.items() if index < len(items)]
    for task in ordered[:max_layers]:
        code, number, product_id, url = task['code'], task['number'], task['product_id'], task['url']
        path = raw / f'{code}-{number}-{product_id}-observed.geojson'
        try:
            data, evidence = retrieve(url, path, offline, reuse_cache=reuse_cache)
            count = validate_layer(data)
            downloads.append(evidence)
            layers.append(dict(activation_code=code, aoi_number=number,
                               product_id=product_id, feature_count=count, path=str(path),
                               source_url=url, sha256=evidence['sha256'],
                               geometry_role='provider_observed_wildfire_extent',
                               actor_input_eligible=False,
                               warning='Retrospective product; overlapping dates/versions must not be summed'))
        except (OSError, ValueError) as error:
            failures.append(dict(url=url, error=str(error)))
    summary = dict(activations=len(activations), mapping_areas=sum(len(item['areas']) for item in activations),
                   products=len(products), vector_layers=len(layers),
                   vector_features=sum(item['feature_count'] for item in layers),
                   quarantined=sum(item['status'].startswith('quarantined') for item in activations),
                   failures=len(failures), raw_bytes=sum(item['bytes'] for item in downloads))
    result = dict(schema_version='spain-cems-discovery-v1', cutoff=cutoff,
                  coverage='All pages of public Rapid Mapping index, not all Spanish wildfires',
                  layer_sampling='Round-robin by activation, bounded attempts and 10 MB per response',
                  summary=summary, activations=activations, products=products, layers=layers, failures=failures)
    (output / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'provenance.json').write_text(json.dumps(dict(
        assembled_at=datetime.now(timezone.utc).isoformat(), offline_rebuild=offline,
        timestamp_policy='Assembly time is not source publication time or the original fetch time of cached bytes',
        requests=downloads, collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/spain-cems-2026')
    parser.add_argument('--cutoff', default='2026-10-18')
    parser.add_argument('--max-layers', type=int, default=20)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--reuse-cache', action='store_true')
    args = parser.parse_args()
    print(json.dumps(discover(args.output, args.cutoff, args.offline, args.max_layers, args.reuse_cache)))


if __name__ == '__main__':
    main()
