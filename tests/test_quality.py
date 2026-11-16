from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from affine import Affine
from rasterio.warp import transform as warp_points

from dpc_radar import DpcRadarError, Quality, RainGrid, radar_total
from dpc_radar.quality import RECORD_MARGIN, RECORDS, record_ceiling
from tests.conftest import METRES, T0, TMERC, grid, tiff

ROME = (12.5, 42.0)  # the radar sees well here
LIPARI = (14.95, 38.47)  # and here it does not: the beam is too high


def merging(data: np.ndarray, centre: tuple[float, float], name: str = "CUM3") -> RainGrid:
    """A 20x20 «Merging» frame (0.01°) centred on a point."""
    lon, lat = centre
    return RainGrid.from_geotiff(
        tiff(data, Affine(0.01, 0, lon - 0.1, 0, -0.01, lat + 0.1)), name, T0
    )


def radar(value: float, centre: tuple[float, float], size: int = 60) -> RainGrid:
    """A radar-only frame (1 km) of one value, centred on a point."""
    xs, ys = warp_points("EPSG:4326", TMERC, [centre[0]], [centre[1]])
    half = size * 500
    transform = Affine(1000, 0, xs[0] - half, 0, -1000, ys[0] + half)
    return RainGrid.from_geotiff(tiff(np.full((size, size), value), transform, TMERC), "SRT1", T0)


def bump(peak: float) -> np.ndarray:
    """Rain around the middle of a 20x20 frame, falling off as the «Merging» draws it."""
    rows, cols = np.mgrid[0:20, 0:20]
    return np.asarray(peak * np.exp(-((rows - 10) ** 2 + (cols - 10) ** 2) / 18.0))


def test_a_clean_frame_is_kept_whole() -> None:
    frame = grid(np.full((20, 20), 10.0))
    q = frame.quality()
    assert isinstance(q, Quality)
    assert q.usable and q.grid is frame
    assert not q.mask.any()
    assert q.reasons == {"jump": 0, "beyond_record": 0, "outlier": 0, "gauge_only": 0}
    assert (q.score, q.verdict) == (1.0, None)


def test_a_dry_frame_scores_one() -> None:
    assert grid(np.zeros((20, 20))).quality().score == 1.0


def test_beyond_the_record_needs_no_previous_frame(rain: np.ndarray) -> None:
    q = grid(rain).quality()
    assert q.reasons["beyond_record"] == 1
    assert q.mask[5, 5] and q.mask.sum() == 1
    assert q.grid is not None and np.isnan(q.grid.data[5, 5])
    assert q.grid.data[6, 6] == 10.0
    assert q.score == pytest.approx(1 - 1 / 391)  # 400 pixels, 9 without data


def test_record_ceiling() -> None:
    assert record_ceiling(None) is None  # an intensity has no window
    assert record_ceiling(1) == RECORDS[1] * RECORD_MARGIN
    assert record_ceiling(2) == RECORDS[3] * RECORD_MARGIN  # the next window up
    assert record_ceiling(72) == RECORDS[24] * RECORD_MARGIN


def test_an_intensity_has_no_record() -> None:
    data = np.full((20, 20), 2000.0)
    frame = RainGrid.from_geotiff(tiff(data, METRES, TMERC), "SRI", T0)
    assert frame.quality().reasons["beyond_record"] == 0


def test_a_hot_pixel_is_an_outlier_and_a_storm_is_not() -> None:
    data = np.full((20, 20), 2.0)
    data[4, 4] = 90.0  # alone among 2 mm
    rows, cols = np.mgrid[0:20, 0:20]
    storm = 120.0 * np.exp(-((rows - 14) ** 2 + (cols - 14) ** 2) / 18.0)
    q = grid(np.maximum(data, storm)).quality()
    assert q.reasons["outlier"] == 1
    assert q.mask[4, 4] and not q.mask[14, 14]


def test_an_outlier_at_the_edge_of_the_frame() -> None:
    data = np.full((20, 20), 1.0)
    data[0, 0] = 80.0
    assert grid(data).quality().mask[0, 0]


def test_a_rejected_frame_has_no_grid() -> None:
    before = grid(np.full((20, 20), 10.0), time=T0 - timedelta(minutes=30))
    bell = np.full((20, 20), 10.0)
    bell[5:15, 5:15] = 600.0
    q = grid(bell).quality(before)
    assert not q.usable and q.grid is None
    assert (q.verdict, q.score) == ("rejected", 0.0)
    assert q.reasons["jump"] == 100
    assert q.mask.all()


def test_a_few_jumps_are_masked_and_counted() -> None:
    before = grid(np.full((20, 20), 10.0), time=T0 - timedelta(minutes=30))
    now = np.full((20, 20), 10.0)
    now[3, 3] = 400.0
    q = grid(now).quality(before)
    assert q.verdict == "pixels_masked"
    assert q.reasons["jump"] == 1 and q.reasons["outlier"] == 1  # two rules, one pixel
    assert q.mask.sum() == 1 and q.usable


def test_a_frame_in_line_with_the_one_before() -> None:
    before = grid(np.full((20, 20), 10.0), time=T0 - timedelta(minutes=30))
    q = grid(np.full((20, 20), 12.0)).quality(before)
    assert (q.verdict, q.score) == ("consistent", 1.0)


def test_rain_the_radar_did_not_see_is_a_gauge_gone_wrong() -> None:
    data = bump(126.6)  # the stuck gauge of 6-8 October 2026
    q = merging(data, ROME).quality(radar=radar(0.0, ROME))
    assert q.reasons["gauge_only"] == int((data >= 30).sum())
    assert q.reasons["outlier"] == 0
    assert q.grid is not None and np.isnan(q.grid.data[10, 10])
    assert q.grid.data[10, 16] > 1  # under 30 mm the rule does not speak
    assert 0 < q.score < 1


def test_rain_the_radar_saw_too_is_kept() -> None:
    q = merging(bump(126.6), ROME).quality(radar=radar(40.0, ROME))
    assert q.reasons["gauge_only"] == 0 and q.score == 1.0


def test_where_the_radar_is_blind_the_gauge_is_all_we_have() -> None:
    # Lipari, 10 October 2026: 31 mm in the «Merging», nothing on the radar. Real.
    q = merging(bump(31.1), LIPARI).quality(radar=radar(0.0, LIPARI))
    assert q.reasons["gauge_only"] == 0


def test_no_radar_data_is_not_a_dry_radar() -> None:
    data = np.full((20, 20), 60.0)
    off = radar(-9999.0, ROME)  # the radar was off: no data, not zero
    assert merging(data, ROME).quality(radar=off).reasons["gauge_only"] == 0
    away = radar(0.0, LIPARI)  # a radar frame of somewhere else
    assert merging(data, ROME).quality(radar=away).reasons["gauge_only"] == 0


def test_the_gauge_rule_is_for_merging_frames_only() -> None:
    frame = RainGrid.from_geotiff(tiff(np.full((60, 60), 60.0), METRES, TMERC), "SRT1", T0)
    assert frame.quality(radar=radar(0.0, ROME)).reasons["gauge_only"] == 0


def test_radar_total_sums_the_hours() -> None:
    hours = [
        RainGrid.from_geotiff(
            tiff(np.full((20, 20), mm), METRES, TMERC), "SRT1", T0 - timedelta(hours=h)
        )
        for h, mm in enumerate((5.0, 3.0, 1.0))
    ]
    hours[1].data[0, 0] = np.nan
    total = radar_total(hours[::-1])
    assert total.time == T0  # the last hour of the window
    assert total.data[5, 5] == 9.0
    assert np.isnan(total.data[0, 0])  # an hour without data: no total


def test_radar_total_refuses_nothing_and_mixed_grids() -> None:
    with pytest.raises(DpcRadarError, match="no frames"):
        radar_total([])
    a = RainGrid.from_geotiff(tiff(np.zeros((20, 20)), METRES, TMERC), "SRT1", T0)
    b = RainGrid.from_geotiff(tiff(np.zeros((10, 10)), METRES, TMERC), "SRT1", T0)
    with pytest.raises(DpcRadarError, match="different grids"):
        radar_total([a, b])
