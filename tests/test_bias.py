from __future__ import annotations

import numpy as np
import pytest
from affine import Affine
from rasterio.warp import transform as warp_points

from dpc_radar import DpcRadarError, RainGrid, bias_map, correct, radar_factor
from dpc_radar.bias import FACTOR_MAX, FACTOR_MIN
from tests.conftest import T0, TMERC, grid, tiff

ROME = (12.5, 41.9)  # the radar reads more than the «Merging» here
FOGGIA_GAUGE = (15.39, 41.25)  # a gauge that reports rain under a dry sky
TYRRHENIAN = (13.0, 40.5)  # open sea: no gauges
TRAPANI = (12.51, 38.02)  # outside every range circle


def radar(data: np.ndarray, centre: tuple[float, float], name: str = "SRT1") -> RainGrid:
    """A radar-only frame (1 km) centred on a point."""
    xs, ys = warp_points("EPSG:4326", TMERC, [centre[0]], [centre[1]])
    half = data.shape[0] * 500
    transform = Affine(1000, 0, xs[0] - half, 0, -1000, ys[0] + half)
    return RainGrid.from_geotiff(tiff(data, transform, TMERC), name, T0)


def test_map_is_on_the_mosaic_grid() -> None:
    m = bias_map()
    assert m.factor.shape == (1400, 1200)
    assert (m.transform.a, m.transform.c, m.transform.f) == (1000.0, -600000.0, 650000.0)
    assert m.built_on
    assert np.nanmin(m.factor) >= FACTOR_MIN and np.nanmax(m.factor) <= FACTOR_MAX
    known = m.factor[np.isfinite(m.factor) & (m.factor != 1.0)]
    assert known.size > 300_000  # the land of Italy
    assert 0.7 < float(np.median(known)) < 0.9  # the radar reads more than the «Merging»


def test_factor_at_a_point() -> None:
    rome = radar_factor(*ROME)
    assert rome is not None and 0.5 < rome < 0.8


def test_where_nothing_can_be_learnt_the_factor_is_one() -> None:
    assert radar_factor(*TYRRHENIAN) == 1.0
    assert radar_factor(*FOGGIA_GAUGE) == 1.0


def test_outside_the_mosaic_there_is_no_factor() -> None:
    assert radar_factor(*TRAPANI) is None
    assert radar_factor(30.0, 42.0) is None  # outside the grid


@pytest.mark.parametrize("name", ["SRT1", "SRI"])
def test_correct_multiplies_a_radar_frame(name: str) -> None:
    data = np.full((20, 20), 10.0)
    data[0, 0] = -9999.0
    frame = radar(data, ROME, name)
    fixed = correct(frame)
    factor = radar_factor(*ROME)
    assert factor is not None
    assert float(fixed.data[10, 10]) == pytest.approx(10.0 * factor, abs=0.3)
    assert np.isnan(fixed.data[0, 0])  # no data stays no data
    assert (fixed.product, fixed.time, fixed.transform) == (
        frame.product,
        frame.time,
        frame.transform,
    )
    assert frame.data[10, 10] == 10.0  # the frame given is not touched


def test_correct_leaves_the_rain_alone_where_there_is_no_factor() -> None:
    frame = radar(np.full((20, 20), 10.0), TRAPANI)
    assert np.array_equal(correct(frame).data, frame.data)


def test_a_merging_frame_is_not_corrected() -> None:
    with pytest.raises(DpcRadarError, match="CUM24"):
        correct(grid(np.full((20, 20), 10.0)))
