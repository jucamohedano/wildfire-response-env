#!/usr/bin/env python3
"""Shared pinned source verification and sampling for the extended map."""
import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

PARENT_FILE_SHA256 = 'c4568ea6f5f16e41c8a81768f510def1e7d7440eebce0af0e5382d7226f0638e'
PARENT_DOCUMENT_SHA256 = '50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d'
PARENT_NETWORK_SHA256 = '12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16'
FUEL_ARCHIVE_SHA256 = 'ed06080d81048de18dc34caf0f4fcc2f26d626f020bf132277d4e470a71a83d8'
FUEL_MEMBER = 'FuelMap2000_NFFL_LAEA.tif'
FUEL_MEMBER_SHA256 = 'cfb7447de166fb6fab12b685c98c1d12f55c4c62708ebff2af26a9ca59452102'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'invalid number {value}')))


def load_parent(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARENT_FILE_SHA256:
        raise ValueError('parent file hash mismatch')
    parent = json.loads(raw, object_pairs_hook=_unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'invalid number {value}')))
    if parent.get('sha256') != PARENT_DOCUMENT_SHA256 or digest({k: v for k, v in parent.items() if k != 'sha256'}) != PARENT_DOCUMENT_SHA256:
        raise ValueError('parent document hash mismatch')
    if parent.get('version') != 'incident-day-inputs-v2' or parent.get('network_sha256') != PARENT_NETWORK_SHA256:
        raise ValueError('unexpected parent identity')
    grid = parent['grid']
    if (grid['crs'], grid['width'], grid['height'], grid['cell_km'], grid['transform']) != ('EPSG:25830', 37, 36, 0.5, [500, 0, 297000, 0, -500, 4054500]):
        raise ValueError('unexpected parent grid')
    prov = parent['provenance']
    for matrix in (parent['heights'], prov['source_fuel_classes'], prov['source_fuel_known_mask']):
        if len(matrix) != 36 or any(len(row) != 37 for row in matrix):
            raise ValueError('invalid parent matrix')
    for row in range(36):
        for col in range(37):
            height = parent['heights'][row][col]
            fuel = prov['source_fuel_classes'][row][col]
            known = prov['source_fuel_known_mask'][row][col]
            if (type(height) not in (float, int) or not math.isfinite(height)
                or type(fuel) is not int or fuel not in range(11)
                or type(known) is not int or known not in (0, 1) or bool(fuel) != bool(known)):
                raise ValueError('invalid parent sample')
    return parent


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sample_fuel(archive, parent, *, width, height, west, north, parent_row, parent_col):
    """Sample source pixel containing each projected 500 m centre, offline."""
    import numpy as np
    import rasterio
    from pyproj import Transformer
    from rasterio.warp import transform
    from rasterio.windows import Window

    if file_sha256(archive) != FUEL_ARCHIVE_SHA256:
        raise ValueError('fuel archive hash mismatch')
    if parent_row < 0 or parent_col < 0 or parent_row + 36 > height or parent_col + 37 > width:
        raise ValueError('parent grid is outside sample extent')
    points = [(west + 250 + 500 * c, north - 250 - 500 * r)
              for r in range(height) for c in range(width)]
    with tempfile.TemporaryDirectory(prefix='extended-map-effis-') as tmp:
        path = Path(tmp) / FUEL_MEMBER
        with zipfile.ZipFile(archive) as source:
            info = source.getinfo(FUEL_MEMBER)
            if info.file_size != 621969457:
                raise ValueError('unexpected fuel archive member size')
            with source.open(info) as inp, path.open('wb') as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
        if file_sha256(path) != FUEL_MEMBER_SHA256:
            raise ValueError('fuel member hash mismatch')
        with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO', GDAL_NUM_THREADS='1'):
            with rasterio.open(path) as src:
                if (src.crs.to_string() != 'EPSG:3035' or src.count != 1 or
                    src.shape != (23200, 26801) or src.dtypes != ('uint8',) or src.nodata != 0 or
                    not np.allclose(tuple(src.transform)[:6], (250, 0, 800000, 0, -250, 6500000), atol=1e-7, rtol=0)):
                    raise ValueError('unexpected EFFIS source raster metadata')
                # Original audit projects stored WGS84 centres to the native grid.
                # Independently check every GDAL pixel against pyproj on UTM centres.
                lon, lat = transform('EPSG:25830', 'EPSG:4326', *zip(*points))
                for r in range(36):
                    for c in range(37):
                        lon[(r + parent_row) * width + c + parent_col], lat[(r + parent_row) * width + c + parent_col] = parent['grid']['centre_lon_lat'][r][c]
                xs, ys = transform('EPSG:4326', 'EPSG:3035', lon, lat)
                check_x, check_y = Transformer.from_crs('EPSG:25830', 'EPSG:3035', always_xy=True).transform(*zip(*points))
                indexes = [src.index(x, y) for x, y in zip(xs, ys)]
                if indexes != [src.index(x, y) for x, y in zip(check_x, check_y)]:
                    raise ValueError('projection methods select different source pixels')
                if any(not (0 <= r < src.height and 0 <= c < src.width) for r, c in indexes):
                    raise ValueError('source footprint does not cover all grid centres')
                r0, r1 = min(r for r, c in indexes), max(r for r, c in indexes)
                c0, c1 = min(c for r, c in indexes), max(c for r, c in indexes)
                data = src.read(1, window=Window(c0, r0, c1-c0+1, r1-r0+1), masked=True)
                samples = []
                for r, c in indexes:
                    value = data[r-r0, c-c0]
                    sample = None if np.ma.is_masked(value) else int(value)
                    if sample is not None and sample not in range(1, 11):
                        raise ValueError('unexpected EFFIS fuel class')
                    samples.append(sample)
    return [samples[r * width:(r + 1) * width] for r in range(height)]



def serialize(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def write_private_json(path, value):
    """Keep source-derived grids outside the tracked project tree."""
    path = Path(path)
    root = Path(__file__).resolve().parents[1]
    resolved = path.resolve()
    allowed = (Path('/tmp').resolve(), (root / '.runtime').resolve())
    project_runtime = (root / '.runtime').resolve()
    if (path.is_symlink() or (resolved.is_relative_to(root) and not resolved.is_relative_to(project_runtime))
        or not any(resolved.is_relative_to(base) and resolved != base for base in allowed)):
        raise ValueError('output must be under /tmp outside the project or in the repository .runtime directory')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(serialize(value))
