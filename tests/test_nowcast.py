from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from affine import Affine

from dpc_radar import DpcRadarError, RainGrid
from dpc_radar.nowcast import Forecast, Motion, chance, motion, nowcast, rain_ahead
from tests.conftest import T0, TMERC, grid, tiff

SIZE = 160
BIG = Affine(1000, 0, -80_000, 0, -1000, 80_000)  # 160 km around 42°N 12.5°E
CENTRE = (12.5, 42.0)


def field(row: float, col: float, peak: float = 40.0, width: float = 8.0) -> np.ndarray:
    """A round storm of ``peak`` mm/h centred on (row, col) of a 160x160 frame."""
    rows, cols = np.mgrid[0:SIZE, 0:SIZE]
    rain = peak * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2 * width**2))
    return np.where(rain >= 0.1, rain, 0.0)


def sri(data: np.ndarray, minutes: int = 0, name: str = "SRI") -> RainGrid:
    return RainGrid.from_geotiff(tiff(data, BIG, TMERC), name, T0 + timedelta(minutes=minutes))


def moving(frames: int = 4, rows: float = 1.0, cols: float = 2.0, step: int = 5) -> list[RainGrid]:
    """A storm that moves ``rows`` down and ``cols`` right every frame; the last frame is at T0."""
    first = -(frames - 1)
    return [sri(field(60 + rows * k, 50 + cols * k), step * k) for k in range(first, 1)]


def where(data: np.ndarray) -> tuple[int, int]:
    r, c = np.unravel_index(np.nanargmax(data), data.shape)
    return int(r), int(c)


def test_the_motion_of_a_storm() -> None:
    m = motion(moving())
    assert isinstance(m, Motion) and m.found
    # Two columns (km) every five minutes to the east, one row to the south.
    assert float(np.median(m.east)) == pytest.approx(24.0, abs=1.5)
    assert float(np.median(m.north)) == pytest.approx(-12.0, abs=1.5)
    got = m.at(*CENTRE)
    assert got is not None
    speed, heading = got
    assert speed == pytest.approx(26.8, abs=2.0)
    assert heading == pytest.approx(116.6, abs=5.0)  # east-south-east
    assert m.at(30.0, 42.0) is None  # outside the frame


def test_the_storm_goes_on_along_its_motion() -> None:
    forecasts = nowcast(moving(), minutes=60)
    assert len(forecasts) == 12
    assert [f.lead for f in forecasts[:2]] == [timedelta(minutes=5), timedelta(minutes=10)]
    for f in forecasts:
        assert isinstance(f, Forecast) and not isinstance(f, RainGrid)
        assert (f.issued, f.unit, f.window, f.threshold) == (T0, "mm/h", None, None)
        assert f.time == T0 + f.lead
    half = forecasts[5]  # 30 minutes: six rows down, twelve columns right of (60, 50)
    r, c = where(half.data)
    assert abs(r - 66) <= 1 and abs(c - 62) <= 1
    assert float(np.nanmax(half.data)) == pytest.approx(40.0, rel=0.1)  # moved, not changed
    hour = forecasts[11]
    r, c = where(hour.data)
    assert abs(r - 72) <= 2 and abs(c - 74) <= 2


def test_what_comes_from_outside_is_not_known() -> None:
    last = nowcast(moving(), minutes=60)[-1]
    assert np.isnan(last.data[80, 0])  # an hour upstream of the left edge is outside
    assert np.isfinite(last.data[80, 100])
    # Nor what comes from a place without data.
    frames = moving()
    hole = frames[-1].data.copy()
    hole[100:120, 100:120] = np.nan
    frames[-1] = RainGrid(frames[-1].product, T0, hole, BIG, frames[-1].crs)
    after = nowcast(frames, minutes=30)[-1]
    assert np.isnan(after.data[116, 122]) and np.isfinite(after.data[40, 40])


def test_sampling_a_forecast() -> None:
    f = nowcast(moving(), minutes=30)[-1]
    here = f.sample([CENTRE, (30.0, 42.0)])
    assert here[0] is not None and here[0] >= 0.0
    assert here[1] is None


def test_the_rain_of_the_next_hour() -> None:
    total = rain_ahead(moving(), minutes=60)
    assert (total.unit, total.lead, total.window) == ("mm", timedelta(hours=1), timedelta(hours=1))
    assert total.time == T0 + timedelta(hours=1)
    # The storm passes: nowhere the whole hour at its peak, but a good part of it on its path.
    assert 8.0 < float(np.nanmax(total.data)) < 40.0
    r, c = where(total.data)
    assert 60 <= r <= 72 and 50 <= c <= 74
    still = [sri(field(60, 50), m) for m in (-10, -5, 0)]
    stay = rain_ahead(still, minutes=60)
    assert float(np.nanmax(stay.data)) == pytest.approx(40.0, rel=0.05)  # 40 mm/h for an hour
    assert where(stay.data) == (60, 50)


def test_the_chance_of_rain() -> None:
    p = chance(moving(), mm=5.0, minutes=60)
    assert (p.unit, p.threshold, p.window) == ("probability", 5.0, timedelta(hours=1))
    known = p.data[np.isfinite(p.data)]
    assert known.min() >= 0.0 and known.max() <= 1.0
    assert p.data[66, 62] > 0.5  # on the path
    assert p.data[20, 140] == 0.0  # far from it
    # Near the path, where the sum itself does not reach 5 mm, there is still some chance.
    total = rain_ahead(moving(), minutes=60).data
    beside = (total < 5.0) & (p.data > 0.0)
    assert beside.any()
    assert np.isnan(p.data[80, 0])  # not known where the forecast is not


def test_no_rain_is_no_motion() -> None:
    dry = [sri(np.zeros((SIZE, SIZE)), m) for m in (-5, 0)]
    m = motion(dry)
    assert not m.found and not m.east.any() and not m.north.any()
    assert float(np.nanmax(nowcast(dry, minutes=10)[-1].data)) == 0.0


def test_only_the_last_half_hour_is_used() -> None:
    # An hour ago the storm was elsewhere and going elsewhere: it must not count.
    frames = [sri(field(120, 120), -60), *moving()]
    assert float(np.median(motion(frames).east)) == pytest.approx(24.0, abs=1.5)


def test_what_is_refused() -> None:
    frames = moving()
    with pytest.raises(DpcRadarError, match="at least two frames"):
        nowcast(frames[-1:])
    with pytest.raises(DpcRadarError, match="intensity"):
        nowcast([sri(field(60, 50), -60, "SRT1"), sri(field(60, 50), 0, "SRT1")])
    with pytest.raises(DpcRadarError, match="order of time"):
        nowcast(frames[::-1])
    with pytest.raises(DpcRadarError, match="different grids"):
        nowcast([frames[0], frames[-1].crop((12.3, 41.8, 12.7, 42.2))])
    with pytest.raises(DpcRadarError, match="too far apart"):
        nowcast([sri(field(60, 50), -20), sri(field(62, 54), 0)])
    with pytest.raises(DpcRadarError, match="in metres"):
        nowcast(
            [
                grid(np.ones((20, 20)), "SRI"),
                grid(np.ones((20, 20)), "SRI", T0 + timedelta(minutes=5)),
            ]
        )
    for minutes in (0, 3, 125):
        with pytest.raises(DpcRadarError, match="120 minutes"):
            nowcast(frames, minutes=minutes)


def test_rain_without_a_pattern_says_nothing() -> None:
    flat = [sri(np.full((SIZE, SIZE), 5.0), m) for m in (-5, 0)]
    assert not motion(flat).found


def test_a_wide_frame_is_read_block_by_block() -> None:
    # 400 km: sixteen blocks, rain in the north-west only. The rest takes its motion.
    wide = Affine(1000, 0, -200_000, 0, -1000, 200_000)
    rows, cols = np.mgrid[0:400, 0:400]

    def at(k: int) -> RainGrid:
        rain = 30.0 * np.exp(-((rows - 96 - k) ** 2 + (cols - 96 - 2 * k) ** 2) / 50.0)
        data = np.where(rain >= 0.1, rain, 0.0)
        return RainGrid.from_geotiff(tiff(data, wide, TMERC), "SRI", T0 + timedelta(minutes=5 * k))

    m = motion([at(k) for k in (-3, -2, -1, 0)])
    assert m.found
    assert float(m.east[96, 96]) == pytest.approx(24.0, abs=1.5)
    assert float(m.east[350, 350]) == pytest.approx(24.0, abs=1.5)
    assert float(m.north[350, 350]) == pytest.approx(-12.0, abs=1.5)
