from __future__ import annotations

import math

import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as warp_points

from dpc_radar import (
    Climatology,
    DpcRadarError,
    RainGrid,
    climatology,
    rarity,
    return_level,
    return_period,
)
from dpc_radar.climatology import BANDS, MAX_RETURN_YEARS, WINDOWS
from tests.conftest import T0, TMERC, grid, tiff

GENOA = (8.93, 44.41)  # where it rains hard
BARI = (16.87, 41.12)  # where it does not
TRAPANI = (12.51, 38.02)  # outside every range circle


def made(shape: float = 0.1, known: bool = True) -> Climatology:
    """A climatology of 4x4 cells of 5 km around 42°N 12.5°E, the same everywhere.

    Gumbel-like: location 20 mm and scale 10 mm for 1 h, twice that for 3 h, ...
    The last column has percentiles but no fit; the last row has nothing.
    """
    parts: dict[int, dict[str, np.ndarray]] = {}
    for n, window in enumerate(WINDOWS):
        values = {
            "loc": 20.0 * (n + 1),
            "scale": 10.0 * (n + 1),
            "shape": shape,
            "years": 16.0,
            "p90": 0.0,
            "p99": 2.0,
            "p99.9": 10.0,
            "p99.99": 30.0,
            "low2": 0.9,
            "low50": 0.7,
            "high2": 1.1,
            "high50": 1.5,
        }
        parts[window] = {name: np.full((4, 4), values[name]) for name in BANDS}
        for name in BANDS:
            if not name.startswith("p"):
                parts[window][name][:, 3] = np.nan
            if not known:
                parts[window][name][:] = np.nan
            parts[window][name][3, :] = np.nan
    transform = Affine(5000, 0, -10_000, 0, -5000, 10_000)
    return Climatology(parts, transform, CRS.from_string(TMERC), "2018/2025")


def lonlat(x: float, y: float) -> tuple[float, float]:
    lons, lats = warp_points(TMERC, "EPSG:4326", [x], [y])
    return float(lons[0]), float(lats[0])


CENTRE = lonlat(-7_000, 7_000)


def test_the_level_of_a_return_period() -> None:
    c = made()
    two = c.level(1, *CENTRE, 2)
    fifty = c.level(1, *CENTRE, 50)
    assert two is not None and fifty is not None
    expected = 20 + 10 / 0.1 * ((-math.log(0.5)) ** -0.1 - 1)
    assert two.mm == pytest.approx(expected, abs=0.05)
    assert two.low == pytest.approx(0.9 * two.mm, abs=0.1)
    assert two.high == pytest.approx(1.1 * two.mm, abs=0.1)
    assert fifty.mm > two.mm
    assert fifty.low == pytest.approx(0.7 * fifty.mm, abs=0.1)
    assert fifty.high == pytest.approx(1.5 * fifty.mm, abs=0.1)
    ten = c.level(1, *CENTRE, 10)
    assert ten is not None and 0.7 < ten.low / ten.mm < 0.9  # between the two ends


def test_rarity_is_the_level_read_backwards() -> None:
    c = made()
    for years in (2.0, 10.0, 50.0):
        at = c.level(3, *CENTRE, years)
        assert at is not None
        r = c.rarity(3, *CENTRE, at.mm)
        assert r.return_period == pytest.approx(years, rel=0.02)
        assert r.low is not None and r.high is not None and r.return_period is not None
        assert r.low <= r.return_period <= r.high
        assert r.years == 16
    r = c.rarity(3, *CENTRE, c.level(3, *CENTRE, 10).mm)  # type: ignore[union-attr]
    assert r.low is not None and r.high is not None and r.low < 10 < r.high


def test_ordinary_rain_comes_every_year_and_a_flood_stops_at_the_ceiling() -> None:
    c = made()
    assert c.rarity(1, *CENTRE, 0.0).return_period == 1.0
    assert c.rarity(1, *CENTRE, 5.0).return_period == pytest.approx(1.0, abs=0.05)
    flood = c.rarity(1, *CENTRE, 5000.0)
    assert (flood.return_period, flood.low, flood.high) == (MAX_RETURN_YEARS,) * 3


@pytest.mark.parametrize("shape", [-0.1, 0.3])
def test_a_short_and_a_heavy_tail(shape: float) -> None:
    c = made(shape)
    assert c.rarity(1, *CENTRE, 5000.0).return_period == MAX_RETURN_YEARS
    assert c.rarity(1, *CENTRE, -1000.0).return_period == 1.0
    at = c.level(1, *CENTRE, 20)
    assert at is not None
    assert c.rarity(1, *CENTRE, at.mm).return_period == pytest.approx(20, rel=0.02)


def test_percentile() -> None:
    c = made()
    assert c.rarity(1, *CENTRE, 2.0).percentile == pytest.approx(99.0)
    assert c.rarity(1, *CENTRE, 10.0).percentile == pytest.approx(99.9)
    between = c.rarity(1, *CENTRE, 5.0).percentile
    assert between is not None and 99.0 < between < 99.9
    assert c.rarity(1, *CENTRE, 0.0).percentile == pytest.approx(90.0)  # 90 or less
    assert c.rarity(1, *CENTRE, 500.0).percentile == pytest.approx(99.99)  # that or more


def test_where_there_are_percentiles_but_no_fit() -> None:
    c = made()
    edge = lonlat(7_000, 7_000)
    r = c.rarity(1, *edge, 10.0)
    assert r.percentile == pytest.approx(99.9)
    assert (r.return_period, r.low, r.high, r.years) == (None, None, None, 0)
    assert c.level(1, *edge, 10) is None


def test_where_there_is_nothing() -> None:
    c = made()
    for point in (lonlat(-7_000, -7_000), (30.0, 42.0)):  # no data; outside the grid
        r = c.rarity(1, *point, 10.0)
        assert (r.return_period, r.percentile, r.years) == (None, None, 0)
        assert c.level(1, *point, 10) is None


def test_what_is_refused() -> None:
    c = made()
    with pytest.raises(DpcRadarError, match="no climatology for 12 h"):
        c.rarity(12, *CENTRE, 10.0)
    for years in (1, 0.5, 500):
        with pytest.raises(DpcRadarError, match="return period"):
            c.level(1, *CENTRE, years)


def test_the_return_period_of_a_frame() -> None:
    c = made()
    data = np.full((20, 20), 5.0)
    data[0:5, 0:5] = c.level(1, *CENTRE, 10).mm  # type: ignore[union-attr]
    data[10, 10] = -9999.0
    frame = RainGrid.from_geotiff(
        tiff(data, Affine(1000, 0, -10_000, 0, -1000, 10_000), TMERC), "SRT1", T0
    )
    years = c.like(frame)
    assert years.shape == (20, 20)
    assert years[2, 2] == pytest.approx(10, rel=0.02)
    assert years[7, 7] == pytest.approx(1.0, abs=0.05)
    assert np.isnan(years[10, 10])  # no rain data
    assert np.isnan(years[2, 17]) and np.isnan(years[17, 2])  # no fit


def test_an_intensity_has_no_return_period() -> None:
    frame = RainGrid.from_geotiff(tiff(np.full((20, 20), 5.0)), "SRI", T0)
    with pytest.raises(DpcRadarError, match="intensity"):
        made().like(frame)
    with pytest.raises(DpcRadarError, match="no climatology for 12 h"):
        made().like(grid(np.full((20, 20), 5.0), "CUM12"))


def test_a_sum_of_hourly_frames_says_its_window() -> None:
    c = made()
    day = c.level(24, *CENTRE, 10)
    assert day is not None
    frame = RainGrid.from_geotiff(
        tiff(np.full((20, 20), day.mm), Affine(1000, 0, -10_000, 0, -1000, 10_000), TMERC),
        "SRT1",
        T0,
    )
    assert c.like(frame, 24)[2, 2] == pytest.approx(10, rel=0.02)
    assert c.like(frame)[2, 2] == MAX_RETURN_YEARS  # the same millimetres in one hour


def test_a_tail_with_no_shape() -> None:
    c = made(0.0)  # a Gumbel
    at = c.level(1, *CENTRE, 10)
    assert at is not None
    assert at.mm == pytest.approx(20 - 10 * math.log(-math.log(0.9)), abs=0.05)
    assert c.rarity(1, *CENTRE, at.mm).return_period == pytest.approx(10, rel=0.02)


def test_the_shipped_map() -> None:
    c = climatology()
    assert c.built_on == "2018/2025"
    assert set(c.parts) == set(WINDOWS)
    for window in WINDOWS:
        assert set(c.parts[window]) == set(BANDS)
        assert c.parts[window]["loc"].shape == (280, 240)
    assert (c.transform.a, c.transform.c, c.transform.f) == (5000.0, -600000.0, 650000.0)
    fitted = np.isfinite(c.parts[1]["loc"])
    assert fitted.sum() > 20_000  # Italy and its seas, in cells of 25 km²
    for window in WINDOWS:
        p = c.parts[window]
        assert np.nanmin(p["scale"]) > 0
        assert np.nanmin(p["shape"]) >= -0.1 - 1e-6 and np.nanmax(p["shape"]) <= 0.3 + 1e-6
        assert np.nanmax(p["low50"]) <= 1.0 + 1e-6 <= np.nanmin(p["high50"]) + 2e-6
    # The longer the window, the more rain a return period takes.
    levels = [np.nanmedian(c.parts[w]["loc"]) for w in WINDOWS]
    assert levels == sorted(levels)


def test_the_same_rain_is_rarer_in_bari_than_in_genoa() -> None:
    genoa, bari = rarity(24, *GENOA, 100.0), rarity(24, *BARI, 100.0)
    assert genoa.return_period is not None and bari.return_period is not None
    assert bari.return_period > 2 * genoa.return_period
    here, there = return_level(24, *GENOA, 10), return_level(24, *BARI, 10)
    assert here is not None and there is not None
    assert here.mm > there.mm
    assert here.low < here.mm < here.high


def test_outside_the_mosaic_there_is_no_climatology() -> None:
    assert rarity(3, *TRAPANI, 50.0).return_period is None
    assert return_level(3, *TRAPANI, 10) is None


def test_return_period_of_a_real_grid() -> None:
    xs, ys = warp_points("EPSG:4326", TMERC, [GENOA[0]], [GENOA[1]])
    transform = Affine(1000, 0, xs[0] - 10_000, 0, -1000, ys[0] + 10_000)
    frame = RainGrid.from_geotiff(tiff(np.full((20, 20), 60.0), transform, TMERC), "SRT1", T0)
    years = return_period(frame)
    at = rarity(1, *GENOA, 60.0).return_period
    assert at is not None
    assert float(years[10, 10]) == pytest.approx(at, rel=0.05)
