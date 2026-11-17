"""The API behind the demo map: the rain over Italy as JSON, GeoJSON and PNG.

    uvicorn --app-dir demo api:app --port 8000

Every answer is made with the public functions of dpc-radar, and the frames
are read live from the DPC: no key, no account. What is kept in memory is the
frames already read and what was computed from them, so a click on the map
downloads nothing twice. The page in ``web/`` is the only client; nothing here
is an alert service.

    /api/health            is it up
    /api/layer/{key}       the newest frame of «now», 1, 3, 6, 12 or 24 hours
    /api/rain/{key}.png    that frame as a picture, in the projection of the map
    /api/film/{key}        the frames before it, for the map to play: /api/rain/{key}.png?t=
    /api/zones             the alert zones of the newest bulletin, as GeoJSON
    /api/storms            the storms on the newest frame, as GeoJSON
    /api/radars            the radars, and which are on, as GeoJSON
    /api/point?lon=&lat=   the rain at a point, and in the alert zone it is in
    /api/ahead?lon=&lat=   the rain of the next two hours at a point, as a forecast
    /api/radar?name=       the rain inside the range of a radar
"""

from __future__ import annotations

import asyncio
import math
import time as clock
from collections import OrderedDict, defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import numpy as np
from affine import Affine
from rasterio.features import geometry_mask, shapes
from rasterio.io import MemoryFile
from rasterio.warp import Resampling, reproject, transform_geom
from shapely.geometry import Point, Polygon, box, mapping, shape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from dpc_radar import (
    SITES,
    Absent,
    DpcRadarError,
    Forecast,
    Motion,
    NetworkStatus,
    RadarClient,
    RainGrid,
    bulletins,
    chance,
    motion,
    nowcast,
    storms,
)
from dpc_radar.climatology import WINDOWS
from dpc_radar.facts import HOURS, STORM_MM_H, describe, frame_of, rarity_of, stamp, trust, zone_of
from dpc_radar.products import LastProduct, Product

#: The layers of the map: «now» is an intensity, the others the rain of N hours.
LAYERS = {"now": "SRI"} | {str(hours): name for hours, name in HOURS.items()}
#: Where each colour starts, in mm/h and in mm.
MM_H = [0.5, 2, 5, 10, 25, 50, 100]
MM = [1, 5, 10, 25, 50, 100, 150]
COLOURS = ["#a9cdf6", "#5f9fea", "#2c70d8", "#1b47a8", "#6a2fb0", "#b3289a", "#e3245e"]
#: Where there is no measure: a light veil, because not measured is not dry.
VEIL = (90, 96, 110, 46)
#: The picture: Italy in the projection of the web maps (EPSG:3857), 1 km pixels.
WEST, SOUTH, EAST, NORTH = 5.5, 35.0, 19.5, 48.0
#: The forecasts of a click, in minutes, and the rain their chance is of, in mm.
AHEAD = (30, 60, 120)
CHANCE_MM, CHANCE_MINUTES = 5.0, 60
HOUR = timedelta(hours=1)
#: The film of a layer: how many frames, and the least time between two of them.
FILM_FRAMES, FILM_STEP = 13, timedelta(minutes=10)

_R = 6378137.0
_BASELINE = 6  # frames of five minutes: the half hour the motion is measured on


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    return math.radians(lon) * _R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _R


_X0, _Y0 = _mercator(WEST, SOUTH)
_X1, _Y1 = _mercator(EAST, NORTH)
WIDTH, HEIGHT = round((_X1 - _X0) / 1000), round((_Y1 - _Y0) / 1000)
PICTURE = Affine((_X1 - _X0) / WIDTH, 0, _X0, 0, -(_Y1 - _Y0) / HEIGHT, _Y1)


class Cache:
    """What was asked for, kept: for some seconds, or until what it was made from changes."""

    def __init__(self) -> None:
        self._held: dict[str, tuple[float, Any, Any]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get[T](
        self,
        key: str,
        job: Callable[[], Awaitable[T]],
        *,
        ttl: float | None = None,
        made_from: Any = None,
    ) -> T:
        async with self._locks[key]:
            held = self._held.get(key)
            if held is not None:
                fresh = held[1] == made_from if ttl is None else clock.monotonic() - held[0] < ttl
                if fresh:
                    return held[2]  # type: ignore[no-any-return]
            value = await job()
            if ttl != 0:  # zero is a lock alone: the job keeps what it wants to keep
                self._held[key] = (clock.monotonic(), made_from, value)
            return value


@dataclass(frozen=True)
class Frame:
    """The newest frame of a product that passes the quality check, and what the check said."""

    grid: RainGrid
    quality: dict[str, Any]


@dataclass(frozen=True)
class Ahead:
    """The forecasts made from one SRI frame."""

    totals: dict[int, Forecast]
    chance: Forecast


class Radar:
    """The DPC API with a memory: frames, the bulletin, and what was computed from them."""

    def __init__(self) -> None:
        self.client = RadarClient()
        self.cache = Cache()
        self._frames: OrderedDict[tuple[str, datetime], RainGrid] = OrderedDict()
        self._pictures: OrderedDict[tuple[str, datetime], bytes] = OrderedDict()

    async def last(self, name: str) -> LastProduct:
        last = await self.cache.get(f"last {name}", lambda: self.client.last_product(name), ttl=30)
        if last is None:
            raise DpcRadarError(f"the DPC API lists no frame of {name}")
        return last

    async def at(self, name: str | Product, time: datetime) -> RainGrid | None:
        key = (name if isinstance(name, str) else name.name, time)

        async def read() -> RainGrid | None:
            if key not in self._frames:
                found = await self.client.at(name, time)
                if isinstance(found, Absent):
                    return None
                self._frames[key] = found
                while len(self._frames) > 40:
                    self._frames.popitem(last=False)
            return self._frames[key]

        return await self.cache.get(f"frame {key[0]} {time.isoformat()}", read, ttl=0)

    async def frame(self, name: str) -> Frame:
        """The newest frame, checked against the one before (:meth:`RainGrid.quality`)."""
        last = await self.last(name)

        async def check() -> Frame:
            newest = await self.at(name, last.time)
            if newest is None:
                raise DpcRadarError(f"the newest {name} frame is not available")
            previous = await self.at(name, last.time - last.product.period)
            answer, rejected = await asyncio.to_thread(newest.quality, previous), None
            if answer.grid is None and previous is not None:
                answer, rejected = await asyncio.to_thread(previous.quality), newest
            if answer.grid is None:
                raise DpcRadarError(f"the {name} frame of {stamp(last.time)} fails the check")
            return Frame(
                answer.grid,
                {
                    "verdict": answer.verdict,
                    "trusted_share": round(answer.score, 3),
                    "masked_pixels": int(answer.mask.sum()),
                    "reasons": dict(answer.reasons),
                    "newest_frame_rejected": None if rejected is None else stamp(rejected.time),
                },
            )

        return await self.cache.get(f"checked {name}", check, made_from=last.time)

    async def picture(self, name: str, time: datetime | None) -> bytes | None:
        """A frame as a picture: the newest one checked, an older one as the DPC published it.

        Only the pictures of older frames are kept (50 kB each, a grid is 7 MB).
        """
        newest = (await self.frame(name)).grid
        key = (name, newest.time if time is None else time)

        async def draw() -> bytes | None:
            if key not in self._pictures:
                grid = newest if key[1] == newest.time else self._frames.get(key)
                if grid is None:
                    found = await self.client.at(name, key[1])
                    if isinstance(found, Absent):
                        return None
                    grid = found
                self._pictures[key] = await asyncio.to_thread(picture, grid)
                while len(self._pictures) > 120:
                    self._pictures.popitem(last=False)
            return self._pictures[key]

        return await self.cache.get(f"picture {name} {key[1].isoformat()}", draw, ttl=0)

    async def half_hour(self) -> list[RainGrid]:
        """The SRI frames of the last half hour, the oldest first, the last one checked."""
        newest = (await self.frame("SRI")).grid
        step = newest.product.period
        before = [await self.at("SRI", newest.time - step * k) for k in range(_BASELINE, 0, -1)]
        return [*(f for f in before if f is not None), newest]

    async def motion(self) -> Motion | None:
        """How the rain moved in the last half hour; ``None`` if the frames do not say."""
        frames = await self.half_hour()

        def compute() -> Motion | None:
            try:
                return motion(frames)
            except DpcRadarError:  # one frame only, or frames too far apart
                return None

        return await self.cache.get(
            "motion", lambda: asyncio.to_thread(compute), made_from=frames[-1].time
        )

    async def ahead(self) -> Ahead:
        """The rain to come: the newest frame moved along that motion, and summed."""
        frames = await self.half_hour()

        def compute() -> Ahead:
            steps = nowcast(frames, minutes=max(AHEAD))
            now, step = frames[-1], steps[0].lead
            totals = {}
            for minutes in AHEAD:
                window = timedelta(minutes=minutes)
                fields = [f.data for f in steps if f.lead <= window]
                mm = np.sum(fields, axis=0, dtype=np.float32) * np.float32(step / HOUR)
                totals[minutes] = Forecast(now.time, window, "mm", mm, now.transform, now.crs)
            return Ahead(totals, chance(frames, mm=CHANCE_MM, minutes=CHANCE_MINUTES))

        return await self.cache.get(
            "ahead", lambda: asyncio.to_thread(compute), made_from=frames[-1].time
        )

    async def bulletin(self) -> bulletins.Bulletin:
        """The newest bulletin; GitHub allows 60 unsigned calls an hour, so it is kept."""

        async def read() -> bulletins.Bulletin | str:
            try:
                return await bulletins.latest()
            except (DpcRadarError, httpx.HTTPError) as exc:
                return f"the bulletin is not reachable right now ({exc})"

        found = await self.cache.get("bulletin", read, ttl=900)
        if isinstance(found, str):
            raise DpcRadarError(found)
        return found

    async def status(self) -> NetworkStatus | None:
        async def read() -> NetworkStatus | None:
            try:
                found = await self.client.radar_status()
            except (DpcRadarError, httpx.HTTPError):
                return None
            return found if isinstance(found, NetworkStatus) else None

        return await self.cache.get("status", read, ttl=300)


def _mm(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), 1)


def colour(value: float | None, steps: list[float]) -> str | None:
    """The colour of an amount on the map; ``None`` under the first step, or not measured."""
    if value is None or not math.isfinite(value) or value < steps[0]:
        return None
    return COLOURS[int(np.digitize(value, steps)) - 1]


def picture(grid: RainGrid) -> bytes:
    """A frame as a PNG that lies on a web map: warped to EPSG:3857, one colour a step."""
    warped = np.full((HEIGHT, WIDTH), np.nan, dtype="float32")
    reproject(
        grid.data,
        warped,
        src_transform=grid.transform,
        src_crs=grid.crs,
        src_nodata=np.nan,
        dst_transform=PICTURE,
        dst_crs="EPSG:3857",
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    steps = MM_H if grid.product.hours is None else MM
    palette = np.zeros((len(steps) + 2, 4), dtype="uint8")
    for k, colour in enumerate(COLOURS, start=1):
        palette[k] = (*bytes.fromhex(colour[1:]), 215)
    palette[-1] = VEIL
    known = np.isfinite(warped)
    index = np.where(known, np.digitize(np.nan_to_num(warped), steps), len(steps) + 1)
    rgba = palette[index]
    with MemoryFile() as mem:
        with mem.open(driver="PNG", width=WIDTH, height=HEIGHT, count=4, dtype="uint8") as png:
            png.write(np.moveaxis(rgba, -1, 0))
        return bytes(mem.read())


def inside(grid: RainGrid, area: Any) -> dict[str, Any] | None:
    """The rain of a frame inside a shape in degrees; ``None`` if the frame does not reach it."""
    try:
        part = grid.crop(area.bounds)
    except DpcRadarError:
        return None
    outline = transform_geom("EPSG:4326", part.crs, mapping(area))
    out = geometry_mask([outline], part.data.shape, part.transform, all_touched=True)
    if out.all():
        return None
    found = describe(replace(part, data=np.where(out, np.nan, part.data)))
    measured = float(np.isfinite(part.data[~out]).mean())
    keep = ("max_mm", "max_lon", "max_lat", "mean_mm", "p95_mm", "km2_over")
    return {key: found[key] for key in keep} | {
        "measured_share": round(measured, 3),
        "colour": colour(found["max_mm"], MM),
    }


async def rain_inside(radar: Radar, area: Any) -> list[dict[str, Any]]:
    """The rain of the last 1 to 24 hours inside a shape."""
    rows = []
    for hours, name in HOURS.items():
        try:
            frame = await radar.frame(name)
        except (DpcRadarError, httpx.HTTPError) as exc:
            rows.append({"hours": hours, "error": str(exc)})
            continue
        found = await asyncio.to_thread(inside, frame.grid, area)
        rows.append({"hours": hours, "time": stamp(frame.grid.time), **(found or {"max_mm": None})})
    return rows


def circle(lon: float, lat: float, km: float) -> Polygon:
    """The points ``km`` away from one, as a shape in degrees."""
    d, la, lo = km * 1000 / _R, math.radians(lat), math.radians(lon)
    ring = []
    for k in range(72):
        b = math.radians(k * 5)
        la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
        lo2 = lo + math.atan2(
            math.sin(b) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2)
        )
        ring.append((math.degrees(lo2), math.degrees(la2)))
    return Polygon(ring)


def outlines(grid: RainGrid, threshold: float) -> list[Any]:
    """The shapes of the patches at ``threshold`` or more, in degrees."""
    wet = np.nan_to_num(grid.data) >= threshold
    found = shapes(wet.astype("uint8"), mask=wet, connectivity=8, transform=grid.transform)
    return [shape(transform_geom(grid.crs, "EPSG:4326", outline)) for outline, _ in found]


def _moving(going: tuple[float, float] | None) -> dict[str, Any] | None:
    if going is None:
        return None
    return {"kmh": round(going[0], 1), "towards_degrees": round(going[1])}


def _collection(features: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
    return {"type": "FeatureCollection", **more, "features": features}


def _lonlat(request: Request) -> tuple[float, float]:
    try:
        lon, lat = float(request.query_params["lon"]), float(request.query_params["lat"])
    except (KeyError, ValueError):
        raise DpcRadarError("a point is lon and lat, two numbers in degrees") from None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise DpcRadarError("a point is lon and lat, two numbers in degrees")
    return round(lon, 4), round(lat, 4)


async def _part(job: Awaitable[Any]) -> Any:
    """One piece of an answer: what fails says so, and the rest is still there."""
    try:
        return await job
    except (DpcRadarError, httpx.HTTPError) as exc:
        return {"error": str(exc) or type(exc).__name__}


Handler = Callable[[Radar, Request], Awaitable[Response | dict[str, Any]]]


def route(path: str, handler: Handler) -> Route:
    async def endpoint(request: Request) -> Response:
        try:
            found = await handler(request.app.state.radar, request)
        except DpcRadarError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"the DPC does not answer: {exc}"}, status_code=502)
        return found if isinstance(found, Response) else JSONResponse(found)

    return Route(path, endpoint)


def _layer(key: str) -> str:
    if key not in LAYERS:
        raise DpcRadarError(f"a layer is one of {', '.join(LAYERS)}")
    return LAYERS[key]


async def health(radar: Radar, request: Request) -> dict[str, Any]:
    return {"ok": True}


async def layer(radar: Radar, request: Request) -> dict[str, Any]:
    key = request.path_params["key"]
    frame = await radar.frame(_layer(key))
    of = frame_of(frame.grid)
    steps = MM_H if of["hours"] is None else MM
    return {
        "key": key,
        **of,
        "quality": frame.quality,
        "image": f"/api/rain/{key}.png?t={of['time']}",
        "bounds": [[SOUTH, WEST], [NORTH, EAST]],
        "legend": [{"from": s, "colour": c} for s, c in zip(steps, COLOURS, strict=True)],
        "python": f'await RadarClient().latest("{of["product"]}")',
    }


async def film(radar: Radar, request: Request) -> dict[str, Any]:
    """The frames before the newest one, the oldest first: what the map plays."""
    key = request.path_params["key"]
    newest = (await radar.frame(_layer(key))).grid
    step = max(newest.product.period, FILM_STEP)
    times = [stamp(newest.time - step * k) for k in range(FILM_FRAMES - 1, -1, -1)]
    return {
        "key": key,
        "product": newest.product.name,
        "step_minutes": step // timedelta(minutes=1),
        "frames": [{"time": t, "image": f"/api/rain/{key}.png?t={t}"} for t in times],
        "python": f'await RadarClient().at("{newest.product.name}", time)',
    }


async def rain(radar: Radar, request: Request) -> Response:
    name, asked = _layer(request.path_params["key"]), request.query_params.get("t")
    try:
        time = None if asked is None else datetime.fromisoformat(asked)
    except ValueError:
        raise DpcRadarError("a time is YYYY-MM-DDTHH:MMZ") from None
    if time is not None and time.tzinfo is None:
        raise DpcRadarError("a time is YYYY-MM-DDTHH:MMZ")
    png = await radar.picture(name, time)
    if png is None:
        return Response(status_code=404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


def _shown(bulletin: bulletins.Bulletin) -> date:
    """The day of a bulletin the map shows: today in Italy, or the nearest it has."""
    today = datetime.now(bulletins.ROME).date()
    return min(bulletin.days(), key=lambda day: abs(day - today))


async def zones(radar: Radar, request: Request) -> dict[str, Any]:
    bulletin = await radar.bulletin()
    shown = _shown(bulletin)

    def build() -> dict[str, Any]:
        features = [
            {
                "type": "Feature",
                "geometry": mapping(zone.geometry.simplify(0.004)),
                "properties": {"kind": "zone", **zone_of(zone)},
            }
            for zone in bulletin.zones
            if zone.valid == shown
        ]
        return _collection(
            features,
            bulletin=bulletin.name,
            issued=bulletin.issued.isoformat(timespec="minutes"),
            day=shown.isoformat(),
            python="await bulletins.latest()",
        )

    return await radar.cache.get(
        "zones", lambda: asyncio.to_thread(build), made_from=(bulletin.name, shown)
    )


async def storms_now(radar: Radar, request: Request) -> dict[str, Any]:
    frame = await radar.frame("SRI")
    wind = await radar.motion()

    def build() -> dict[str, Any]:
        grid = frame.grid
        patches = outlines(grid, STORM_MM_H)
        features = []
        for storm in storms(grid, threshold=STORM_MM_H):
            peak = Point(storm.peak_lon, storm.peak_lat)
            around = min(patches, key=lambda p: p.distance(peak), default=box(*storm.bbox))
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(around),
                    "properties": {
                        "kind": "storm",
                        "lon": round(storm.lon, 3),
                        "lat": round(storm.lat, 3),
                        "area_km2": round(storm.area_km2, 1),
                        "peak_mm_h": _mm(storm.peak),
                        "colour": colour(storm.peak, MM_H),
                        "peak_lon": round(storm.peak_lon, 3),
                        "peak_lat": round(storm.peak_lat, 3),
                        "mean_mm_h": _mm(storm.mean),
                        "moving": _moving(None if wind is None else wind.at(storm.lon, storm.lat)),
                    },
                }
            )
        return _collection(
            features,
            threshold_mm_h=STORM_MM_H,
            frame=frame_of(grid),
            python=f'storms(await RadarClient().latest("SRI"), threshold={STORM_MM_H})',
        )

    return await radar.cache.get(
        "storms", lambda: asyncio.to_thread(build), made_from=frame.grid.time
    )


async def radars(radar: Radar, request: Request) -> dict[str, Any]:
    status = await radar.status()
    listed = {} if status is None else {r.code: r for r in status.radars}
    features = []
    for site in SITES:
        told = listed.pop(site.code, None) if site.code else None
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [site.lon, site.lat]},
                "properties": {
                    "kind": "radar",
                    "name": site.name,
                    "code": site.code,
                    "band": site.band,
                    "range_km": site.range_km,
                    "altitude_m": site.altitude,
                    "operator": None if told is None else told.operator,
                    "on": None if told is None else told.on,
                    "in_mosaic": True,
                },
            }
        )
    for told in listed.values():
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [told.lon, told.lat]},
                "properties": {
                    "kind": "radar",
                    "name": f"{told.place} ({told.province})",
                    "code": told.code,
                    "band": None,
                    "range_km": None,
                    "altitude_m": told.altitude,
                    "operator": told.operator,
                    "on": told.on,
                    "in_mosaic": False,
                },
            }
        )
    return _collection(
        features,
        known=status is not None,
        time=None if status is None else stamp(status.time),
        python="await RadarClient().radar_status()",
    )


async def _now(radar: Radar, lon: float, lat: float) -> dict[str, Any]:
    frame = await radar.frame("SRI")
    (mm_h,) = frame.grid.sample([(lon, lat)])
    return {
        "mm_h": _mm(mm_h),
        "measured": mm_h is not None,
        "colour": colour(mm_h, MM_H),
        "time": stamp(frame.grid.time),
    }


async def _fallen(radar: Radar, lon: float, lat: float, hours: int) -> dict[str, Any]:
    frame = await radar.frame(HOURS[hours])
    (mm,) = frame.grid.sample([(lon, lat)])
    row: dict[str, Any] = {
        "hours": hours,
        "mm": _mm(mm),
        "measured": mm is not None,
        "colour": colour(mm, MM),
        "product": frame.grid.product.name,
        "time": stamp(frame.grid.time),
        "verdict": frame.quality["verdict"],
        "rarity": None,
    }
    if mm and hours in WINDOWS:
        rare = rarity_of(lon, lat, hours, mm)
        row["rarity"] = {k: rare[k] for k in ("return_period_years", "at_least", "percentile")}
    return row


async def _ahead(radar: Radar, lon: float, lat: float) -> dict[str, Any]:
    ahead, wind = await radar.ahead(), await radar.motion()
    (share,) = ahead.chance.sample([(lon, lat)])
    return {
        "lon": lon,
        "lat": lat,
        "issued": stamp(ahead.chance.issued),
        "steps": [
            {"minutes": m, "forecast_mm": _mm(ahead.totals[m].sample([(lon, lat)])[0])}
            for m in AHEAD
        ],
        "chance": {
            "mm": CHANCE_MM,
            "minutes": CHANCE_MINUTES,
            "probability": None if share is None else round(share, 2),
        },
        "moving": _moving(None if wind is None else wind.at(lon, lat)),
        "python": "await Facts(RadarClient())"
        f".rain_ahead({lon}, {lat}, minutes={CHANCE_MINUTES}, mm={CHANCE_MM})",
    }


async def _zone(radar: Radar, lon: float, lat: float) -> dict[str, Any] | None:
    bulletin = await radar.bulletin()
    found = [bulletin.zone_at(lon, lat, day) for day in bulletin.days()]
    days = [z for z in found if z is not None]
    if not days:
        return None
    return {
        "name": days[0].name,
        "shown": _shown(bulletin).isoformat(),
        "bulletin": bulletin.name,
        "issued": bulletin.issued.isoformat(timespec="minutes"),
        "days": [zone_of(z) for z in days],
        "rain": await rain_inside(radar, days[0].geometry),
    }


async def _in_range(radar: Radar, lon: float, lat: float) -> dict[str, Any]:
    status = await radar.status()
    near = sorted((trust_km(lon, lat, s.lon, s.lat), s) for s in SITES)
    return {
        "known": status is not None,
        "time": None if status is None else stamp(status.time),
        "radars": [
            {
                "name": site.name,
                "band": site.band,
                "distance_km": round(km, 1),
                "on": None if status is None else status.of(site),
            }
            for km, site in near
            if km <= site.range_km
        ],
    }


def trust_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Kilometres between two points on the sphere."""
    a, b = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * _R / 1000 * math.asin(math.sqrt(h))


async def point(radar: Radar, request: Request) -> dict[str, Any]:
    lon, lat = _lonlat(request)
    now, zone, in_range, *fallen = await asyncio.gather(
        _part(_now(radar, lon, lat)),
        _part(_zone(radar, lon, lat)),
        _part(_in_range(radar, lon, lat)),
        *(_part(_fallen(radar, lon, lat, hours)) for hours in HOURS),
    )
    return {
        "lon": lon,
        "lat": lat,
        "now": now,
        "rain": [
            row if "hours" in row else {"hours": hours, **row}
            for hours, row in zip(HOURS, fallen, strict=True)
        ],
        "zone": zone,
        "reliability": trust(lon, lat),
        "radars": in_range,
        "python": f"facts = Facts(RadarClient())\n"
        f"await facts.rain_at({lon}, {lat}, hours=24)\n"
        f"await facts.official_alert({lon}, {lat})",
    }


async def ahead(radar: Radar, request: Request) -> dict[str, Any]:
    return await _ahead(radar, *_lonlat(request))


async def radar_range(radar: Radar, request: Request) -> dict[str, Any]:
    name = request.query_params.get("name", "")
    site = next((s for s in SITES if s.name == name), None)
    if site is None:
        raise DpcRadarError(f"no radar of the mosaic is called {name!r}")
    return {
        "name": site.name,
        "range_km": site.range_km,
        "rain": await rain_inside(radar, circle(site.lon, site.lat, site.range_km)),
    }


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    radar = app.state.radar = Radar()
    # Read ahead what the first click would wait for.
    ready = asyncio.create_task(_part(radar.ahead()))
    try:
        yield
    finally:
        ready.cancel()
        await radar.client.aclose()


app = Starlette(
    lifespan=lifespan,
    middleware=[Middleware(GZipMiddleware, minimum_size=2000)],
    routes=[
        route("/api/health", health),
        route("/api/layer/{key}", layer),
        route("/api/film/{key}", film),
        route("/api/rain/{key}.png", rain),
        route("/api/zones", zones),
        route("/api/storms", storms_now),
        route("/api/radars", radars),
        route("/api/point", point),
        route("/api/ahead", ahead),
        route("/api/radar", radar_range),
    ],
)
