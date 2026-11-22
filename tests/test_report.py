from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from affine import Affine

from dpc_radar import DpcRadarError, RainGrid
from dpc_radar import bulletins as b
from dpc_radar import report as r
from tests.conftest import LONLAT, TMERC, Radar, tiff
from tests.test_bulletins import NAME, TOMORROW, github
from tests.test_facts import bulletin

DAY = date(2026, 10, 10)
MIDNIGHT = datetime(2026, 10, 10, tzinfo=UTC)
BOX = (15.02, 37.82, 15.18, 37.98)
#: The radar's 1 km grid over the 20x20 test piece of the «Merging» grid.
SOUTH = Affine(1000, 0, 215_000, 0, -1000, -436_000)
PEAK = (15.105, 37.895)  # the centre of the pixel (10, 10) of the «Merging» piece


def frame(name: str, hour: int, rain: float = 0.0, base: float | None = None) -> RainGrid:
    """A frame of ``base`` mm everywhere plus a storm of ``rain`` mm at its heart on PEAK."""
    radar = name == "SRT1"
    shape, transform, crs = ((32, 30), SOUTH, TMERC) if radar else ((20, 20), LONLAT, "EPSG:4326")
    flat = np.full(shape, (2.0 if radar else 6.0) if base is None else base)
    blank = RainGrid.from_geotiff(tiff(flat, transform, crs), name, MIDNIGHT)
    lons, lats = r._lonlat_mesh(blank)
    storm = rain * np.exp(-((lons - PEAK[0]) ** 2 + (lats - PEAK[1]) ** 2) / (2 * 0.03**2))
    return RainGrid.from_geotiff(
        tiff(flat + storm, transform, crs), name, MIDNIGHT + timedelta(hours=hour)
    )


def frames() -> dict[tuple[str, datetime], RainGrid]:
    """A day of 2 mm an hour, with a storm in the hours that end at 04:00, 05:00 and 06:00."""
    radar = [frame("SRT1", h, 20.0 if h in (4, 5, 6) else 0.0) for h in range(25)]
    merging = [frame("CUM3", h, 70.0 if h == 6 else 0.0) for h in range(3, 25, 3)]
    return {(f.product.name, f.time): f for f in [*radar, *merging]}


def reader(held: dict[tuple[str, datetime], RainGrid]) -> r.Read:
    return lambda name, time: held.get((name, time))


def test_days_and_hours() -> None:
    assert r.day_of("2026-10-10") == DAY
    with pytest.raises(DpcRadarError, match="not a day"):
        r.day_of("10/10/2026")
    hours = r.hours_of(DAY)
    assert len(hours) == 24
    assert (hours[0], hours[-1]) == (MIDNIGHT + timedelta(hours=1), MIDNIGHT + timedelta(days=1))


def test_the_facts_of_a_day() -> None:
    report = r.build(reader(frames()), BOX, DAY, bulletins=[bulletin()])
    facts = report.facts
    assert facts["event"] == {
        "day": "2026-10-10",
        "from": "2026-10-10T00:00Z",
        "to": "2026-10-11T00:00Z",
        "bbox": list(BOX),
        "times": "UTC",
    }
    assert facts["frames"]["radar"] == {
        "product": "SRT1",
        "expected": 24,
        "used": 24,
        "missing": [],
        "rejected": [],
        "masked_pixels": 0,
    }
    assert facts["frames"]["merging"]["used"] == 8 and facts["frames"]["merging"]["expected"] == 8
    assert facts["radar"]["complete"] and facts["merging"]["complete"]
    assert facts["merging"]["max_mm"] == 118.0  # 8 x 6 mm, and 70 at the heart of the storm
    assert (facts["merging"]["max_lon"], facts["merging"]["max_lat"]) == PEAK
    assert facts["radar"]["max_mm"] == pytest.approx(108.0, abs=1.0)  # 24 x 2 mm, and 3 x 20
    assert 48.0 <= facts["radar"]["median_mm"] < facts["radar"]["mean_mm"] + 5
    assert facts["radar_over_merging"] == pytest.approx(0.95, abs=0.05)
    assert report.radar is not None and report.merging is not None
    assert report.merging.data.shape != report.radar.data.shape  # each on its own grid

    hourly = facts["hourly"]
    assert len(hourly) == 24 and facts["hourly_threshold_mm"] == 10
    assert hourly[0] == {"time": "2026-10-10T01:00Z", "max_mm": 2.0, "mean_mm": 2.0, "km2_over": 0}
    assert hourly[3]["max_mm"] == pytest.approx(22.0, abs=0.5) and hourly[3]["km2_over"] > 10

    peak = facts["peak"]
    assert (peak["lon"], peak["lat"], peak["of"]) == (*PEAK, "merging")
    assert peak["merging_mm"] == pytest.approx(116.0, abs=2.0)  # the median of nine pixels
    assert peak["radar_mm"] == pytest.approx(106.0, abs=2.0)
    assert len(peak["hourly_radar_mm"]) == 24 and peak["hourly_radar_mm"][0] == 2.0
    wettest = {w["hours"]: w for w in peak["wettest"]}
    assert list(wettest) == [1, 3, 6, 24]
    assert wettest[1]["ended"] == "2026-10-10T04:00Z"
    assert wettest[3]["ended"] == "2026-10-10T06:00Z"
    assert wettest[3]["mm"] == pytest.approx(64.0, abs=2.0)
    assert wettest[24]["mm"] == pytest.approx(106.0, abs=2.0)
    assert wettest[3]["max_return_years"] == 50
    assert "score" in peak["reliability"]

    assert facts["bulletins"] == [
        {
            "bulletin": "20261010_1417",
            "issued": "2026-10-10T14:17+02:00",
            "days_before": 0,
            "zone": {
                "day": "2026-10-10",
                "zone": "Calabria-Versante tirrenico meridionale",
                "level": 2,
                "colour": "orange",
                "hydrogeological": "orange",
                "hydraulic": "none",
                "thunderstorms": "yellow",
            },
        }
    ]


def test_the_text_only_quotes_the_facts(tmp_path: Path) -> None:
    report = r.build(reader(frames()), BOX, DAY, bulletins=[bulletin()])
    text = report.text()
    assert text.startswith("# The rain of 10 October 2026\n")
    assert "**118.0 mm**, at 15.105° E, 37.895° N" in text
    assert "24 SRT1 frames out of 24" in text and "8 CUM3 frames out of 8" in text
    assert "at level **orange** for 10 October 2026" in text
    assert "| 01:00 | 2.0 mm | 2.0 mm | 0.0 km² |" in text
    assert r.check_text(text, report.facts) == []

    written = report.save(tmp_path / "out")
    assert [p.name for p in written] == ["facts.json", "report.md", "map.png", "hourly.png"]
    assert all(p.stat().st_size > 0 for p in written)
    assert json.loads(written[0].read_text()) == report.facts
    assert written[1].read_text() == text


def test_check_text() -> None:
    facts = {"mm": 191.84, "at": "2026-10-10T03:00Z", "km2_over": {"50": 12.0}, "ok": True}
    fine = "On 10 October 2026 at 03:00, 191.8 mm (192 mm) on 12 km² over 50 mm; CUM3, SRT1."
    assert r.check_text(fine, facts) == []
    assert r.check_text("It rained 190 mm, or 191.9, for 4 hours; 1 gauge.", facts) == [
        "190",
        "191.9",
        "4",
        "1",
    ]
    assert r.check_text("Nothing: None, null.", {"a": None, "b": [1.5, [2]]}) == []
    assert r.check_text("1.5 and 2 and 3", {"a": None, "b": [1.5, [2]]}) == ["3"]


def test_a_text_with_a_number_of_its_own_is_not_saved(tmp_path: Path) -> None:
    report = r.build(reader(frames()), BOX, DAY)
    with pytest.raises(DpcRadarError, match="not in the facts: 250, 9999"):
        report.save(tmp_path, text="It rained 250 mm in 9999 hours.")
    assert list(tmp_path.iterdir()) == []
    own = "The radar counted 118.0 mm at the wettest place, on 10 October 2026."
    written = report.save(tmp_path, text=own, images=False)
    assert [p.name for p in written] == ["facts.json", "report.md"]
    assert written[1].read_text() == own


def test_missing_and_rejected_frames_make_the_totals_at_least() -> None:
    held = frames()
    del held["SRT1", MIDNIGHT + timedelta(hours=10)]
    del held["SRT1", MIDNIGHT + timedelta(hours=5)]  # the CUM3 of 06:00 loses its comparison
    del held["CUM3", MIDNIGHT + timedelta(hours=12)]
    bad = frame("SRT1", 20).data.copy()
    bad[10:14, 10:14] = 900.0  # sixteen pixels that jump: the frame is rejected
    held["SRT1", MIDNIGHT + timedelta(hours=20)] = replace(frame("SRT1", 20), data=bad)
    other = RainGrid.from_geotiff(
        tiff(np.full((40, 40), 6.0), Affine(0.005, 0, 15.0, 0, -0.005, 38.0)),
        "CUM3",
        MIDNIGHT + timedelta(hours=18),
    )
    held["CUM3", other.time] = other  # a frame on another grid cannot be summed
    spike = frame("CUM3", 21).data.copy()
    spike[3, 3] = 2000.0  # beyond any record: the pixel is masked, the frame is kept
    held["CUM3", MIDNIGHT + timedelta(hours=21)] = RainGrid.from_geotiff(
        tiff(spike), "CUM3", MIDNIGHT + timedelta(hours=21)
    )

    report = r.build(reader(held), BOX, DAY)
    radar, merging = report.facts["frames"]["radar"], report.facts["frames"]["merging"]
    assert radar["used"] == 21
    assert radar["missing"] == ["2026-10-10T05:00Z", "2026-10-10T10:00Z"]
    assert radar["rejected"] == ["2026-10-10T20:00Z"]
    assert merging["used"] == 6 and merging["missing"] == ["2026-10-10T12:00Z"]
    assert merging["rejected"] == ["2026-10-10T18:00Z"] and merging["masked_pixels"] == 1
    assert not report.facts["radar"]["complete"] and not report.facts["merging"]["complete"]
    assert report.facts["radar_over_merging"] is None
    peak = report.facts["peak"]
    assert peak["hourly_radar_mm"][4] is None and peak["hourly_radar_mm"][19] is None
    # No window of 24 hours is whole, and none of 6 holds the storm.
    assert [w["hours"] for w in peak["wettest"]] == [1, 3, 6]
    assert report.facts["hourly"][9]["max_mm"] is None

    text = report.text()
    assert "these totals are **at least**" in text
    assert "| 10:00 | no frame | | |" in text
    assert "Missing: 05:00 of 10 October 2026, 10:00 of 10 October 2026." in text
    assert "Rejected by the quality check: 20:00 of 10 October 2026." in text
    assert "masked 1 pixel readings" in text
    assert "No criticality bulletin was read" in text
    assert r.check_text(text, report.facts) == []


def test_the_radar_alone_when_there_is_no_merging(tmp_path: Path) -> None:
    held = {k: v for k, v in frames().items() if k[0] == "SRT1"}
    del held["SRT1", MIDNIGHT]  # the hour before the day: the first frame goes unchecked
    report = r.build(reader(held), BOX, DAY)
    assert report.merging is None and report.facts["merging"] is None
    assert report.facts["frames"]["merging"]["used"] == 0
    assert len(report.facts["frames"]["merging"]["missing"]) == 8
    assert report.facts["peak"]["of"] == "radar" and report.facts["peak"]["merging_mm"] is None
    text = report.text()
    assert "**The «Merging»**: no frame of the day could be used." in text
    written = report.save(tmp_path)
    assert len(written) == 4


def test_a_day_with_no_frame_or_no_data(tmp_path: Path) -> None:
    with pytest.raises(DpcRadarError, match="no frame of 2026-10-10"):
        r.build(reader({}), BOX, DAY)
    held = {
        (name, time): frame(name, time.hour or 24, base=-9999.0)
        for name, time in frames()
        if name == "SRT1" and time != MIDNIGHT
    }
    report = r.build(reader(held), BOX, DAY, bulletins=[bulletin()])
    assert report.facts["peak"] is None and report.facts["bulletins"] == []
    assert report.facts["radar"]["max_mm"] is None
    text = report.text()
    assert "No pixel of the box has data." in text and "## The wettest place" not in text
    assert [p.name for p in report.save(tmp_path)] == ["facts.json", "report.md", "map.png"]


def test_bulletins_that_do_not_speak_of_the_place_or_of_the_day() -> None:
    when = b.issued(NAME)
    assert when is not None
    elsewhere = b.Bulletin(
        name="20261009_1500",
        issued=when - timedelta(days=1),
        zones=b.zones_from_geojson(TOMORROW, DAY),  # one zone, far from the box
    )
    old = b.Bulletin(
        "20261001_1500",
        when - timedelta(days=9),
        b.zones_from_geojson(TOMORROW, DAY - timedelta(days=9)),
    )
    report = r.build(reader(frames()), BOX, DAY, bulletins=[bulletin(), old, elsewhere])
    found = report.facts["bulletins"]
    assert [x["bulletin"] for x in found] == ["20261009_1500", "20261010_1417"]
    assert found[0]["zone"] is None and found[0]["days_before"] == 1
    text = report.text()
    assert "issued on 9 October 2026 at 14:17 (Italian time) has no zone" in text
    assert r.check_text(text, report.facts) == []


def test_the_words_for_what_is_rare_and_what_is_not_known() -> None:
    report = r.build(reader(frames()), BOX, DAY)
    facts = copy.deepcopy(report.facts)
    rows = facts["peak"]["wettest"]
    rows[0] |= {"return_period_years": None, "at_least": False}
    rows[1] |= {"return_period_years": 50.0, "at_least": True}
    rows[2] |= {"return_period_years": 1.0, "at_least": False}
    rows[3] |= {
        "return_period_years": 12.4,
        "low_years": 6.1,
        "high_years": 31.8,
        "at_least": False,
    }
    facts["peak"]["reliability"] |= {"score": 0.2, "reliable": False}
    facts["merging"]["measured_share"] = 0.8
    text = r.Report(facts, None, None).text()
    assert "| not known here |" in text and "| 50 years or more |" in text
    assert "| seen every year |" in text and "| about 12 years (between 6 and 32) |" in text
    assert "a radar reading cannot be taken at face value" in text
    assert "Only a share of 0.8 of the box has data." in text
    assert r.check_text(text, facts) == []

    facts["peak"]["reliability"]["score"] = None
    facts["peak"]["wettest"] = []
    text = r.Report(facts, None, None).text()
    assert "How far to trust" not in text and "Return period" not in text


def test_the_images_need_matplotlib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = r.build(reader(frames()), BOX, DAY)
    monkeypatch.setitem(sys.modules, "matplotlib.figure", None)
    with pytest.raises(DpcRadarError, match=r"dpc-radar\[report\]"):
        report.save(tmp_path)


async def test_download_asks_the_api_for_the_frames_of_the_day() -> None:
    held = frames()
    del held["CUM3", MIDNIGHT + timedelta(hours=9)]
    radar = Radar(list(held.values()))
    read = await r.download(radar, DAY)
    assert len(radar.asked) == 33  # 25 hours of SRT1, eight CUM3
    assert read("SRT1", MIDNIGHT) is not None and read("SRT1", MIDNIGHT + timedelta(days=1))
    assert read("CUM3", MIDNIGHT + timedelta(hours=9)) is None
    assert r.build(read, BOX, DAY).facts["frames"]["merging"]["used"] == 7


async def test_the_bulletins_of_a_day() -> None:
    async with httpx.AsyncClient(transport=github()) as client:
        found = await r.bulletins_for(DAY, client=client)
        assert [x.name for x in found] == ["20261010_1417"]
        assert [x.name for x in await r.bulletins_for(DAY + timedelta(days=1), client=client)] == [
            "20261010_1417"
        ]
        assert await r.bulletins_for(DAY + timedelta(days=2), client=client) == []
    assert b.name_on(["20261010_0900_today.json", NAME], DAY) == NAME


def test_the_template_says_every_number_of_its_own_fact() -> None:
    report = r.build(reader(frames()), BOX, DAY, bulletins=[bulletin()])
    text = report.text()
    assert r.check_claims(text, report.facts) == []
    peak = report.facts["peak"]
    merging, radar = f"{peak['merging_mm']} mm", f"{peak['radar_mm']} mm"
    assert merging != radar
    # The two totals of the wettest place, swapped: both numbers are in the facts.
    swapped = (
        text.replace(f"gives **{merging}** in", "gives **@M** in")
        .replace(f"gives **{radar}**.", f"gives **{merging}**.")
        .replace("@M", radar)
    )
    assert r.check_text(swapped, report.facts) == []
    found = r.check_claims(swapped, report.facts)
    assert [m.key for m in found] == ["peak.merging_mm", "peak.radar_mm"]
    assert found[0].fact == peak["merging_mm"]
    assert str(found[0]).endswith(f"peak.merging_mm is {peak['merging_mm']}")


def test_misquotes_in_the_totals_the_tables_and_the_frames() -> None:
    report = r.build(reader(frames()), BOX, DAY)
    text = report.text()
    facts = report.facts
    hour = facts["hourly"][2]
    row = f"| 03:00 | {hour['max_mm']} mm | {hour['mean_mm']} mm | {hour['km2_over']} km² |"
    assert row in text
    wrong = text.replace(
        row, f"| 03:00 | {hour['max_mm'] + 1} mm | {hour['mean_mm']} mm | 9.0 km² |"
    )
    wrong = wrong.replace("- SRT1: 24 of 24 frames used.", "- SRT1: 8 of 24 frames used.")
    first = facts["peak"]["wettest"][0]
    good = f"| 1 | {first['mm']} mm |"
    assert good in text
    wrong = wrong.replace(good, f"| 1 | {round(first['mm'] + 0.1, 1)} mm |")
    total = facts["merging"]
    wrong = wrong.replace(
        f"the mean is {total['mean_mm']} mm and the median {total['median_mm']} mm",
        f"the mean is {total['median_mm']} mm and the median {total['mean_mm']} mm",
        1,
    )
    keys = {m.key for m in r.check_claims(wrong, facts)}
    assert {
        "hourly[03:00].max_mm",
        "hourly[03:00].km2_over",
        "frames.radar.used",
        "peak.wettest[1 hours].mm",
    } <= keys
    if total["mean_mm"] != total["median_mm"]:
        assert {"merging.mean_mm", "merging.median_mm"} <= keys


def test_a_text_in_other_words_is_not_read_by_the_claims() -> None:
    facts = {"peak": {"merging_mm": 118.04, "radar_mm": 95.2}}
    assert r.check_claims("The gauges put 95.2 mm there, the radar 118.0.", facts) == []
    said = "the «Merging» gives 118.0 mm in the day"
    assert r.check_claims(said, facts) == []
    assert [m.key for m in r.check_claims("the «Merging» gives 118.1 mm in the day", facts)] == [
        "peak.merging_mm"
    ]
    # A product the facts do not hold, and an hour that is not in the table.
    odd = "- CUM6: 3 of 4 frames used.\n| 07:00 | 1.0 mm | 1.0 mm | 0.0 km² |"
    assert [m.key for m in r.check_claims(odd, {"frames": {}, "hourly": []})] == [
        "frames[CUM6]",
        "hourly[07:00].max_mm",
        "hourly[07:00].mean_mm",
        "hourly[07:00].km2_over",
    ]
