from __future__ import annotations

import numpy as np
import pytest
from rasterio.warp import transform as warp_points

from dpc_radar import SITES, RainGrid, reliability, reliability_map
from dpc_radar.reliability import RELIABLE_FROM, nearest_site
from tests.conftest import METRES, T0, TMERC, grid, tiff

LIPARI = (14.95, 38.47)
MELITO = (15.78, 37.92)


def test_lipari_is_not_reliable_and_says_why() -> None:
    # The cloudburst of 10/10/2026 the radar did not see: the beam is too high.
    r = reliability(*LIPARI)
    assert r.reliable is False
    assert r.score is not None and r.score < RELIABLE_FROM
    assert r.score == r.visibility  # the weakest part
    assert r.beam_height is not None and r.beam_height > 3000
    assert r.agreement is None  # the sea around it: no gauges to compare with
    assert r.distance_km > 100


def test_where_the_radar_saw_the_storm_it_is_reliable() -> None:
    r = reliability(*MELITO)
    assert r.reliable is True
    assert r.coverage is not None and r.agreement is not None and r.visibility is not None
    assert r.score == min(r.visibility, r.coverage, r.agreement)


def test_never_seen_is_zero_not_unknown() -> None:
    r = reliability(12.51, 38.02)  # Trapani: outside every range circle
    assert (r.score, r.coverage, r.reliable) == (0.0, 0.0, False)
    assert r.visibility is None and r.beam_height is None


def test_outside_the_grid_nothing_is_known() -> None:
    r = reliability(30.0, 42.0)
    assert r.score is None and r.reliable is None and r.beam_height is None
    assert r.nearest_site == "Salento"


def test_nearest_site() -> None:
    site, km = nearest_site(12.79, 42.86)
    assert site.name == "Monte Serano" and km < 1
    assert site.source == "dpc" and site.band == "C"


def test_sites_are_inside_italy_and_unique() -> None:
    assert len({s.name for s in SITES}) == len(SITES)
    assert all(6 < s.lon < 19 and 36 < s.lat < 48 for s in SITES)
    assert sum(s.source == "dpc" for s in SITES) == 7


def test_map_is_on_the_mosaic_grid() -> None:
    m = reliability_map()
    assert m.score.shape == (1400, 1200)
    assert (m.transform.a, m.transform.c, m.transform.f) == (1000.0, -600000.0, 650000.0)
    assert m.built_on
    known = np.isfinite(m.score)
    assert known.all()  # inside the grid there is always an answer
    assert np.nanmin(m.visibility) >= 0 and np.nanmax(m.agreement) <= 1


@pytest.mark.parametrize("name", ["CUM24", "SRT1"])
def test_score_on_the_pixels_of_a_frame(name: str) -> None:
    data = np.zeros((20, 20))
    frame = (
        grid(data)
        if name == "CUM24"
        else RainGrid.from_geotiff(tiff(data, METRES, TMERC), name, T0)
    )
    score = reliability_map().like(frame)
    assert score.shape == data.shape
    t = frame.transform
    x, y = t.c + t.a * 10.5, t.f + t.e * 10.5  # the centre of pixel (10, 10)
    lons, lats = warp_points(frame.crs, "EPSG:4326", [x], [y])
    expected = reliability(lons[0], lats[0]).score
    assert expected is not None
    assert float(score[10, 10]) == pytest.approx(expected, abs=0.011)
