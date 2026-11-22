"""The report of a day of rain over a box: facts first, then a text that only quotes them.

``dpc-radar report --bbox W,S,E,N --day YYYY-MM-DD --out folder`` writes four
files: ``facts.json`` (every figure, with the frames it came from),
``report.md`` (the text), ``map.png`` (the radar alone next to the «Merging»)
and ``hourly.png`` (the rain at the wettest place, hour by hour). From Python::

    from dpc_radar.archive import MergingArchive
    from dpc_radar.report import build

    report = build(MergingArchive(folder).read, (15.5, 37.8, 16.2, 38.3), date(2026, 10, 10))
    report.facts["peak"]  # where, how much, how rare
    report.save(Path("melito"))

**What is in the facts.** The day is a UTC day. The radar alone is the sum of
its 24 hourly ``SRT1`` frames, each checked against the hour before it
(:meth:`dpc_radar.RainGrid.quality`); the «Merging» is the sum of the eight
``CUM3`` frames of the day, each checked against the radar of its three hours
(a gauge gone wrong). A frame that is missing or rejected is listed, and the
total is then "at least". At the wettest place (the maximum of the «Merging»,
or of the radar when there is none): both totals, the wettest 1, 3, 6 and 24
hours of radar rain and how rare they are there (:func:`dpc_radar.rarity`),
how far the radar can be trusted (:func:`dpc_radar.reliability`), and the
level the official bulletin gave to that place for that day.

**The text never holds a number that is not in the facts.** It is written from
a template, with no language model, and before anything is saved
:func:`check_text` reads it back: every number in it must be one of the
figures of ``facts.json``, to the decimals it is written with. The same check
is there for a text written by someone else, a model included
(``report.save(folder, text=...)``, or ``dpc-radar check report.md
facts.json``): a text with a number of its own is refused. That check knows
that a number exists, not what it is said of; :func:`check_claims` reads the
sentences of the template and ties each number to its fact, so two figures
swapped between the «Merging» and the radar alone are refused too. A sentence
in other words gets only the first check.

The frames come from a local archive (:class:`dpc_radar.archive.MergingArchive`)
or, for the last five months, from the API (:func:`download`). The images need
matplotlib: ``pip install "dpc-radar[report]"``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar import bulletins
from dpc_radar.bulletins import Bulletin
from dpc_radar.client import Absent
from dpc_radar.errors import DpcRadarError
from dpc_radar.facts import Source, describe, rarity_of, stamp, trust, zone_of
from dpc_radar.grid import BBox, RainGrid
from dpc_radar.quality import radar_total

#: ``read(product, time)``: one frame, or ``None`` if there is none.
Read = Callable[[str, datetime], RainGrid | None]

#: The thresholds (mm in the day) the areas are counted over.
THRESHOLDS: Final = (20, 50, 100, 200)
#: The threshold (mm in an hour) of the hourly table.
HOURLY_MM: Final = 10
#: The windows (hours) whose wettest sum is compared with the climatology.
WINDOWS: Final = (1, 3, 6, 24)

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\w)")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def day_of(text: str) -> date:
    """``2026-10-10`` as a date; :class:`DpcRadarError` if it is not one."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DpcRadarError(f"not a day (YYYY-MM-DD): {text!r}") from None


def hours_of(day: date) -> list[datetime]:
    """The 24 hours that end inside a UTC day: 01:00, 02:00 … 00:00 of the next."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [start + timedelta(hours=h) for h in range(1, 25)]


@dataclass(frozen=True)
class Report:
    """The facts of a day of rain, and the two totals they were read from.

    ``radar`` and ``merging`` are the rain of the day over the box, each on
    its own grid (``None`` when no frame of it could be used).
    """

    facts: dict[str, Any]
    radar: RainGrid | None
    merging: RainGrid | None

    def text(self) -> str:
        """The report in Markdown: only words, and numbers of :attr:`facts`."""
        return _text(self.facts)

    def save(self, folder: Path, *, text: str | None = None, images: bool = True) -> list[Path]:
        """Write ``facts.json``, ``report.md`` and the two images; the paths written.

        ``text`` replaces the text of the template: it goes through
        :func:`check_text` and :func:`check_claims`, and a number that is not
        in the facts, or that is said of a fact that holds another, is a
        :class:`DpcRadarError` with nothing written.
        """
        words = self.text() if text is None else text
        strangers = check_text(words, self.facts)
        if strangers:
            raise DpcRadarError(
                "the text holds numbers that are not in the facts: " + ", ".join(strangers)
            )
        misquotes = check_claims(words, self.facts)
        if misquotes:
            raise DpcRadarError(
                "the text says numbers of the wrong fact: " + "; ".join(map(str, misquotes))
            )
        folder.mkdir(parents=True, exist_ok=True)
        written = [folder / "facts.json", folder / "report.md"]
        written[0].write_text(json.dumps(self.facts, indent=2, ensure_ascii=False) + "\n")
        written[1].write_text(words)
        if images:
            written += _images(self, folder)
        return written


def check_text(text: str, facts: dict[str, Any]) -> list[str]:
    """The numbers of ``text`` that are not in ``facts``, as written (empty: all found).

    A number of the text is found when a figure of the facts, rounded to the
    decimals the text uses, is that number: ``192`` and ``191.8`` both match
    191.84, ``190`` does not. Times and dates count by their parts (``03:00``
    on ``10 October 2026`` matches ``2026-10-10T03:00Z``). Digits inside a
    word (``CUM3``) are not numbers.
    """
    known = set(_figures(facts))
    strangers = []
    for found in _NUMBER.finditer(text):
        token = found.group()
        decimals = len(token.partition(".")[2])
        if not any(round(value, decimals) == float(token) for value in known):
            strangers.append(token)
    return strangers


@dataclass(frozen=True)
class Misquote:
    """A number said of a fact that holds another one.

    ``said`` is the piece of the text, ``key`` the fact it speaks of (a path
    into ``facts.json``: ``peak.merging_mm``, ``hourly[03:00].max_mm``), and
    ``fact`` what the facts hold there (``None`` when nothing).
    """

    said: str
    key: str
    fact: Any

    def __str__(self) -> str:
        return f"«{self.said}»: {self.key} is {self.fact}"


_N = r"(\d+(?:\.\d+)?)"
_HHMM = r"(\d\d:\d\d)"
_PLACE = rf"{_N}° E, {_N}° N"
_SUBJECTS = {"The «Merging»": "merging", "The radar alone": "radar"}
#: The sentences of a total, inside the paragraph that names its product.
_TOTAL_CLAIMS = (
    (rf"the sum of {_N} \w+ frames out of {_N}", ("frames.{s}.used", "frames.{s}.expected")),
    (rf"The maximum is {_N} mm, at {_PLACE}", ("{s}.max_mm", "{s}.max_lon", "{s}.max_lat")),
    (rf"the mean is {_N} mm and the median {_N} mm", ("{s}.mean_mm", "{s}.median_mm")),
    (r"Only a share of " + _N, ("{s}.measured_share",)),
)
#: The sentences that name their own fact.
_CLAIMS = (
    (rf"the radar alone read {_N} times the «Merging»", ("radar_over_merging",)),
    (rf"At {_PLACE}, read as the median", ("peak.lon", "peak.lat")),
    (rf"the «Merging» gives {_N} mm in the day", ("peak.merging_mm",)),
    (rf"the radar alone gives {_N} mm", ("peak.radar_mm",)),
    (rf"its reliability is {_N}", ("peak.reliability.score",)),
    (rf"The nearest radar is [^,]+, {_N} km", ("peak.reliability.distance_km",)),
    (rf"Area at {_N} mm or more", ("hourly_threshold_mm",)),
)


def check_claims(text: str, facts: dict[str, Any]) -> list[Misquote]:
    """The numbers of ``text`` said of the wrong fact (empty: none found).

    :func:`check_text` asks whether a number is in the facts; this asks
    whether it is the number *of what the sentence says*. It reads the
    sentences of the template (:meth:`Report.text`): the totals of each
    product, the wettest place, its hours, the reliability, the hourly table,
    the frames. "The «Merging» gives 95.2 mm" where the facts give 95.2 mm to
    the radar alone is a misquote, and so is a figure rounded the wrong way
    ("118.1" for 118.04). A sentence written in other words is not read: a
    model that rephrases goes through :func:`check_text` only.
    """
    plain = text.replace("**", "")
    out: list[Misquote] = []

    def claim(found: re.Match[str], keys: Iterable[str], first: int = 1) -> None:
        for token, key in zip(found.groups()[first - 1 :], keys, strict=True):
            fact = _at(facts, key)
            if not _same(token, fact):
                out.append(Misquote(found.group().strip(), key, fact))

    for paragraph in re.split(r"\n\s*\n", plain):
        subject = next((s for name, s in _SUBJECTS.items() if paragraph.startswith(name)), None)
        if subject is None or not isinstance(facts.get(subject), dict):
            continue
        for pattern, keys in _TOTAL_CLAIMS:
            for found in re.finditer(pattern, paragraph):
                claim(found, [k.format(s=subject) for k in keys])
        for found in re.finditer(rf"{_N} km² got {_N} mm or more", paragraph):
            fact = _at(facts, f"{subject}.km2_over.{found.group(2)}")
            if not _same(found.group(1), fact):
                out.append(Misquote(found.group(), f"{subject}.km2_over.{found.group(2)}", fact))
    for pattern, keys in _CLAIMS:
        for found in re.finditer(pattern, plain):
            claim(found, keys)
    for found in re.finditer(rf"^- (\w+): {_N} of {_N} frames used", plain, re.M):
        source = next(
            (k for k, f in facts.get("frames", {}).items() if f.get("product") == found.group(1)),
            None,
        )
        if source is None:
            out.append(Misquote(found.group(), f"frames[{found.group(1)}]", None))
        else:
            claim(found, (f"frames.{source}.used", f"frames.{source}.expected"), first=2)
    hourly = {_hhmm(h["time"]): h for h in facts.get("hourly") or [] if isinstance(h, dict)}
    row = rf"^\| {_HHMM} \| {_N} mm \| {_N} mm \| {_N} km² \|"
    for found in re.finditer(row, plain, re.M):
        hour = hourly.get(found.group(1), {})
        for token, part in zip(found.groups()[1:], ("max_mm", "mean_mm", "km2_over"), strict=True):
            if not _same(token, hour.get(part)):
                key = f"hourly[{found.group(1)}].{part}"
                out.append(Misquote(found.group(), key, hour.get(part)))
    peak = facts.get("peak") or {}
    wettest = {w["hours"]: w for w in peak.get("wettest") or [] if isinstance(w, dict)}
    for found in re.finditer(rf"^\| {_N} \| {_N} mm \| {_HHMM} \|", plain, re.M):
        window = wettest.get(int(float(found.group(1))), {})
        key = f"peak.wettest[{found.group(1)} hours]"
        if not _same(found.group(2), window.get("mm")):
            out.append(Misquote(found.group(), f"{key}.mm", window.get("mm")))
        ended = window.get("ended")
        if ended is None or _hhmm(ended) != found.group(3):
            out.append(Misquote(found.group(), f"{key}.ended", ended))
    return out


def _at(facts: Any, key: str) -> Any:
    """The fact at a dotted path; ``None`` when there is none."""
    for part in key.split("."):
        if not isinstance(facts, dict) or part not in facts:
            return None
        facts = facts[part]
    return facts


def _same(token: str, fact: Any) -> bool:
    """Is ``fact`` the number written as ``token``, to the decimals written?"""
    if isinstance(fact, bool) or not isinstance(fact, int | float):
        return False
    decimals = len(token.partition(".")[2])
    return round(float(fact), decimals) == float(token)


def _figures(value: Any) -> Iterator[float]:
    """Every number in a tree of facts: values, keys, and the digits inside strings."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        yield abs(float(value))
    elif isinstance(value, str):
        yield from (float(d) for d in _DIGITS.findall(value))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _figures(key)
            yield from _figures(item)
    else:
        for item in value:
            yield from _figures(item)


def build(read: Read, bbox: BBox, day: date, *, bulletins: Iterable[Bulletin] = ()) -> Report:
    """The report of the rain of ``day`` (UTC) over ``bbox``, from the frames ``read`` gives.

    ``bulletins``: the criticality bulletins to look the wettest place up in
    (:func:`bulletins_for`); those that do not cover the day are left out.
    """
    hours = hours_of(day)
    radar = _Sum("SRT1", len(hours))
    merging = _Sum("CUM3", len(hours) // 3)
    hourly: dict[datetime, RainGrid] = {}
    previous = read("SRT1", hours[0] - timedelta(hours=1))
    last: list[RainGrid | None] = []
    for k, time in enumerate(hours, start=1):
        frame = read("SRT1", time)
        last = [*last[-2:], frame]
        if frame is None:
            radar.missing.append(time)
        else:
            part = radar.add(frame, frame.quality(previous).grid, bbox)
            if part is not None:
                hourly[time] = part
        previous = frame
        if k % 3 == 0:
            block = read("CUM3", time)
            if block is None:
                merging.missing.append(time)
                continue
            three = [f for f in last if f is not None]
            alone = radar_total(three) if len(three) == 3 else None
            merging.add(block, block.quality(radar=alone).grid, bbox)
    if not radar.parts and not merging.parts:
        raise DpcRadarError(f"no frame of {day} to read: neither SRT1 nor CUM3")

    totals = {"radar": radar.total(), "merging": merging.total()}
    facts: dict[str, Any] = {
        "event": {
            "day": day.isoformat(),
            "from": stamp(hours[0] - timedelta(hours=1)),
            "to": stamp(hours[-1]),
            "bbox": list(bbox),
            "times": "UTC",
        },
        "frames": {"radar": radar.frames(), "merging": merging.frames()},
        "radar": _day(totals["radar"], radar),
        "merging": _day(totals["merging"], merging),
        "hourly_threshold_mm": HOURLY_MM,
        "hourly": [_hour(time, hourly.get(time)) for time in hours],
    }
    both = facts["radar"], facts["merging"]
    means = [None if f is None or not f["complete"] else f["mean_mm"] for f in both]
    facts["radar_over_merging"] = (
        round(means[0] / means[1], 2) if means[0] is not None and means[1] else None
    )
    source = "merging" if totals["merging"] is not None else "radar"
    lon, lat = facts[source]["max_lon"], facts[source]["max_lat"]
    facts["peak"] = None if lon is None else _peak(source, lon, lat, totals, hours, hourly)
    facts["bulletins"] = [] if lon is None else _alerts(bulletins, lon, lat, day)
    facts["notes"] = [
        "a point is the median of the 3x3 pixels around it; a maximum is one pixel",
        "the radar alone and the «Merging» are on different grids: 1 km and 0.01 degrees",
        "each total is over the rectangle of its own grid that holds the box, so the two "
        "areas differ a little",
        "radar data, not an alert: the official alert is the DPC bulletin",
    ]
    return Report(facts, totals["radar"], totals["merging"])


class _Sum:
    """The frames of one product over the day: those used, those not, and their sum."""

    def __init__(self, product: str, expected: int) -> None:
        self.product = product
        self.expected = expected
        self.parts: list[RainGrid] = []
        self.missing: list[datetime] = []
        self.rejected: list[datetime] = []
        self.masked = 0

    def add(self, frame: RainGrid, checked: RainGrid | None, bbox: BBox) -> RainGrid | None:
        """The piece of a checked frame over the box, if it can be summed with the others."""
        part = None if checked is None else checked.crop(bbox)
        first = self.parts[0] if self.parts else part
        if part is None or first is None or part.data.shape != first.data.shape:
            self.rejected.append(frame.time)
            return None
        self.masked += int((np.isfinite(frame.crop(bbox).data) & ~np.isfinite(part.data)).sum())
        self.parts.append(part)
        return part

    def total(self) -> RainGrid | None:
        """The sum of the frames used; ``NaN`` where none of them has data."""
        if not self.parts:
            return None
        stack = np.stack([p.data for p in self.parts])
        seen = np.isfinite(stack).any(axis=0)
        data = np.where(seen, np.nansum(stack, axis=0), np.nan).astype("float32")
        return replace(self.parts[-1], data=data)

    def frames(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "expected": self.expected,
            "used": len(self.parts),
            "missing": [stamp(t) for t in self.missing],
            "rejected": [stamp(t) for t in self.rejected],
            "masked_pixels": self.masked,
        }


def _day(total: RainGrid | None, frames: _Sum) -> dict[str, Any] | None:
    if total is None:
        return None
    return {"complete": len(frames.parts) == frames.expected, **describe(total, THRESHOLDS)}


def _hour(time: datetime, part: RainGrid | None) -> dict[str, Any]:
    if part is None:
        return {"time": stamp(time), "max_mm": None, "mean_mm": None, "km2_over": None}
    seen = describe(part, (HOURLY_MM,))
    return {
        "time": stamp(time),
        "max_mm": seen["max_mm"],
        "mean_mm": seen["mean_mm"],
        "km2_over": seen["km2_over"][str(HOURLY_MM)],
    }


def _peak(
    source: str,
    lon: float,
    lat: float,
    totals: dict[str, RainGrid | None],
    hours: list[datetime],
    hourly: dict[datetime, RainGrid],
) -> dict[str, Any]:
    """The wettest place of the day: its totals, its hours, how rare, how trusted."""

    def at(grid: RainGrid | None) -> float | None:
        mm = None if grid is None else grid.sample([(lon, lat)])[0]
        return None if mm is None else round(mm, 1)

    series = [at(hourly.get(time)) for time in hours]
    wettest = []
    for window in WINDOWS:
        sums = [
            (round(sum(piece), 1), hours[k + window - 1])  # type: ignore[arg-type]
            for k in range(len(series) - window + 1)
            if None not in (piece := series[k : k + window])
        ]
        if sums:
            mm, end = max(sums, key=lambda s: s[0])
            wettest.append({"ended": stamp(end), **rarity_of(lon, lat, window, mm)})
    return {
        "lon": lon,
        "lat": lat,
        "of": source,
        "method": "median of the 3x3 pixels around the place of the maximum",
        "radar_mm": at(totals["radar"]),
        "merging_mm": at(totals["merging"]),
        "hourly_radar_mm": series,
        "wettest": wettest,
        "reliability": trust(lon, lat),
    }


def _alerts(found: Iterable[Bulletin], lon: float, lat: float, day: date) -> list[dict[str, Any]]:
    out = []
    for bulletin in sorted(found, key=lambda b: b.issued):
        if day not in bulletin.days():
            continue
        zone = bulletin.zone_at(lon, lat, day)
        out.append(
            {
                "bulletin": bulletin.name,
                "issued": bulletin.issued.isoformat(timespec="minutes"),
                "days_before": (day - bulletin.issued.date()).days,
                "zone": None if zone is None else zone_of(zone),
            }
        )
    return out


async def download(radar: Source, day: date) -> Read:
    """The frames of a day from the API, as a ``read`` for :func:`build`.

    33 requests: the 25 hourly ``SRT1`` frames (the hour before the day
    included) and the eight ``CUM3``. A frame the DPC no longer keeps (older
    than about five months) is simply not there.
    """
    hours = hours_of(day)
    wanted = [("SRT1", t) for t in [hours[0] - timedelta(hours=1), *hours]]
    wanted += [("CUM3", t) for t in hours[2::3]]
    held: dict[tuple[str, datetime], RainGrid] = {}
    for name, time in wanted:
        frame = await radar.at(name, time)
        if not isinstance(frame, Absent):
            held[name, time] = frame
    return lambda name, time: held.get((name, time))


async def bulletins_for(day: date, **http: Any) -> list[Bulletin]:
    """The bulletins that speak of ``day``: the last one of the day before, and of the day.

    ``http``: ``client`` or ``send``, as in :func:`dpc_radar.bulletins.fetch`.
    """
    found = await bulletins.names(**http)
    days = (day - timedelta(days=1), day)
    wanted = [name for d in days if (name := bulletins.name_on(found, d)) is not None]
    return [await bulletins.fetch(name, **http) for name in wanted]


# The text. Every number it writes comes out of the facts, through these.


def _day_words(text: str) -> str:
    day = date.fromisoformat(text[:10])
    return f"{day.day} {day:%B %Y}"


def _hhmm(text: str) -> str:
    return text[11:16]


def _place(lon: float, lat: float) -> str:
    return f"{lon}° E, {lat}° N"


def _times(found: list[str]) -> str:
    return ", ".join(f"{_hhmm(t)} of {_day_words(t)}" for t in found)


def _total_words(name: str, what: str, total: dict[str, Any], frames: dict[str, Any]) -> list[str]:
    sum_of = f"the sum of {frames['used']} {frames['product']} frames out of {frames['expected']}"
    if total["max_mm"] is None:
        return [f"**{name}** ({what}: {sum_of}). No pixel of the box has data.", ""]
    lines = [
        f"**{name}** ({what}: {sum_of}). The maximum is **{total['max_mm']} mm**, at "
        f"{_place(total['max_lon'], total['max_lat'])}. Over the box the mean is "
        f"{total['mean_mm']} mm and the median {total['median_mm']} mm."
    ]
    areas = [f"{km2} km² got {mm} mm or more" for mm, km2 in total["km2_over"].items() if km2 > 0]
    if areas:
        lines.append("Of the box, " + ", ".join(areas) + ".")
    if not total["complete"]:
        lines.append("Some frames are missing or were rejected: these totals are **at least**.")
    if total["measured_share"] < 1:
        lines.append(f"Only a share of {total['measured_share']} of the box has data.")
    return [" ".join(lines), ""]


def _rarity_words(r: dict[str, Any]) -> str:
    years = r["return_period_years"]
    if years is None:
        return "not known here"
    if r["at_least"]:
        return f"{r['max_return_years']} years or more"
    if years <= 1:
        return "seen every year"
    return f"about {years:.0f} years (between {r['low_years']:.0f} and {r['high_years']:.0f})"


def _text(facts: dict[str, Any]) -> str:
    event, peak = facts["event"], facts["peak"]
    west, south, east, north = event["bbox"]
    out = [
        f"# The rain of {_day_words(event['day'])}",
        "",
        f"Box: {west}° to {east}° E, {south}° to {north}° N. The day is the UTC day, from "
        f"{_hhmm(event['from'])} to {_hhmm(event['to'])} of the next; every time below is UTC.",
        "",
        "*Written by dpc-radar from the open radar data of the Italian Civil Protection "
        "(DPC). Every number here is in `facts.json`, with the frames it came from. "
        "This is a reading of radar data: the official alert is the DPC bulletin.*",
        "",
        "## How much it rained",
        "",
    ]
    names = {
        "merging": ("The «Merging»", "radar corrected with the rain gauges"),
        "radar": ("The radar alone", "no rain gauges"),
    }
    for key, (name, what) in names.items():
        if facts[key] is None:
            out += [f"**{name}**: no frame of the day could be used.", ""]
        else:
            out += _total_words(name, what, facts[key], facts["frames"][key])
    if facts["radar_over_merging"] is not None:
        out += [
            f"Over the whole box the radar alone read {facts['radar_over_merging']} times "
            "the «Merging» (the ratio of the two means).",
            "",
        ]
    out += ["![The rain of the day: the radar alone and the «Merging»](map.png)", ""]

    if peak is not None:
        out += ["## The wettest place", ""]
        words = [
            f"At {_place(peak['lon'], peak['lat'])}, read as the median of the nine pixels "
            "around it:"
        ]
        if peak["merging_mm"] is not None:
            words.append(f"the «Merging» gives **{peak['merging_mm']} mm** in the day;")
        if peak["radar_mm"] is not None:
            words.append(f"the radar alone gives **{peak['radar_mm']} mm**.")
        out += [" ".join(words), ""]
        if peak["wettest"]:
            out += [
                "The wettest hours there, in radar rain, and how often that much falls at "
                "that place (the climatology of the radar history):",
                "",
                "| Hours | Radar rain | Ended at | Return period |",
                "|---|---|---|---|",
            ]
            out += [
                f"| {w['hours']} | {w['mm']} mm | {_hhmm(w['ended'])} | {_rarity_words(w)} |"
                for w in peak["wettest"]
            ]
            out.append("")
        trusted = peak["reliability"]
        if trusted["score"] is not None:
            verdict = "can" if trusted["reliable"] else "cannot"
            out += [
                f"How far to trust the radar there: its reliability is {trusted['score']} on a "
                f"scale from zero to one, so a radar reading {verdict} be taken at face value. "
                f"The nearest radar is {trusted['nearest_radar']}, {trusted['distance_km']} km "
                "away.",
                "",
            ]
        out += ["![The radar rain at the wettest place, hour by hour](hourly.png)", ""]

    out += [
        "## Hour by hour",
        "",
        "The radar alone over the box. Each hour ends at the time given.",
        "",
        f"| Hour | Maximum | Mean | Area at {facts['hourly_threshold_mm']} mm or more |",
        "|---|---|---|---|",
    ]
    for hour in facts["hourly"]:
        if hour["max_mm"] is None:
            out.append(f"| {_hhmm(hour['time'])} | no frame | | |")
        else:
            out.append(
                f"| {_hhmm(hour['time'])} | {hour['max_mm']} mm | {hour['mean_mm']} mm "
                f"| {hour['km2_over']} km² |"
            )
    out += ["", "## The official alert", ""]
    if not facts["bulletins"]:
        out += ["No criticality bulletin was read for this report.", ""]
    for b in facts["bulletins"]:
        when = f"{_day_words(b['issued'])} at {_hhmm(b['issued'])} (Italian time)"
        zone = b["zone"]
        if zone is None:
            out += [f"The bulletin issued on {when} has no zone at the wettest place.", ""]
            continue
        out += [
            f"The bulletin issued on {when} put the wettest place, in the zone "
            f"«{zone['zone']}», at level **{zone['colour']}** for {_day_words(zone['day'])} "
            f"(hydrogeological risk: {zone['hydrogeological']}; hydraulic: {zone['hydraulic']}; "
            f"thunderstorms: {zone['thunderstorms']}).",
            "",
        ]
    out += ["## The frames", ""]
    for key in ("radar", "merging"):
        f = facts["frames"][key]
        line = f"- {f['product']}: {f['used']} of {f['expected']} frames used."
        if f["missing"]:
            line += f" Missing: {_times(f['missing'])}."
        if f["rejected"]:
            line += f" Rejected by the quality check: {_times(f['rejected'])}."
        if f["masked_pixels"]:
            line += (
                f" In the frames used, the quality check masked {f['masked_pixels']} pixel "
                "readings (a pixel counts once for every frame it is masked in)."
            )
        out.append(line)
    return "\n".join(out) + "\n"


# The images.


def _lonlat_mesh(grid: RainGrid) -> tuple[np.ndarray, np.ndarray]:
    """The longitude and latitude of the centre of every pixel."""
    h, w = grid.data.shape
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    t = grid.transform
    xs, ys = t.a * cols + t.b * rows + t.c, t.d * cols + t.e * rows + t.f
    lons, lats = warp_points(grid.crs, "EPSG:4326", xs.ravel(), ys.ravel())
    return np.reshape(lons, (h, w)), np.reshape(lats, (h, w))


def _images(report: Report, folder: Path) -> list[Path]:
    try:
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise DpcRadarError("the images need matplotlib: pip install 'dpc-radar[report]'") from exc

    facts = report.facts
    peak = facts["peak"]
    west, south, east, north = facts["event"]["bbox"]
    panels = [
        ("The radar alone", report.radar),
        ("The «Merging» (radar and rain gauges)", report.merging),
    ]
    shown = [(title, grid) for title, grid in panels if grid is not None]
    top = max(float(np.nanmax(g.data)) if np.isfinite(g.data).any() else 0.0 for _, g in shown)
    figure = Figure(figsize=(5.5 * len(shown), 4.6), layout="constrained")
    axes = figure.subplots(1, len(shown), squeeze=False)[0]
    for ax, (title, grid) in zip(axes, shown, strict=True):
        lons, lats = _lonlat_mesh(grid)
        mesh = ax.pcolormesh(
            lons, lats, grid.data, shading="nearest", cmap="YlGnBu", vmin=0, vmax=max(top, 1.0)
        )
        if peak is not None:
            ax.plot(peak["lon"], peak["lat"], marker="+", color="crimson", markersize=14)
        ax.set(title=title, xlim=(west, east), ylim=(south, north))
        ax.set(xlabel="longitude (° E)", ylabel="latitude (° N)")
    figure.colorbar(mesh, ax=list(axes), label=f"mm on {_day_words(facts['event']['day'])}")
    figure.savefig(folder / "map.png", dpi=140)
    written = [folder / "map.png"]

    if peak is not None:
        figure = Figure(figsize=(9, 3.6), layout="constrained")
        ax = figure.subplots()
        series = peak["hourly_radar_mm"]
        ax.bar(range(1, len(series) + 1), [mm or 0.0 for mm in series], color="#2b7bba")
        ax.set(
            xlabel="hour of the day (UTC, the hour each bar ends at)",
            ylabel="radar rain (mm)",
            title=f"The radar rain at {_place(peak['lon'], peak['lat'])}, hour by hour",
        )
        figure.savefig(folder / "hourly.png", dpi=140)
        written.append(folder / "hourly.png")
    return written


__all__ = [
    "HOURLY_MM",
    "THRESHOLDS",
    "WINDOWS",
    "Misquote",
    "Read",
    "Report",
    "build",
    "bulletins_for",
    "check_claims",
    "check_text",
    "day_of",
    "download",
    "hours_of",
]
