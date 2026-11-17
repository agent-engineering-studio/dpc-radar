from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from affine import Affine

from dpc_radar import DpcRadarError, RainGrid
from dpc_radar.storms import Storm, Track, storms, track
from tests.conftest import METRES, T0, TMERC, grid, tiff

FIVE = timedelta(minutes=5)


def sri(data: np.ndarray, minutes: int = 0, transform: Affine = METRES) -> RainGrid:
    """An SRI frame (1 km, mm/h) ``minutes`` after T0."""
    return RainGrid.from_geotiff(
        tiff(data, transform, TMERC), "SRI", T0 + timedelta(minutes=minutes)
    )


def cell(top: int, left: int, size: int = 4, rate: float = 30.0) -> np.ndarray:
    """A 40x40 frame with one square storm."""
    data = np.zeros((40, 40))
    data[top : top + size, left : left + size] = rate
    return data


def test_one_storm_and_what_is_said_of_it() -> None:
    data = cell(10, 20)
    data[11, 21] = 80.0
    (s,) = storms(sri(data), threshold=10)
    assert isinstance(s, Storm)
    assert (s.time, s.product, s.threshold) == (T0, "SRI", 10)
    assert s.area_km2 == 16.0
    assert s.peak == 80.0
    assert s.mean == pytest.approx((15 * 30 + 80) / 16)
    # METRES puts the frame around 42°N 12.5°E: the storm is a few km north-east of it.
    assert 12.5 < s.lon < 12.7 and 41.9 < s.lat < 42.1
    # The centre of the rain leans towards the peak, which is up and left of the middle.
    assert s.peak_lon < s.lon and s.peak_lat > s.lat
    west, south, east, north = s.bbox
    assert west < s.peak_lon < east and south < s.peak_lat < north


def test_small_patches_and_weak_rain_are_dropped() -> None:
    data = cell(10, 20) + cell(30, 5, size=2) + cell(2, 2, rate=5.0)
    assert len(storms(sri(data), threshold=10)) == 1
    assert len(storms(sri(data), threshold=10, min_area_km2=1)) == 2
    assert len(storms(sri(data), threshold=1, min_area_km2=1)) == 3
    assert storms(sri(np.zeros((40, 40))), threshold=10) == []
    assert storms(sri(cell(30, 5, size=2)), threshold=10) == []  # only a small one


def test_the_strongest_first_and_corners_touch() -> None:
    data = cell(10, 20) + cell(30, 5, rate=60.0)
    first, second = storms(sri(data), threshold=10)
    assert (first.peak, second.peak) == (60.0, 30.0)
    diagonal = cell(10, 10) + cell(14, 14)
    assert len(storms(sri(diagonal), threshold=10)) == 1


def test_no_data_is_not_a_storm() -> None:
    data = cell(10, 20)
    data[0:5, 0:5] = -9999.0
    (s,) = storms(sri(data), threshold=10)
    assert s.area_km2 == 16.0


def test_area_on_a_lonlat_grid() -> None:
    data = np.zeros((20, 20))
    data[5:15, 5:15] = 40.0
    (s,) = storms(grid(data, "CUM3"), threshold=30)
    # 100 pixels of 0.01° at 38°N: about 1.11 km by 0.88 km each.
    assert s.area_km2 == pytest.approx(97, abs=2)
    assert s.product == "CUM3"


def test_a_storm_is_followed_while_it_moves() -> None:
    frames = [sri(cell(10, 5 + 2 * k), 5 * k) for k in range(6)]
    (t,) = track(frames, threshold=10)
    assert isinstance(t, Track)
    assert len(t.storms) == 6
    assert (t.start, t.end, t.duration) == (T0, T0 + 5 * FIVE, 5 * FIVE)
    assert t.distance_km == pytest.approx(10, abs=0.1)  # 2 km a frame, five times
    assert t.speed_kmh == pytest.approx(24, abs=0.3)
    assert t.heading == pytest.approx(90, abs=1)  # east
    assert (t.peak, t.area_km2) == (30.0, 16.0)
    assert len(t.path) == 6 and t.path[0][0] < t.path[-1][0]
    # 30 mm/h on 16 km² for 25 minutes.
    assert t.water_m3 == pytest.approx(30 * 16 * (25 / 60) * 1000)


def test_a_storm_seen_once() -> None:
    (t,) = track([sri(cell(10, 5))], threshold=10)
    assert t.duration == timedelta(0)
    assert (t.speed_kmh, t.heading, t.water_m3) == (None, None, 0.0)
    assert track([], threshold=10) == []


def test_a_storm_that_stays_has_no_heading() -> None:
    (t,) = track([sri(cell(10, 5), 5 * k) for k in range(3)], threshold=10)
    assert t.heading is None and t.speed_kmh == 0.0


def test_two_storms_keep_their_own_tracks() -> None:
    frames = [sri(cell(5, 5 + k) + cell(30, 30 - k, rate=50.0), 5 * k) for k in range(4)]
    a, b = track(frames, threshold=10)
    assert len(a.storms) == len(b.storms) == 4
    assert {a.peak, b.peak} == {30.0, 50.0}
    assert {round(a.heading or 0), round(b.heading or 0)} == {90, 270}


def test_a_storm_that_jumps_is_another_storm() -> None:
    a, b = track([sri(cell(5, 5)), sri(cell(30, 30), 5)], threshold=10)
    assert len(a.storms) == len(b.storms) == 1


def test_after_a_gap_every_storm_is_new() -> None:
    frames = [sri(cell(10, 5)), sri(cell(10, 5), 5), sri(cell(10, 5), 60)]
    a, b = track(frames, threshold=10)
    assert (len(a.storms), len(b.storms)) == (2, 1)
    (whole,) = track(frames, threshold=10, max_gap=timedelta(hours=1))
    assert len(whole.storms) == 3


def test_a_split_and_a_merge() -> None:
    one = cell(10, 10, size=8)
    two = np.zeros((40, 40))
    two[10:18, 10:15] = 30.0  # the larger piece
    two[10:18, 16:18] = 30.0  # the smaller one, a column apart
    tracks = track([sri(one), sri(two, 5), sri(one, 10)], threshold=10)
    assert [len(t.storms) for t in tracks] == [3, 1]
    assert [s.area_km2 for s in tracks[0].storms] == [64.0, 40.0, 64.0]
    assert tracks[1].storms[0].area_km2 == 16.0


def test_accumulations_have_no_water() -> None:
    frames = [
        RainGrid.from_geotiff(tiff(cell(10, 5), METRES, TMERC), "SRT1", T0 + k * FIVE)
        for k in range(2)
    ]
    (t,) = track(frames, threshold=10)
    assert t.water_m3 is None


def test_what_cannot_be_followed() -> None:
    with pytest.raises(DpcRadarError, match="different grids"):
        track([sri(cell(10, 5)), sri(np.zeros((20, 20)), 5)], threshold=10)
    with pytest.raises(DpcRadarError, match="order of time"):
        track([sri(cell(10, 5), 5), sri(cell(10, 5))], threshold=10)
