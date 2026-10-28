"""Offline, display-only terrain for an OpenLayers ImageStatic layer.

render_terrain(prepared, domain, *, source_reader=None) accepts dictionaries
already pinned by the core loaders. source_reader is a zero-argument callable
returning raw ASC bytes or None for an absent source; injected bytes still must
match compile_topographic_domain.INPUT_PINS. The default reads relative to this
module's repository, independent of the working directory. Present invalid data
raises ValueError; only absence selects the explicitly labelled 500 m fallback.

Pure helpers: crop_samples(samples, grid) selects the pinned 25 m lattice with
one sample of halo; terrain_rgb(elevations, *, cell_size_metres, halo=1) returns
(width, height, row-major RGB bytes); encode_png(width, height, rgb) encodes those
pixels. All matrices are north-first. No heights are repaired or interpolated.
terrain_relief uses the same validated stencil for transparent, edge-feathered
RGBA lighting; render_terrain(relief=True) encodes that display-only layer.
"""
import hashlib
import math
from pathlib import Path
import struct
import zlib

from eval import compile_topographic_domain as compiler


MAX_SAMPLES = 1024 * 1024
CRS = 'EPSG:25830'


def _finite(value, name):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be numeric')
    try:
        numeric = float(value)
    except OverflowError as error:
        raise ValueError(f'{name} exceeds numeric bounds') from error
    if not math.isfinite(numeric):
        raise ValueError(f'{name} must be finite')
    return numeric


def _shape(values):
    if (not isinstance(values, (list, tuple)) or not values
            or not isinstance(values[0], (list, tuple)) or not values[0]):
        raise ValueError('Expected a nonempty rectangular matrix')
    height, width = len(values), len(values[0])
    if (height * width > MAX_SAMPLES or any(
            not isinstance(row, (list, tuple)) or len(row) != width for row in values)):
        raise ValueError('Matrix is ragged or exceeds the sample cap')
    return height, width


def _positive_heights(values):
    for row in values:
        for value in row:
            if _finite(value, 'height') <= 0:
                raise ValueError('Every height and stencil sample must be positive; no fill')


def _grid(grid):
    width, height = grid['width'], grid['height']
    if (type(width) is not int or type(height) is not int
            or not 0 < width <= 128 or not 0 < height <= 128
            or width * height > 4096 or grid['crs'] != CRS):
        raise ValueError('Expected a bounded EPSG:25830 domain')
    transform = grid['transform']
    if not isinstance(transform, (list, tuple)) or len(transform) != 6:
        raise ValueError('Expected a six-value affine transform')
    transform = tuple(_finite(value, 'affine') for value in transform)
    if transform[1] != 0 or transform[3] != 0:
        raise ValueError('Rotated or sheared terrain is unsupported')
    if (transform[0] != 500 or transform[4] != -500
            or _finite(grid['cell_km'], 'cell_km') != 0.5):
        raise ValueError('Expected the north-up 500 m domain grid')
    west, north = transform[2], transform[5]
    extent = [west, north - height * 500, west + width * 500, north]
    if not all(math.isfinite(value) for value in extent):
        raise ValueError('Nonfinite terrain extent')
    return width, height, transform, extent


def crop_samples(samples, grid):
    """Return a new 25 m crop plus halo from the 860-row, 780-column ASC.

    The source northwest lattice origin is (296500, 4055000). Thus the parent
    (297000, 4054500) starts at row/column 20, before adding crop offsets.
    The active 37-column, 36-row domain selects rows 19:741, columns 19:761;
    removing its halo yields 740 by 720 pixels. Outside-crop unknowns are unused.
    """
    if _shape(samples) != (860, 780):
        raise ValueError('Expected the pinned 860 by 780 source lattice')
    width, height, transform, _ = _grid(grid)
    column_offset = (transform[2] - 296500) / 25
    row_offset = (4055000 - transform[5]) / 25
    if not column_offset.is_integer() or not row_offset.is_integer():
        raise ValueError('Domain must align exactly with the source lattice')
    left, top = int(column_offset), int(row_offset)
    right, bottom = left + width * 20, top + height * 20
    if left < 1 or top < 1 or right >= 780 or bottom >= 860:
        raise ValueError('Source does not contain the full crop and one-sample halo')
    window = tuple(tuple(row[left - 1:right + 1]) for row in samples[top - 1:bottom + 1])
    _positive_heights(window)
    return window


def terrain_rgb(elevations, *, cell_size_metres, halo=1):
    """Return deterministic north-first RGB terrain without modifying heights."""
    return _terrain_pixels(elevations, cell_size_metres=cell_size_metres, halo=halo)


def terrain_relief(elevations, *, cell_size_metres, halo=1):
    """Transparent feathered hillshade; exaggeration changes lighting, not heights."""
    return _terrain_pixels(elevations, cell_size_metres=cell_size_metres, halo=halo,
                           relief=True)


def _terrain_pixels(elevations, *, cell_size_metres, halo=1, relief=False):
    """Return (width, height, RGB or relief RGBA bytes), without input changes.

    Heights and spacing are metres; all samples, including the halo, must be
    positive finite numbers. halo=1 removes a complete Horn stencil border.
    halo=0 retains the coarse domain: Horn inside, supported one-sided edge
    differences at its boundary (a singleton axis has zero gradient).
    Lighting is Lambertian, northwest at 45 degrees, with a fixed 0..2000 m
    pale sage-to-cream tint and ambient light. No global normalization occurs.
    """
    height, width = _shape(elevations)
    spacing = _finite(cell_size_metres, 'cell_size_metres')
    if spacing <= 0 or type(halo) is not int or halo not in (0, 1):
        raise ValueError('Spacing must be positive and halo must be 0 or 1')
    if width <= 2 * halo or height <= 2 * halo:
        raise ValueError('Missing complete one-sample halo')
    _positive_heights(elevations)
    output_width, output_height = width - 2 * halo, height - 2 * halo
    pixels = bytearray()
    for row_index in range(halo, height - halo):
        centre = elevations[row_index]
        north = elevations[max(0, row_index - 1)]
        south = elevations[min(height - 1, row_index + 1)]
        for column in range(halo, width - halo):
            left, right = max(0, column - 1), min(width - 1, column + 1)
            if 0 < row_index < height - 1 and 0 < column < width - 1:
                column_gradient = ((north[right] - north[left]) / 8
                                   + (centre[right] - centre[left]) / 4
                                   + (south[right] - south[left]) / 8) / spacing
                row_gradient = ((south[left] - north[left]) / 8
                                + (south[column] - north[column]) / 4
                                + (south[right] - north[right]) / 8) / spacing
            else:
                column_gradient = ((centre[right] - centre[left]) / max(1, right - left)
                                   / spacing)
                row_span = min(height - 1, row_index + 1) - max(0, row_index - 1)
                row_gradient = ((south[column] - north[column]) / max(1, row_span)
                                / spacing)
            if not math.isfinite(column_gradient) or not math.isfinite(row_gradient):
                raise ValueError('Nonfinite terrain gradient')
            if relief:
                column_gradient *= 2.5
                row_gradient *= 2.5
            normal_length = math.hypot(column_gradient, row_gradient, 1)
            if not math.isfinite(normal_length):
                raise ValueError('Nonfinite terrain normal')
            light = max(0, 0.5 * (column_gradient / normal_length)
                        + 0.5 * (row_gradient / normal_length)
                        + math.sqrt(0.5) / normal_length)
            if relief:
                horizontal = min(column - halo, width - halo - 1 - column)
                vertical = min(row_index - halo, height - halo - 1 - row_index)
                feather = min(1, horizontal / max(1, output_width * 0.08),
                              vertical / max(1, output_height * 0.08))
                shade = light - math.sqrt(0.5)
                colour = 255 if shade > 0 else 0
                alpha = round(min(105, abs(shade) * 165) * feather)
                pixels.extend((colour, colour, colour, alpha))
                continue
            brightness = 0.72 + 0.28 * light
            tint = min(1, centre[column] / 2000)
            pixels.extend(round((low + (high - low) * tint) * brightness)
                          for low, high in ((202, 244), (215, 236), (188, 213)))
    return output_width, output_height, bytes(pixels)


def encode_png(width, height, rgb, *, channels=3):
    """Encode bounded row-major RGB/RGBA bytes as deterministic 8-bit PNG."""
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width * height > MAX_SAMPLES or type(rgb) is not bytes
            or type(channels) is not int or channels not in (3, 4)
            or len(rgb) != width * height * channels):
        raise ValueError('Expected bounded dimensions and exactly RGB or RGBA bytes')

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    stride = width * channels
    scanlines = b''.join(b'\x00' + rgb[start:start + stride]
                         for start in range(0, len(rgb), stride))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if channels == 4 else 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanlines, 9)) + chunk(b'IEND', b''))


def _read_source():
    path = Path(__file__).resolve().parents[1] / compiler.TERRAIN_ASCII
    try:
        with path.open('rb') as stream:
            return stream.read(compiler.MAX_ASCII_BYTES + 1)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('Present terrain source is a broken symlink')
        return None


def render_terrain(prepared, domain, *, source_reader=None, relief=False):
    """Return {png: bytes, extent: [W,S,E,N], crs, source_label} for display.

    The optional source_reader() -> bytes | None bypasses filesystem discovery,
    never SHA verification. Errors reading or validating a present source are
    propagated, never converted into fallback. This function has no mutable cache
    and never writes to prepared, domain, or simulator state.
    """
    width, height, transform, extent = _grid(domain['grid'])
    parent_width, parent_height, parent_transform, _ = _grid(prepared['grid'])
    crop = domain['crop']
    if any(type(crop[key]) is not int for key in ('left', 'top', 'width', 'height')):
        raise ValueError('Crop values must be integers')
    left, top = crop['left'], crop['top']
    if (left < 0 or top < 0 or crop['width'] != width or crop['height'] != height
            or left + width > parent_width or top + height > parent_height
            or transform != (500, 0, parent_transform[2] + left * 500,
                             0, -500, parent_transform[5] - top * 500)
            or domain['prepared_inputs_sha256'] != prepared['sha256']):
        raise ValueError('Domain crop/grid does not match the supplied prepared parent')
    elevations = domain['elevations_metres']
    support = domain['height_support_mask']
    if _shape(elevations) != (height, width) or _shape(support) != (height, width):
        raise ValueError('Domain heights/support must match its grid')
    if any(type(value) not in (int, bool) or value != 1 for row in support for value in row):
        raise ValueError('Domain contains unsupported heights; no fill')
    _positive_heights(elevations)
    data = (source_reader or _read_source)()
    renderer = terrain_relief if relief else terrain_rgb
    if data is None:
        pixels = renderer(elevations, cell_size_metres=500, halo=0)
        label = 'Coarse 500 m domain-render fallback (raw ASC absent; display only)'
    else:
        if (type(data) is not bytes or not 0 < len(data) <= compiler.MAX_ASCII_BYTES
                or hashlib.sha256(data).hexdigest() != compiler.INPUT_PINS[compiler.TERRAIN_ASCII]):
            raise ValueError('Present terrain source differs from its pinned SHA-256 or size')
        if ((parent_width, parent_height) != (37, 41)
                or parent_transform != (500, 0, 297000, 0, -500, 4054500)):
            raise ValueError('Raw terrain requires the pinned prepared parent grid')
        samples = crop_samples(compiler.read_ascii(data), domain['grid'])
        pixels = renderer(samples, cell_size_metres=25)
        label = 'IGN MDT05, 25 m sampled ASC; pinned SHA-256 ' + compiler.INPUT_PINS[compiler.TERRAIN_ASCII]
    return dict(png=encode_png(*pixels, channels=4 if relief else 3), extent=extent,
                crs=CRS, source_label=label)
