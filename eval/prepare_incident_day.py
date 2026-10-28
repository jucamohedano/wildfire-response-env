"""Compile an incident-day input pack from three caller-pinned JSON documents."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import _check_hash, _unique, load_inputs, prepare_inputs


def read_document(path, expected):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Source document must not be a symlink')
    with path.open('rb') as stream:
        raw = stream.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('Source document exceeds byte limit')
    document = json.loads(raw, object_pairs_hook=_unique)
    if _check_hash(document, str(path)) != expected:
        raise ValueError('Source document differs from caller pin')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'domain', 'network'):
        parser.add_argument('--' + name, required=True, type=Path)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--synthetic-fuel-class', type=int, choices=range(1, 11))
    parser.add_argument('--fuel-estimates', type=Path)
    parser.add_argument('--fuel-estimates-sha256')
    parser.add_argument('--allow-unapproved', action='store_true')
    options = parser.parse_args()
    if bool(options.fuel_estimates) != bool(options.fuel_estimates_sha256):
        parser.error('Supply both the estimate document and its independent canonical SHA256')
    if options.fuel_estimates and options.synthetic_fuel_class is not None:
        parser.error('Choose categorical estimates or a uniform assumption, not both')
    try:
        documents = [read_document(getattr(options, name), getattr(options, name + '_sha256'))
                     for name in ('prepared', 'domain', 'network')]
        resolution = (None if options.synthetic_fuel_class is None else
                      {'kind': 'synthetic', 'class': options.synthetic_fuel_class})
        if options.fuel_estimates:
            resolution = {'kind': 'estimated', 'document': read_document(
                options.fuel_estimates, options.fuel_estimates_sha256)}
        pack = prepare_inputs(*documents, fuel_resolution=resolution)
        load_inputs(pack, pack['sha256'], allow_unapproved=options.allow_unapproved)
        encoded = (json.dumps(pack, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        descriptor = os.open(options.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        parser.exit(2, f'Preparation failed: {error}\n')
    print(json.dumps({'sha256': pack['sha256'], 'approval_status': pack['approval_status'],
                      'unknown_fuel_cells': pack['provenance']['unknown_fuel_cells']}, sort_keys=True))


if __name__ == '__main__':
    main()
