"""Bounded offline Horn diagnostics; no metadata verification or physics approval.

Inputs are rectangular Python lists or tuples with at most MAX_CELLS samples.
Cell sizes are horizontal metres; vertical_scale_metres explicitly converts one
source height unit to metres. Supplying that conversion is an assumption, never
evidence that a raster's height units or datum have been verified. This module
does not read rasters, acquire data, or enable simulator slope coupling.
"""
from dataclasses import dataclass, field
import math


MAX_CELLS = 65_536


@dataclass(frozen=True)
class TerrainGradients:
    """Immutable diagnostics; invalid cells contain None and valid_mask=False.

    gradient_columns and gradient_rows are metres per metre, with positive row
    direction following array order. Row gradients are not geographic northing.
    Every output requires all nine samples, including the stencil's centre.
    """

    gradient_columns: tuple
    gradient_rows: tuple
    slope_percent: tuple
    slope_degrees: tuple
    valid_mask: tuple
    slope_enabled: bool = field(default=False, init=False)
    vertical_units_verified: bool = field(default=False, init=False)
    vertical_datum_verified: bool = field(default=False, init=False)
    physics_approved: bool = field(default=False, init=False)


def _number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be a real number, excluding booleans')
    try:
        return float(value)
    except OverflowError as error:
        raise ValueError(f'{name} exceeds floating-point range') from error


def _positive_number(value, name):
    result = _number(value, name)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return result


def _shape(values, name):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f'{name} must be a nonempty rectangular list or tuple')
    if not isinstance(values[0], (list, tuple)) or not values[0]:
        raise ValueError(f'{name} must have nonempty rows')
    height, width = len(values), len(values[0])
    if height * width > MAX_CELLS:
        raise ValueError(f'{name} exceeds the {MAX_CELLS}-cell diagnostic cap')
    if any(not isinstance(row, (list, tuple)) or len(row) != width for row in values):
        raise ValueError(f'{name} must be rectangular')
    return height, width


def horn3x3(elevations, *, cell_size_x_metres, cell_size_y_metres,
            vertical_scale_metres, valid_mask=None, nodata=None):
    """Compute diagnostic gradients, percent slope and atan slope in degrees.

    All sizes and the source-height-to-metre scale must be finite and positive.
    None, NaN, either infinity, nodata, and false input-mask entries are missing.
    Any missing sample masks all stencil outputs; the outer border stays masked.
    No interpolation, zero filling, resampling, or metadata inference is applied.
    Arrays smaller than 3 by 3, malformed values, or nonfinite arithmetic fail.
    """
    cell_width = _positive_number(cell_size_x_metres, 'cell_size_x_metres')
    cell_height = _positive_number(cell_size_y_metres, 'cell_size_y_metres')
    height_scale = _positive_number(vertical_scale_metres, 'vertical_scale_metres')
    height, width = _shape(elevations, 'elevations')
    if height < 3 or width < 3:
        raise ValueError('elevations must be at least 3 by 3')
    if valid_mask is not None:
        if _shape(valid_mask, 'valid_mask') != (height, width):
            raise ValueError('valid_mask must match elevations shape')
        if any(type(value) is not bool for row in valid_mask for value in row):
            raise ValueError('valid_mask entries must be booleans')
    missing_value = None if nodata is None else _number(nodata, 'nodata')
    samples = []
    for row_index, source_row in enumerate(elevations):
        sample_row = []
        for column_index, value in enumerate(source_row):
            numeric = None if value is None else _number(value, 'elevation')
            missing = (numeric is None or not math.isfinite(numeric)
                       or numeric == missing_value
                       or (valid_mask is not None and not valid_mask[row_index][column_index]))
            sample_row.append(None if missing else numeric)
        samples.append(sample_row)
    column_gradients = [[None] * width for row_index in range(height)]
    row_gradients = [[None] * width for row_index in range(height)]
    percentages = [[None] * width for row_index in range(height)]
    degrees = [[None] * width for row_index in range(height)]
    output_mask = [[False] * width for row_index in range(height)]
    for row_index in range(1, height - 1):
        for column_index in range(1, width - 1):
            window = [row[column_index - 1:column_index + 2]
                      for row in samples[row_index - 1:row_index + 2]]
            if any(value is None for row in window for value in row):
                continue
            top_left, top_middle, top_right = window[0]
            middle_left, centre, middle_right = window[1]
            bottom_left, bottom_middle, bottom_right = window[2]
            column_difference = ((top_right - top_left) / 8
                                 + (middle_right - middle_left) / 4
                                 + (bottom_right - bottom_left) / 8)
            row_difference = ((bottom_left - top_left) / 8
                              + (bottom_middle - top_middle) / 4
                              + (bottom_right - top_right) / 8)
            column_gradient = column_difference / cell_width * height_scale
            row_gradient = row_difference / cell_height * height_scale
            magnitude = math.hypot(column_gradient, row_gradient)
            percent = magnitude * 100
            angle = math.degrees(math.atan(magnitude))
            if not all(math.isfinite(value) for value in
                       (column_gradient, row_gradient, magnitude, percent, angle)):
                raise ValueError('Gradient arithmetic exceeds finite floating-point range')
            column_gradients[row_index][column_index] = column_gradient
            row_gradients[row_index][column_index] = row_gradient
            percentages[row_index][column_index] = percent
            degrees[row_index][column_index] = angle
            output_mask[row_index][column_index] = True
    return TerrainGradients(*(tuple(tuple(row) for row in values) for values in
                              (column_gradients, row_gradients, percentages, degrees, output_mask)))
