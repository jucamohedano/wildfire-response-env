"""Small allowlisted simulation inputs, separate from retrospective evidence."""
import copy
from datetime import datetime
import json
import math
from pathlib import Path
import re

from ..data import geometry, load_pack as load_engine_defaults
from ..ops.generator import digest


VERSION = 'wildfire-malaga-episode-pack-v1'
FIELDS = {'version', 'role', 'actor_input_eligible', 'source_bundle_sha256',
          'region_bbox_lon_lat', 'detection_local', 'assumed_ground_transit_minutes', 'sha256'}
MAX_BYTES = 65536


def pack_digest(pack):
    return digest({key: value for key, value in pack.items() if key != 'sha256'})


def validate_pack(pack):
    if type(pack) is not dict or set(pack) != FIELDS:
        raise ValueError('Expected the exact Málaga episode-pack fields')
    if (pack['version'] != VERSION or pack['role'] != 'simulation_configuration'
            or pack['actor_input_eligible'] is not False):
        raise ValueError('Unknown Málaga configuration contract')
    if (type(pack['source_bundle_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', pack['source_bundle_sha256'])):
        raise ValueError('Expected a trusted source-bundle fingerprint')
    bbox = pack['region_bbox_lon_lat']
    if (type(bbox) is not list or len(bbox) != 4
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)):
        raise ValueError('Expected four finite geographic coordinates')
    west, south, east, north = bbox
    if not (-5.5 <= west < east <= -4.8 and 36.2 <= south < north <= 36.9):
        raise ValueError('Study envelope must be within the bounded Málaga context area')
    if pack['detection_local'] != '2021-09-08T21:35:00':
        raise ValueError('Unexpected source detection anchor')
    transit = pack['assumed_ground_transit_minutes']
    if type(transit) is not int or not 1 <= transit <= 60:
        raise ValueError('Assumed transit must be bounded model minutes')
    if type(pack['sha256']) is not str or pack['sha256'] != pack_digest(pack):
        raise ValueError('Málaga configuration digest mismatch')
    grid = geometry(engine_pack(pack))
    if not (16 <= grid['width'] <= 128 and 12 <= grid['height'] <= 128):
        raise ValueError('Unsupported exercise grid dimensions')
    return pack


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate episode-pack field')
        result[key] = value
    return result


def load_pack(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Episode pack exceeds 64 KiB')
    return validate_pack(json.loads(data, object_pairs_hook=_unique))


def engine_pack(pack):
    result = copy.deepcopy(load_engine_defaults())
    result['version'] = VERSION
    result['sources'] = {}
    result['region'] = dict(bbox_lon_lat=pack['region_bbox_lon_lat'][:],
                            basis='Retrospectively selected study envelope, not an active-fire perimeter')
    result['assumptions']['cell_km'] = .5
    names = {'valverde': 'Exercise ground desk A', 'cabezudos': 'Exercise ground desk B',
             'las-arenas': 'Exercise air desk'}
    result['facilities'] = [dict(id=identifier, name=name, type='synthetic_support_desk', coordinates=None)
                            for identifier, name in names.items()]
    return result


def clock_minute(pack):
    stamp = datetime.fromisoformat(pack['detection_local'])
    return stamp.hour * 60 + stamp.minute
