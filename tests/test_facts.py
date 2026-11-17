from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pytest

from dpc_radar import DpcRadarError
from dpc_radar import bulletins as b
from dpc_radar import facts as f
from dpc_radar.facts import Facts
from dpc_radar.quality import Quality
from tests.conftest import T0, Radar, grid
from tests.test_bulletins import NAME, TODAY, TOMORROW
from tests.test_nowcast import CENTRE, moving

HALF = timedelta(minutes=30)
#: The centre of the pixel (10, 10) of the 20x20 test grid.
POINT = (15.105, 37.895)


def bulletin() -> b.Bulletin:
    when = b.issued(NAME)
    assert when is not None
    day = when.date()
    zones = b.zones_from_geojson(TODAY, day)
    zones += b.zones_from_geojson(TOMORROW, day + timedelta(days=1))
    return b.Bulletin(name="20261010_1417", issued=when, zones=zones)


def flat(mm: float, name: str = "CUM3", minutes: int = 0) -> Any:
    return grid(np.full((20, 20), mm), name, T0 + timedelta(minutes=minutes))


def test_stamp_and_frame() -> None:
    assert f.stamp(T0) == "2026-10-10T06:00Z"
    assert f.frame_of(flat(1.0)) == {
        "product": "CUM3",
        "time": "2026-10-10T06:00Z",
        "unit": "mm",
        "hours": 3,
        "source": "radar and rain gauges («Merging»)",
    }
    assert f.frame_of(flat(1.0, "SRI"))["unit"] == "mm/h"
    assert f.frame_of(flat(1.0, "SRT1"))["source"] == "radar only"


def test_describe() -> None:
    data = np.full((20, 20), 10.0)
    data[4, 6] = 60.0
    data[:, :10] = -9999.0  # the western half has no data
    data[4, 16] = 60.0
    seen = f.describe(grid(data))
    assert seen["measured_share"] == 0.5
    assert (seen["max_mm"], seen["max_lon"], seen["max_lat"]) == (60.0, 15.165, 37.955)
    assert seen["median_mm"] == 10.0 and seen["mean_mm"] == 10.2
    # One pixel of 0.01° at 38°N is about 0.98 km².
    assert seen["km2_over"]["50"] == pytest.approx(0.98, abs=0.05)
    assert seen["km2_over"]["10"] == pytest.approx(seen["area_km2"] / 2, abs=0.2)
    assert seen["km2_over"]["100"] == 0.0


def test_describe_a_piece_with_no_data() -> None:
    seen = f.describe(grid(np.full((20, 20), -9999.0)), (5,))
    assert seen["measured_share"] == 0.0 and seen["max_mm"] is None and seen["mean_mm"] is None
    assert seen["km2_over"] == {"5": 0.0}


async def test_latest() -> None:
    radar = Radar([flat(1.0, "CUM3"), flat(1.0, "SRI", 15)])
    found = {p["product"]: p for p in (await Facts(radar).latest())["products"]}
    assert list(found) == ["SRI", "SRT1", "CUM3", "CUM6", "CUM12", "CUM24"]
    assert (found["SRI"]["time"], found["SRI"]["every_minutes"]) == ("2026-10-10T06:15Z", 5)
    assert (found["CUM3"]["time"], found["CUM3"]["every_minutes"]) == ("2026-10-10T06:00Z", 30)
    assert (found["CUM24"]["time"], found["CUM24"]["every_minutes"]) == (None, None)


async def test_rain_at_names_its_frame() -> None:
    data = np.full((20, 20), 4.0)
    data[10, 10] = 90.0  # one pixel: the median of the nine around it does not see it
    radar = Radar([grid(data, "CUM3"), flat(4.0, "CUM3", -30)])
    facts = Facts(radar)
    got = await facts.rain_at(*POINT, hours=3)
    assert (got["mm"], got["measured"], got["hours"]) == (4.0, True, 3)
    assert got["frame"]["product"] == "CUM3" and got["frame"]["time"] == "2026-10-10T06:00Z"
    assert got["quality"]["verdict"] == "consistent"
    assert got["quality"]["newest_frame_rejected"] is None
    assert set(got["reliability"]) == {
        "score",
        "reliable",
        "beam_height_m",
        "nearest_radar",
        "distance_km",
    }
    assert got["note"] == f.NOT_OFFICIAL
    asked = len(radar.asked)
    await facts.rain_at(15.05, 37.95, hours=3)
    assert len(radar.asked) == asked  # the frames are kept


async def test_rain_at_where_nothing_is_measured() -> None:
    got = await Facts(Radar([flat(4.0, "SRT1")])).rain_at(2.0, 48.0)
    assert (got["mm"], got["measured"], got["note"]) == (None, False, f.NOT_MEASURED)
    assert got["frame"]["product"] == "SRT1" and got["quality"]["verdict"] is None


async def test_a_rejected_frame_gives_way_to_the_one_before() -> None:
    bad = np.full((20, 20), 4.0)
    bad[5:9, 5:9] = 9000.0
    radar = Radar([grid(bad, "CUM3"), flat(4.0, "CUM3", -30)])
    got = await Facts(radar).rain_at(*POINT, hours=3)
    assert got["mm"] == 4.0 and got["frame"]["time"] == "2026-10-10T05:30Z"
    assert got["quality"]["newest_frame_rejected"] == "2026-10-10T06:00Z"


async def test_what_cannot_be_answered() -> None:
    facts = Facts(Radar([flat(4.0)], newest={"CUM6": T0}))
    with pytest.raises(DpcRadarError, match="not of 2"):
        await facts.rain_at(*POINT, hours=2)
    with pytest.raises(DpcRadarError, match="lists no frame of SRT1"):
        await facts.rain_at(*POINT, hours=1)
    with pytest.raises(DpcRadarError, match=r"CUM6 frame .* is not available"):
        await facts.rain_at(*POINT, hours=6)
    empty = np.zeros((2, 2), dtype=bool)
    with pytest.raises(DpcRadarError, match="fails the quality check"):
        f._good(Quality(None, empty, {}, 0.0, "rejected"), flat(4.0))


async def test_the_cache_forgets_the_oldest_frame() -> None:
    radar = Radar([flat(4.0), flat(4.0, minutes=-30), flat(2.0, "CUM6")])
    facts = Facts(radar, cache_frames=2)
    await facts.rain_at(*POINT, hours=3)
    await facts.rain_at(*POINT, hours=6)  # two more asked, one of them absent
    asked = len(radar.asked)
    await facts.rain_at(*POINT, hours=3)
    assert len(radar.asked) == asked + 2  # the CUM3 frames had been dropped, one by one


async def test_area_summary() -> None:
    rows, cols = np.mgrid[0:20, 0:20]
    data = 4.0 + 86.0 * np.exp(-((rows - 10) ** 2 + (cols - 10) ** 2) / 18.0)  # a smooth storm
    data[3, 16] = 900.0  # one pixel far above its neighbours: the quality check masks it
    facts = Facts(Radar([grid(data, "CUM24")]))
    got = await facts.area_summary(15.05, 37.85, 15.2, 38.0, hours=24)
    assert got["bbox"] == [15.05, 37.85, 15.2, 38.0]
    assert (got["max_mm"], got["max_lon"], got["max_lat"]) == (90.0, 15.105, 37.895)
    assert got["frame"]["product"] == "CUM24"
    assert got["km2_over"]["50"] == pytest.approx(36, abs=2) and got["km2_over"]["100"] == 0.0
    assert got["quality"]["masked_pixels"] == 1 and got["quality"]["reasons"]["outlier"] == 1
    with pytest.raises(DpcRadarError, match="west < east"):
        await facts.area_summary(15.15, 37.85, 15.05, 37.95, hours=24)
    with pytest.raises(DpcRadarError, match="outside the frame"):
        await facts.area_summary(2.0, 48.0, 2.1, 48.1, hours=24)


async def test_storm_now() -> None:
    facts = Facts(Radar(moving(frames=7)))
    got = await facts.storm_now()
    assert got["count"] == 1 and got["bbox"] is None and got["threshold_mm_h"] == 10.0
    assert got["frame"]["product"] == "SRI" and got["frame"]["time"] == "2026-10-10T06:00Z"
    assert got["motion_from"] == "2026-10-10T05:30Z"
    (storm,) = got["storms"]
    assert storm["peak_mm_h"] == pytest.approx(40.0, abs=0.5)
    assert storm["moving"]["kmh"] == pytest.approx(26.8, abs=2.0)
    assert storm["moving"]["towards_degrees"] == pytest.approx(117, abs=5)

    here = await facts.storm_now(storm["lon"] - 0.1, storm["lat"] - 0.1, storm["lon"], storm["lat"])
    assert here["count"] == 1 and len(here["bbox"]) == 4
    away = await facts.storm_now(5.0, 36.0, 5.5, 36.5)
    assert away["count"] == 0 and away["storms"] == []
    with pytest.raises(DpcRadarError, match="all four"):
        await facts.storm_now(west=12.0, east=13.0)


async def test_storm_now_with_one_frame_knows_no_motion() -> None:
    got = await Facts(Radar(moving()[-1:])).storm_now()
    assert got["count"] == 1 and got["motion_from"] is None
    assert got["storms"][0]["moving"] is None


async def test_the_newest_intensity_frame_must_be_there() -> None:
    frames = moving()
    radar = Radar(frames[:-1], newest={"SRI": T0})
    with pytest.raises(DpcRadarError, match="newest SRI frame"):
        await Facts(radar).storm_now()


async def test_rain_ahead_is_a_forecast() -> None:
    facts = Facts(Radar(moving(frames=7)))
    got = await facts.rain_ahead(*CENTRE, minutes=60, mm=5.0)
    assert got["kind"] == "forecast"
    assert (got["issued"], got["until"]) == ("2026-10-10T06:00Z", "2026-10-10T07:00Z")
    assert got["forecast_mm"] is not None and got["forecast_mm"] >= 0
    assert 0 <= got["chance"]["probability"] <= 1 and got["chance"]["mm"] == 5.0
    assert got["moving"]["kmh"] == pytest.approx(26.8, abs=2.0)
    assert "extrapolation" in got["limits"]

    outside = await facts.rain_ahead(2.0, 48.0)
    assert outside["forecast_mm"] is None and outside["chance"]["probability"] is None
    assert outside["moving"] is None


async def test_official_alert() -> None:
    calls = 0

    async def newest() -> b.Bulletin:
        nonlocal calls
        calls += 1
        return bulletin()

    facts = Facts(Radar([]), bulletin=newest)
    at = await facts.official_alert(lon=15.5, lat=38.0)
    assert at["bulletin"] == "20261010_1417" and at["days"] == ["2026-10-10", "2026-10-11"]
    assert at["issued"] == "2026-10-10T14:17+02:00"
    assert at["zones"] == [
        {
            "day": "2026-10-10",
            "zone": "Calabria-Versante tirrenico meridionale",
            "level": 2,
            "colour": "orange",
            "hydrogeological": "orange",
            "hydraulic": "none",
            "thunderstorms": "yellow",
        }
    ]
    town = await facts.official_alert(place=" bagaladi ")
    assert [(z["day"], z["zone"][:6]) for z in town["zones"]] == [
        ("2026-10-10", "Calabr"),
        ("2026-10-10", "Bacini"),
        ("2026-10-11", "Bacini"),
    ]
    named = await facts.official_alert(place="tordino")
    assert [(z["day"], z["level"]) for z in named["zones"]] == [
        ("2026-10-10", 0),
        ("2026-10-11", 3),
    ]
    everywhere = await facts.official_alert()
    assert [(z["day"], z["colour"]) for z in everywhere["zones"]] == [
        ("2026-10-10", "orange"),
        ("2026-10-11", "red"),
    ]
    assert calls == 1  # the bulletin is kept for a quarter of an hour


def test_rarity() -> None:
    got = Facts(Radar([])).rarity(15.785, 37.925, 6, 88.9)
    assert (got["lon"], got["lat"], got["hours"], got["mm"]) == (15.785, 37.925, 6, 88.9)
    assert got["return_period_years"] > 10 and got["max_return_years"] == 50
    assert got["low_years"] <= got["return_period_years"] <= got["high_years"]
    assert got["at_least"] is False
    assert Facts(Radar([])).rarity(15.785, 37.925, 6, 900.0)["at_least"] is True
    assert Facts(Radar([])).rarity(2.0, 48.0, 6, 50.0)["return_period_years"] is None
    assert f.trust(2.0, 48.0)["score"] is None


def test_the_day_of_a_zone() -> None:
    zone = bulletin().zone_at(13.5, 42.5, date(2026, 10, 11))
    assert zone is not None and f.zone_of(zone)["colour"] == "red"
