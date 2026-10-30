"""Standalone extended-grid contract. No adapter to the frozen incident-day engine."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

VERSION = 'wildfire-extended-inputs-v1'
MAX_BYTES = 4_000_000
_LAYERS = frozenset(('surface', 'fuel', 'elevation', 'access'))
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def canonical_sha256(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False, ensure_ascii=True).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matrix(value, height, width, name):
    if (type(value) is not list or len(value) != height or
            any(type(row) is not list or len(row) != width for row in value)):
        raise ValueError(f'invalid {name} dimensions')
    return value


def _freeze(matrix):
    return tuple(tuple(row) for row in matrix)


@dataclass(frozen=True)
class Contract:
    width: int
    height: int
    crs: str
    transform: tuple
    domain: tuple
    surface: tuple
    fuel: tuple
    elevation: tuple
    access: tuple
    fire_support: tuple
    document_sha256: str
    source_pins: tuple

    def access_status(self, cells):
        """Classify a declared cell path; this does not validate a road or route."""
        positions = _positions(cells, self)
        if not positions:
            raise ValueError('empty access path')
        states = [self.access[r][c] if self.domain[r][c] else 'unknown'
                  for c, r in positions]
        if 'blocked' in states:
            return 'blocked'
        if 'unknown' in states:
            return 'unknown'
        return 'accessible'


def load_inputs(value, expected_sha256, *, expected_source_pins, opt_in=False):
    """Validate a complete v1 document against independent caller-supplied pins."""
    if opt_in is not True:
        raise ValueError('extended inputs require explicit opt-in')
    if type(value) is not dict:
        path = Path(value)
        if path.is_symlink():
            raise ValueError('input must not be a symlink')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('input exceeds byte limit')
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON number')))
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('invalid JSON document') from exc
    if len(encoded) > MAX_BYTES:
        raise ValueError('input exceeds byte limit')
    actual = hashlib.sha256(encoded).hexdigest()
    if type(expected_sha256) is not str or not _HASH.fullmatch(expected_sha256) or actual != expected_sha256:
        raise ValueError('document pin mismatch')
    if (type(value) is not dict or set(value) != {'version', 'grid', 'domain_mask', 'surface',
            'fuel', 'elevation', 'access', 'source_pins'} or value['version'] != VERSION):
        raise ValueError('invalid extended input contract')
    pins = value['source_pins']
    if (type(pins) is not dict or set(pins) != _LAYERS or
            any(type(v) is not str or not _HASH.fullmatch(v) for v in pins.values()) or
            type(expected_source_pins) is not dict or pins != expected_source_pins):
        raise ValueError('source pins must match caller pins for all layers')
    grid = value['grid']
    if type(grid) is not dict or set(grid) != {'width', 'height', 'crs', 'transform'}:
        raise ValueError('invalid grid')
    w, h = grid['width'], grid['height']
    t = grid['transform']
    if (type(w) is not int or type(h) is not int or not 1 <= w <= 1000 or
            not 1 <= h <= 1000 or w * h > 200_000 or
            type(grid['crs']) is not str or not grid['crs'].strip() or
            type(t) is not list or len(t) != 6 or not all(_number(x) for x in t) or
            t[0] <= 0 or t[4] >= 0 or t[1] != 0 or t[3] != 0 or t[0] != -t[4]):
        raise ValueError('invalid north-up square-cell grid')
    domain, surface, fuel, elevation, access = (
        _matrix(value[name], h, w, name) for name in ('domain_mask', 'surface', 'fuel', 'elevation', 'access'))
    support = []
    for r in range(h):
        row = []
        for c in range(w):
            d, s, f, e, a = domain[r][c], surface[r][c], fuel[r][c], elevation[r][c], access[r][c]
            if type(d) is not bool:
                raise ValueError('domain mask must contain booleans')
            if type(s) is not str or s not in ('land', 'sea', 'mixed', 'unknown'):
                raise ValueError('invalid surface')
            if f is not None and (type(f) is not int or not 1 <= f <= 10):
                raise ValueError('fuel must be null or a source class 1..10')
            if e is not None and not _number(e):
                raise ValueError('elevation must be null or finite')
            if type(a) is not str or a not in ('accessible', 'blocked', 'unknown'):
                raise ValueError('invalid access')
            if s == 'sea' and (f is not None or e is not None or a == 'accessible'):
                raise ValueError('confirmed sea cannot carry land fuel, elevation or access')
            if a == 'accessible' and (not d or s != 'land'):
                raise ValueError('accessibility requires in-domain confirmed land')
            # Sea is explicitly nonburnable, while all other unsupported cells are uncertain.
            row.append(d and (s == 'sea' or (s == 'land' and f is not None and e is not None)))
        support.append(row)
    return Contract(w, h, grid['crs'], tuple(t), _freeze(domain), _freeze(surface),
                    _freeze(fuel), _freeze(elevation), _freeze(access), _freeze(support),
                    actual, tuple(sorted(pins.items())))


def _positions(cells, contract):
    if not isinstance(cells, (list, tuple, set, frozenset)):
        raise ValueError('cells must be a collection of [column, row] pairs')
    result = set()
    for point in cells:
        if (type(point) not in (list, tuple) or len(point) != 2 or
                any(type(x) is not int for x in point)):
            raise ValueError('invalid cell coordinate')
        c, r = point
        if not (0 <= c < contract.width and 0 <= r < contract.height):
            raise ValueError('cell coordinate outside grid')
        result.add((c, r))
    return result


class Censor:
    """Sticky pre-step censor for a one-cell, eight-neighbour spread stencil."""
    def __init__(self, contract):
        if not isinstance(contract, Contract):
            raise TypeError('validated contract required')
        self._contract = contract
        self._reasons = set()

    @property
    def censored(self):
        return bool(self._reasons)

    @property
    def reasons(self):
        return tuple(sorted(self._reasons))

    def check(self, burning):
        """Check every burning cell and its full spread neighbourhood before a step."""
        contract = self._contract
        try:
            points = _positions(burning, contract)
        except (TypeError, ValueError):
            self._reasons.add('invalid_fire_observation')
            raise
        for c, r in points:
            # A fire reported in nonburnable sea or any unsupported cell is invalid.
            if not contract.fire_support[r][c] or contract.surface[r][c] != 'land':
                self._reasons.add('invalid_burning_cell')
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    x, y = c + dc, r + dr
                    if not (0 <= x < contract.width and 0 <= y < contract.height):
                        self._reasons.add('grid_boundary')
                    elif not contract.fire_support[y][x]:
                        self._reasons.add('unknown_fire_state')
        return not self.censored
