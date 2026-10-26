"""Compile only allowlisted study inputs from a trusted retrospective bundle."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from wildfire_core.malaga import validate_pack
from envs.wildfire_response.regional.malaga.pack import VERSION, pack_digest


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def compile_pack(bundle, expected_sha256, output):
    bundle, output = Path(bundle), Path(output)
    if output.exists():
        raise ValueError('Refusing to overwrite an episode pack')
    if output.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('Episode packs must not modify the trusted evidence bundle')
    if type(expected_sha256) is not str or len(expected_sha256) != 64:
        raise ValueError('Provide an independently retained bundle fingerprint')
    files = {}
    total = 0
    for path in sorted(bundle.rglob('*')):
        if path.is_symlink():
            raise ValueError('Bundle symlinks are not supported')
        if not path.is_file() or path.relative_to(bundle).as_posix() in ('audit.json', 'geography_check.json'):
            continue
        total += path.stat().st_size
        if total > 64_000_000 or len(files) >= 64:
            raise ValueError('Prepared bundle exceeds its size/count cap')
        with path.open('rb') as stream:
            files[path.relative_to(bundle).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    actual = hashlib.sha256(canonical(files)).hexdigest()
    if actual != expected_sha256:
        raise ValueError('Retrospective bundle differs from its trusted fingerprint')
    manifest_path = bundle / 'manifest.json'
    if manifest_path.stat().st_size > 1_000_000:
        raise ValueError('Manifest exceeds 1 MiB')
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest['version'] != 'wildfire-malaga-evidence-v1'
            or manifest['incident_id'] != 'ES-MA-SIERRA-BERMEJA-2021'
            or manifest['event_year'] != 2021
            or manifest['actor_input_eligible'] is not False
            or manifest['historical_replay_ready'] is not False):
        raise ValueError('Unexpected retrospective evidence contract')
    boxes = [product['layers']['areaOfInterestA']['bbox_crs84'] for product in manifest['products']]
    if len(boxes) != 5:
        raise ValueError('Expected five mapping products')
    bbox = [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]
    clocks = manifest['egif_match']['clocks']
    detected = clocks['detection']['source_timestamp']
    ground = clocks['ground_arrival']['source_timestamp']
    duration = (datetime.fromisoformat(ground) - datetime.fromisoformat(detected)).total_seconds() / 60
    if not math.isfinite(duration) or not 0 <= duration <= 120:
        raise ValueError('Invalid retrospective response prior')
    pack = dict(version=VERSION, role='simulation_configuration', actor_input_eligible=False,
                source_bundle_sha256=actual, region_bbox_lon_lat=bbox, detection_local=detected,
                assumed_ground_transit_minutes=max(1, min(60, math.ceil(duration / 2))))
    pack['sha256'] = pack_digest(pack)
    validate_pack(pack)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(canonical(pack))
    output.chmod(0o600)
    return dict(version=VERSION, sha256=pack['sha256'],
                source_bundle_sha256=actual, actor_input_eligible=False,
                scope='Allowlisted study envelope, detection anchor and assumed transit prior only; not historical conditions')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compile_pack(args.bundle, args.expected_sha256, args.output), indent=2))


if __name__ == '__main__':
    main()
