from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from affine import Affine

from dpc_radar import DpcRadarError, Event, RainGrid, rare_events, with_merging
from dpc_radar.archive import MergingArchive, path
from dpc_radar.catalogue import CONFIRMED_SHARE
from tests.conftest import METRES, T0, TMERC, tiff
from tests.test_climatology import made

HOUR = timedelta(hours=1)
#: The 10-year level of ``made()``: 45.2 mm in 1 h, twice that in 3 h, three times in 6 h.
LEVEL = 45.2


def srt1(data: np.ndarray, hour: int, transform: Affine = METRES) -> RainGrid:
    """The hourly frame (1 km, mm) that ends ``hour`` hours after T0."""
    return RainGrid.from_geotiff(tiff(data, transform, TMERC), "SRT1", T0 + hour * HOUR)


def cell(left: int = 2, width: int = 4, mm: float = 60.0) -> np.ndarray:
    """A 20x20 frame with rain on rows 2-5, from column ``left``."""
    data = np.zeros((20, 20))
    data[2:6, left : left + width] = mm
    return data


def dry() -> np.ndarray:
    return np.zeros((20, 20))


def found(
    frames: list[RainGrid],
    years: float = 10.0,
    min_area_km2: float = 10.0,
    min_reliability: float = 0.5,
) -> list[Event]:
    return rare_events(
        frames,
        years=years,
        min_area_km2=min_area_km2,
        min_reliability=min_reliability,
        source=made(),
    )


def test_an_hour_of_rare_rain() -> None:
    data = cell()
    data[2, 2] = 80.0
    (e,) = found([srt1(dry(), 0), srt1(dry(), 1), srt1(data, 2), srt1(dry(), 3)])
    assert (e.time, e.window, e.start) == (T0 + 2 * HOUR, 1, T0 + HOUR)
    assert (e.first, e.last) == (e.time, e.time)
    # Not the 80 mm of one pixel: the rain that 10 km² of the storm reached.
    assert (e.mm, e.level_mm, e.area_km2) == (60.0, LEVEL, 16.0)
    assert e.times_level == pytest.approx(60 / LEVEL)
    assert e.return_period == made().rarity(1, e.lon, e.lat, 60.0).return_period
    assert 12.4 < e.lon < 12.5 and 42.0 < e.lat < 42.1
    west, south, east, north = e.bbox
    assert west < e.lon < east and south < e.lat < north
    assert (e.radar_mm, e.merging_mm, e.confirmed) == (None, None, None)


def test_with_a_small_area_the_rarest_pixel_counts() -> None:
    data = cell()
    data[2, 2] = 80.0
    (e,) = found([srt1(data, 0)], min_area_km2=1)
    assert e.mm == 80.0


def test_slow_rain_is_rare_over_three_hours() -> None:
    frames = [srt1(dry(), 0), srt1(dry(), 1)]
    frames += [srt1(cell(mm=35.0), h) for h in (2, 3, 4)]
    frames += [srt1(dry(), 5)]
    (e,) = found(frames)
    assert (e.window, e.time, e.mm) == (3, T0 + 4 * HOUR, 105.0)
    assert e.level_mm == pytest.approx(2 * LEVEL, abs=0.1)
    assert (e.first, e.last) == (e.time, e.time)


def test_a_window_before_the_first_frame_is_not_known() -> None:
    assert found([srt1(cell(mm=35.0), h) for h in (0, 1)]) == []
    (e,) = found([srt1(cell(mm=35.0), h) for h in (0, 1, 2)])
    assert e.window == 3


def test_a_missing_hour_makes_its_windows_unknown() -> None:
    wet = {h: srt1(cell(mm=40.0), h) for h in (2, 3, 4, 5)}
    start = [srt1(dry(), 0), srt1(dry(), 1)]
    (e,) = found([*start, wet[2], wet[3], wet[4]])
    assert e.window == 3
    assert found([*start, wet[2], wet[3], wet[5]]) == []


def test_the_same_place_hour_after_hour_is_one_event() -> None:
    weak, strong = cell(), cell(mm=70.0)
    (e,) = found([srt1(weak, 0), srt1(strong, 1), srt1(weak, 2)])
    assert (e.first, e.last) == (T0, T0 + 2 * HOUR)
    # Rarest as the three hours together: 190 mm against a level of 90.
    assert (e.time, e.window, e.mm) == (T0 + 2 * HOUR, 3, 190.0)


def test_an_hour_without_rare_rain_ends_the_event() -> None:
    frames = [srt1(cell(mm=46.0), 0), srt1(dry(), 1), srt1(dry(), 2), srt1(cell(mm=46.0), 3)]
    first, second = found(frames)
    assert {first.time, second.time} == {T0, T0 + 3 * HOUR}
    late = [srt1(cell(mm=46.0), 0), srt1(cell(mm=46.0), 2)]  # the hour between is missing
    assert len(found(late)) == 2


def test_two_events_that_grow_into_each_other_become_one() -> None:
    apart = cell(2) + cell(9, mm=70.0)
    joined = cell(2, width=11)
    (e,) = found([srt1(apart, 0), srt1(joined, 1)])
    assert (e.first, e.last) == (T0, T0 + HOUR)
    assert (e.time, e.mm) == (T0, 70.0)  # the rarest of the two
    assert e.area_km2 == 44.0
    (e,) = found([srt1(cell(2, mm=70.0) + cell(9), 0), srt1(joined, 1)])
    assert e.mm == 70.0


def test_a_piece_of_a_joined_event_belongs_to_the_whole() -> None:
    tall = dry()
    tall[2:13, 9:13] = 60.0
    below = dry()
    below[9:13, 9:13] = 60.0
    # The top of the tall storm joins the one on its left; its foot goes on alone.
    (e,) = found([srt1(cell(2) + tall, 0), srt1(cell(2, width=11) + below, 1)])
    assert (e.first, e.last) == (T0, T0 + HOUR)


def test_the_rarest_first() -> None:
    data = cell(2) + cell(9, mm=50.0)
    data[10:14, 2:6] = 300.0
    events = found([srt1(data, 0)])
    assert [e.mm for e in events] == [300.0, 60.0, 50.0]
    assert events[0].return_period == 50.0
    assert found([srt1(data, 0)], years=20) and len(found([srt1(data, 0)], years=20)) == 2


def test_what_is_left_out() -> None:
    small = dry()
    small[2:4, 2:4] = 60.0
    assert found([srt1(small, 0)]) == []  # 4 km²
    assert len(found([srt1(small, 0)], min_area_km2=1)) == 1
    assert found([srt1(cell(15), 0)]) == []  # the climatology has no fit there
    inside = np.ones((20, 20), dtype=bool)
    inside[:, :10] = False
    assert rare_events([srt1(cell(), 0)], source=made(), inside=inside) == []
    assert len(rare_events([srt1(cell(), 0)], source=made(), inside=~inside)) == 1
    assert found([]) == []
    assert found([srt1(dry(), 0)]) == []


def test_where_the_radar_does_not_see_there_are_no_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Blind:
        def like(self, grid: RainGrid) -> np.ndarray:
            return np.full(grid.data.shape, 0.2, dtype="float32")

    monkeypatch.setattr("dpc_radar.catalogue.reliability_map", Blind)
    assert found([srt1(cell(), 0)]) == []
    assert len(found([srt1(cell(), 0)], min_reliability=0)) == 1


def test_what_is_refused() -> None:
    sri = RainGrid.from_geotiff(tiff(cell(), METRES, TMERC), "SRI", T0)
    with pytest.raises(DpcRadarError, match="not an hourly"):
        found([sri])
    with pytest.raises(DpcRadarError, match="different grids"):
        found([srt1(cell(), 0), srt1(cell(), 1, Affine(1000, 0, 0, 0, -1000, 0))])
    with pytest.raises(DpcRadarError, match="in order"):
        found([srt1(cell(), 1), srt1(cell(), 0)])
    half = RainGrid.from_geotiff(tiff(cell(), METRES, TMERC), "SRT1", T0 + HOUR / 2)
    with pytest.raises(DpcRadarError, match="hourly"):
        found([srt1(cell(), 0), half])
    with pytest.raises(DpcRadarError, match="return period"):
        found([srt1(cell(), 0)], years=1)
    with pytest.raises(DpcRadarError, match="shape"):
        rare_events([srt1(cell(), 0)], source=made(), inside=np.ones((3, 3), dtype=bool))


def test_the_shipped_climatology_is_the_default() -> None:
    assert rare_events([srt1(dry(), 0)]) == []


def archive_of(folder: Path, merging: float | None, hours: tuple[int, ...] = (1, 2, 3)) -> None:
    """SRT1 frames with the storm in hour 2, and the CUM3 of 09:00 with ``merging`` mm."""
    for h in hours:
        file = path(folder, "SRT1", T0 + h * HOUR)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(tiff(cell() if h == 2 else dry(), METRES, TMERC))
    if merging is not None:
        data = np.full((40, 40), merging)
        file = path(folder, "CUM3", T0 + 3 * HOUR)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(tiff(data, Affine(0.01, 0, 12.3, 0, -0.01, 42.2)))


def test_the_merging_confirms_an_event(tmp_path: Path) -> None:
    (e,) = found([srt1(dry(), 0), srt1(dry(), 1), srt1(cell(), 2)])
    archive_of(tmp_path / "yes", 55.0)
    seen = with_merging(e, MergingArchive(tmp_path / "yes"))
    assert (seen.radar_mm, seen.merging_mm, seen.confirmed) == (60.0, 55.0, True)
    archive_of(tmp_path / "no", CONFIRMED_SHARE * 60.0 - 1)
    seen = with_merging(e, MergingArchive(tmp_path / "no"))
    assert (seen.merging_mm, seen.confirmed) == (29.0, False)


def test_without_the_frames_the_event_stays_as_it_is(tmp_path: Path) -> None:
    (e,) = found([srt1(dry(), 0), srt1(dry(), 1), srt1(cell(), 2)])
    archive_of(tmp_path / "radar", None)
    assert with_merging(e, MergingArchive(tmp_path / "radar")) == e
    archive_of(tmp_path / "short", 55.0, hours=(1, 2))
    assert with_merging(e, MergingArchive(tmp_path / "short")) == e
    archive_of(tmp_path / "blank", -9999.0)
    assert with_merging(e, MergingArchive(tmp_path / "blank")) == e
