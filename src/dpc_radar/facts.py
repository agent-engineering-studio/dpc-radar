"""The numbers an assistant may quote, each with the frame it came from.

A language model that is asked "how much did it rain in Florence?" will answer
with a number, whether it has one or not. :class:`Facts` gives it the number:
every answer is a plain dictionary, ready for JSON, that names the product and
the time of the frame it was computed from, how the value was read and how far
the radar can be trusted there. The model words the answer; it does not compute
it. This is what the MCP server (:mod:`dpc_radar.mcp_server`) serves, and it
can be used as it is from Python::

    async with RadarClient() as radar:
        facts = Facts(radar)
        await facts.rain_at(11.25, 43.77, hours=3)

* :meth:`Facts.latest`: the newest frame of every product.
* :meth:`Facts.rain_at`: the rain of the last 1, 3, 6, 12 or 24 hours at a point.
* :meth:`Facts.area_summary`: the same over a box: maximum, mean, km² over thresholds.
* :meth:`Facts.storm_now`: the storms on the newest intensity frame, and where they go.
* :meth:`Facts.rain_ahead`: the nowcast at a point, always labelled as a forecast.
* :meth:`Facts.official_alert`: the level of the DPC criticality bulletin.
* :meth:`Facts.rarity`: how rare an amount of rain is at a place.

The rules every answer follows:

* a point is read as the median of the 3x3 pixels around it, never as one pixel;
* the newest frame goes through :meth:`dpc_radar.RainGrid.quality` against the
  one before it; if it is rejected the answer comes from the frame before, and
  says so;
* ``None`` means not measured, which is not the same as dry;
* millimetres are rounded to 0.1: the radar does not know more than that.

Frames are kept in memory (``cache_frames`` of them), so a conversation that
asks about ten places downloads each frame once.
"""

from __future__ import annotations

import asyncio
import time as clock
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar import bulletins
from dpc_radar.bulletins import Bulletin, Zone
from dpc_radar.client import Absent
from dpc_radar.climatology import MAX_RETURN_YEARS
from dpc_radar.climatology import rarity as rarity_at
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import BBox, RainGrid
from dpc_radar.nowcast import chance, motion
from dpc_radar.nowcast import rain_ahead as forecast_sum
from dpc_radar.products import PRODUCTS, LastProduct, Product
from dpc_radar.quality import Quality
from dpc_radar.reliability import reliability
from dpc_radar.storms import _pixel_km2, storms

#: The product that answers "the rain of the last N hours". There is no CUM1.
HOURS: Final = {1: "SRT1", 3: "CUM3", 6: "CUM6", 12: "CUM12", 24: "CUM24"}
#: The thresholds (mm) of :meth:`Facts.area_summary`.
THRESHOLDS: Final = (1, 5, 10, 20, 50, 100)
#: A storm, for :meth:`Facts.storm_now`: a patch at this intensity (mm/h) or more.
STORM_MM_H: Final = 10.0
#: Storms listed by :meth:`Facts.storm_now`; the count is always of them all.
MAX_STORMS: Final = 20
#: How long a bulletin is kept before it is asked for again, in seconds.
BULLETIN_TTL_S: Final = 900.0

METHOD: Final = "median of the 3x3 pixels around the point"
NOT_MEASURED: Final = "not measured here (no data, or masked by the quality check): not dry"
NOT_OFFICIAL: Final = "radar data, not an alert: the official alert is the DPC bulletin"

_BASELINE = 6  # frames of five minutes: the half hour the motion is measured on


class Source(Protocol):
    """Where the frames come from: a :class:`dpc_radar.RadarClient`, or anything like it."""

    async def last_product(self, name: str | Product) -> LastProduct | None: ...

    async def at(self, name: str | Product, time: datetime) -> RainGrid | Absent: ...


def stamp(time: datetime) -> str:
    """A time as the facts write it: ``2026-10-10T06:00Z``, always UTC."""
    return f"{time.astimezone(UTC):%Y-%m-%dT%H:%MZ}"


def frame_of(grid: RainGrid) -> dict[str, Any]:
    """Which frame a number came from."""
    p = grid.product
    return {
        "product": p.name,
        "time": stamp(grid.time),
        "unit": "mm/h" if p.hours is None else "mm",
        "hours": p.hours,
        "source": "radar and rain gauges («Merging»)" if p.merging else "radar only",
    }


def _mm(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def lonlat(grid: RainGrid, row: int, col: int) -> tuple[float, float]:
    """The centre of a pixel, in degrees."""
    t, c, r = grid.transform, col + 0.5, row + 0.5
    x, y = t.a * c + t.b * r + t.c, t.d * c + t.e * r + t.f
    lons, lats = warp_points(grid.crs, "EPSG:4326", [x], [y])
    return round(float(lons[0]), 4), round(float(lats[0]), 4)


def describe(part: RainGrid, thresholds: tuple[int, ...] = THRESHOLDS) -> dict[str, Any]:
    """The rain of a frame, or of a piece of one, as numbers.

    The mean, the median and the 95th percentile are of the pixels with data,
    each weighted by its area. ``measured_share`` is the share of the area that
    has data: a mean over half a box is the mean of that half.
    """
    known = np.isfinite(part.data)
    km2 = np.broadcast_to(_pixel_km2(part)[:, None], part.data.shape)
    total = float(km2.sum())
    out: dict[str, Any] = {
        "area_km2": round(total, 1),
        "measured_share": round(float(km2[known].sum()) / total, 3),
        "max_mm": None,
        "max_lon": None,
        "max_lat": None,
        "mean_mm": None,
        "median_mm": None,
        "p95_mm": None,
        "km2_over": {str(t): 0.0 for t in thresholds},
    }
    if not known.any():
        return out
    values, weights = part.data[known], km2[known]
    row, col = np.unravel_index(int(np.nanargmax(part.data)), part.data.shape)
    out["max_lon"], out["max_lat"] = lonlat(part, int(row), int(col))
    order = np.argsort(values)
    share = np.cumsum(weights[order]) / weights.sum()
    out |= {
        "max_mm": _mm(values.max()),
        "mean_mm": _mm(np.average(values, weights=weights)),
        "median_mm": _mm(values[order][np.searchsorted(share, 0.5)]),
        "p95_mm": _mm(values[order][np.searchsorted(share, 0.95)]),
        "km2_over": {str(t): round(float(weights[values >= t].sum()), 1) for t in thresholds},
    }
    return out


def trust(lon: float, lat: float) -> dict[str, Any]:
    """How far the radar can be trusted at a point (:func:`dpc_radar.reliability`)."""
    r = reliability(lon, lat)
    return {
        "score": r.score,
        "reliable": r.reliable,
        "beam_height_m": r.beam_height,
        "nearest_radar": r.nearest_site,
        "distance_km": round(r.distance_km, 1),
    }


def rarity_of(lon: float, lat: float, hours: int, mm: float) -> dict[str, Any]:
    """How rare ``mm`` in ``hours`` are at a point (:func:`dpc_radar.rarity`)."""
    r = rarity_at(hours, lon, lat, mm)
    top = float(MAX_RETURN_YEARS)
    return {
        "hours": hours,
        "mm": _mm(mm),
        "return_period_years": _mm(r.return_period),
        "low_years": _mm(r.low),
        "high_years": _mm(r.high),
        "percentile": r.percentile,
        "max_return_years": int(top),
        "at_least": r.return_period is not None and r.return_period >= top,
        "years_of_radar": r.years,
        "basis": "radar-only rain, IT-DPC-SRI history; outside it the answer is None",
    }


def zone_of(zone: Zone) -> dict[str, Any]:
    """One alert zone on one day."""
    return {
        "day": zone.valid.isoformat(),
        "zone": zone.name,
        "level": zone.level,
        "colour": zone.colour,
        "hydrogeological": bulletins.COLOURS[zone.hydrogeological],
        "hydraulic": bulletins.COLOURS[zone.hydraulic],
        "thunderstorms": bulletins.COLOURS[zone.thunderstorms],
    }


def _quality(q: Quality, replaced: RainGrid | None) -> dict[str, Any]:
    return {
        "verdict": q.verdict,
        "trusted_share": round(q.score, 3),
        "masked_pixels": int(q.mask.sum()),
        "reasons": dict(q.reasons),
        "newest_frame_rejected": None if replaced is None else stamp(replaced.time),
    }


def _good(answer: Quality, frame: RainGrid) -> RainGrid:
    """The frame a quality check lets through; an error if it lets nothing through."""
    if answer.grid is None:
        raise DpcRadarError(
            f"the {frame.product.name} frame of {stamp(frame.time)} fails the quality check"
        )
    return answer.grid


def _box(west: float, south: float, east: float, north: float) -> BBox:
    if not (west < east and south < north):
        raise DpcRadarError("a box is west, south, east, north in degrees, west < east")
    return (west, south, east, north)


def _overlap(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


class Facts:
    """Answers about the rain over Italy, as dictionaries that name their frames."""

    def __init__(
        self,
        radar: Source,
        *,
        bulletin: Callable[[], Awaitable[Bulletin]] | None = None,
        cache_frames: int = 24,
    ) -> None:
        self._radar = radar
        self._bulletin = bulletin or bulletins.latest
        self._frames: OrderedDict[tuple[str, datetime], RainGrid] = OrderedDict()
        self._cache_frames = cache_frames
        self._held: tuple[float, Bulletin] | None = None

    async def _last(self, name: str) -> LastProduct:
        last = await self._radar.last_product(name)
        if last is None:
            raise DpcRadarError(f"the DPC API lists no frame of {name}")
        return last

    async def _at(self, name: str, time: datetime) -> RainGrid | None:
        key = (name, time)
        if key in self._frames:
            self._frames.move_to_end(key)
            return self._frames[key]
        grid = await self._radar.at(name, time)
        if isinstance(grid, Absent):
            return None
        self._frames[key] = grid
        if len(self._frames) > self._cache_frames:
            self._frames.popitem(last=False)
        return grid

    async def _trusted(self, name: str) -> tuple[RainGrid, dict[str, Any]]:
        """The newest frame that passes the quality check, and what the check said."""
        last = await self._last(name)
        newest = await self._at(name, last.time)
        if newest is None:
            raise DpcRadarError(f"the newest {name} frame ({stamp(last.time)}) is not available")
        previous = await self._at(name, last.time - last.product.period)
        answer, replaced = newest.quality(previous), None
        if answer.grid is None and previous is not None:
            answer, replaced = previous.quality(), newest
        return _good(answer, newest), _quality(answer, replaced)

    async def _intensity(self) -> list[RainGrid]:
        """The SRI frames of the last half hour, the oldest first, the last one checked."""
        last = await self._last("SRI")
        times = [last.time - last.product.period * k for k in range(_BASELINE, -1, -1)]
        found = [f for f in [await self._at("SRI", t) for t in times] if f is not None]
        if not found or found[-1].time != last.time:
            raise DpcRadarError(f"the newest SRI frame ({stamp(last.time)}) is not available")
        return [*found[:-1], _good(found[-1].quality(), found[-1])]

    async def latest(self) -> dict[str, Any]:
        """The newest frame of every product: its time, and how often a new one comes."""
        names = list(PRODUCTS)
        found = await asyncio.gather(*(self._radar.last_product(n) for n in names))
        return {
            "products": [
                {
                    "product": name,
                    "what": PRODUCTS[name].description,
                    "time": None if last is None else stamp(last.time),
                    "every_minutes": None
                    if last is None
                    else int(last.period.total_seconds() / 60),
                }
                for name, last in zip(names, found, strict=True)
            ],
            "note": "times are UTC; a frame of N hours covers the N hours before its time",
        }

    async def rain_at(self, lon: float, lat: float, hours: int = 1) -> dict[str, Any]:
        """The rain of the last ``hours`` (1, 3, 6, 12 or 24) at a point, in mm."""
        grid, quality = await self._trusted(_product(hours))
        (mm,) = grid.sample([(lon, lat)])
        return {
            "lon": lon,
            "lat": lat,
            "hours": hours,
            "mm": _mm(mm),
            "measured": mm is not None,
            "method": METHOD,
            "frame": frame_of(grid),
            "quality": quality,
            "reliability": trust(lon, lat),
            "note": NOT_MEASURED if mm is None else NOT_OFFICIAL,
        }

    async def area_summary(
        self, west: float, south: float, east: float, north: float, hours: int = 1
    ) -> dict[str, Any]:
        """The rain of the last ``hours`` over a box in degrees: maximum, mean, km²."""
        bbox = _box(west, south, east, north)
        grid, quality = await self._trusted(_product(hours))
        return {
            "bbox": list(bbox),
            "hours": hours,
            **describe(grid.crop(bbox)),
            "frame": frame_of(grid),
            "quality": quality,
            "note": "the maximum is one pixel; km2_over is the area at each mm or more. "
            + NOT_OFFICIAL,
        }

    async def storm_now(
        self,
        west: float | None = None,
        south: float | None = None,
        east: float | None = None,
        north: float | None = None,
    ) -> dict[str, Any]:
        """The storms on the newest SRI frame (patches at 10 mm/h or more), heaviest first.

        With a box, only those that touch it. ``moving`` is the motion of the
        rain around the storm over the last half hour: km/h, and the degrees
        from north of where it goes (90: east).
        """
        corners = [c for c in (west, south, east, north) if c is not None]
        if len(corners) not in (0, 4):
            raise DpcRadarError("a box needs west, south, east and north, all four")
        bbox = _box(*corners) if corners else None
        frames = await self._intensity()
        now = frames[-1]
        found = storms(now, threshold=STORM_MM_H)
        if bbox is not None:
            found = [s for s in found if _overlap(s.bbox, bbox)]
        found.sort(key=lambda s: -s.peak)
        try:
            wind = motion(frames)
        except DpcRadarError:  # one frame only, or frames too far apart
            wind = None
        listed = []
        for s in found[:MAX_STORMS]:
            going = None if wind is None else wind.at(s.lon, s.lat)
            listed.append(
                {
                    "lon": round(s.lon, 3),
                    "lat": round(s.lat, 3),
                    "area_km2": round(s.area_km2, 1),
                    "peak_mm_h": _mm(s.peak),
                    "peak_lon": round(s.peak_lon, 3),
                    "peak_lat": round(s.peak_lat, 3),
                    "mean_mm_h": _mm(s.mean),
                    "moving": None
                    if going is None
                    else {"kmh": round(going[0], 1), "towards_degrees": round(going[1])},
                }
            )
        return {
            "bbox": None if bbox is None else list(bbox),
            "threshold_mm_h": STORM_MM_H,
            "count": len(found),
            "storms": listed,
            "frame": frame_of(now),
            "motion_from": stamp(frames[0].time) if wind is not None else None,
            "note": "an intensity (mm/h) at one instant, not the rain of an hour. " + NOT_OFFICIAL,
        }

    async def rain_ahead(
        self, lon: float, lat: float, minutes: int = 60, mm: float = 20.0
    ) -> dict[str, Any]:
        """A forecast: the rain of the next ``minutes`` (5 to 120) at a point.

        The newest SRI frame moved along the motion of the last half hour
        (:mod:`dpc_radar.nowcast`), and the chance of at least ``mm``.
        """
        frames = await self._intensity()
        ahead = forecast_sum(frames, minutes=minutes)
        (total,) = ahead.sample([(lon, lat)])
        (share,) = chance(frames, mm=mm, minutes=minutes).sample([(lon, lat)])
        going = motion(frames).at(lon, lat)
        return {
            "kind": "forecast",
            "lon": lon,
            "lat": lat,
            "minutes": minutes,
            "issued": stamp(ahead.issued),
            "until": stamp(ahead.time),
            "forecast_mm": _mm(total),
            "chance": {"mm": mm, "probability": None if share is None else round(share, 2)},
            "moving": None
            if going is None
            else {"kmh": round(going[0], 1), "towards_degrees": round(going[1])},
            "frame": frame_of(frames[-1]),
            "motion_from": stamp(frames[0].time),
            "limits": "an extrapolation: the rain is moved, not changed. Storms born after "
            "the frame are not in it. Trust the chance more than the millimetres; "
            "None is not known, not dry. " + NOT_OFFICIAL,
        }

    async def official_alert(
        self,
        lon: float | None = None,
        lat: float | None = None,
        place: str | None = None,
    ) -> dict[str, Any]:
        """The level of the newest DPC criticality bulletin, for today and tomorrow.

        With ``lon`` and ``lat``: the zone of that point. With ``place``: the
        zones whose name contains it, or that hold a municipality of that
        name. With neither: every zone on alert (yellow or more).
        """
        bulletin = await self._newest_bulletin()
        days = bulletin.days()
        if lon is not None and lat is not None:
            at = [bulletin.zone_at(lon, lat, day) for day in days]
            zones = [z for z in at if z is not None]
        elif place:
            text = place.strip().casefold()
            zones = [
                z
                for z in bulletin.zones
                if text in z.name.casefold() or any(text == m.casefold() for m in z.municipalities)
            ]
        else:
            zones = [z for day in days for z in bulletin.alerts(day)]
        return {
            "bulletin": bulletin.name,
            "issued": bulletin.issued.isoformat(timespec="minutes"),
            "days": [d.isoformat() for d in days],
            "asked": {"lon": lon, "lat": lat, "place": place},
            "zones": [zone_of(z) for z in sorted(zones, key=lambda z: (z.valid, -z.level, z.name))],
            "levels": "0 none, 1 yellow, 2 orange, 3 red; the level is the highest of the three "
            "risks (hydrogeological, hydraulic, thunderstorms)",
            "note": "no zone listed means no zone found for the question, not the absence of "
            "an alert. Source: DPC criticality bulletin (Italian time).",
        }

    def rarity(self, lon: float, lat: float, hours: int, mm: float) -> dict[str, Any]:
        """How rare ``mm`` of rain in ``hours`` (1, 3, 6 or 24) are at a point."""
        return {"lon": lon, "lat": lat, **rarity_of(lon, lat, hours, mm)}

    async def _newest_bulletin(self) -> Bulletin:
        now = clock.monotonic()
        if self._held is None or now - self._held[0] > BULLETIN_TTL_S:
            self._held = (now, await self._bulletin())
        return self._held[1]


def _product(hours: int) -> str:
    if hours not in HOURS:
        raise DpcRadarError(f"the DPC publishes the rain of {sorted(HOURS)} hours, not of {hours}")
    return HOURS[hours]


__all__ = [
    "HOURS",
    "MAX_STORMS",
    "STORM_MM_H",
    "THRESHOLDS",
    "Facts",
    "Source",
    "describe",
    "frame_of",
    "lonlat",
    "rarity_of",
    "stamp",
    "trust",
    "zone_of",
]
