"""A catalogue of rare rain: where and when the radar saw rain that comes once in years.

:func:`rare_events` reads a run of hourly radar frames (``SRT1``) and, hour by
hour, sums them over the windows of the climatology (1, 3, 6 and 24 hours).
Where a sum reaches the level a place passes once in ``years`` years
(:mod:`dpc_radar.climatology`) there is rare rain. The touching pixels over
their level are one patch; patches that overlap from one hour to the next are
the same :class:`Event`, and two events that grow into each other become one.
Each event says where and when its rain was rarest, how many millimetres fell
there, in which window, the return period, and the area it covered.

It is a list of places to look at, not a list of floods. The radar says where
it rained too much for that place; whether there was damage is for someone to
check on the ground or in the news. Three things keep the list honest:

* pixels where the radar does not see well are left out
  (:func:`dpc_radar.reliability`, ``min_reliability``): there the radar
  millimetres are not rain;
* patches smaller than ``min_area_km2`` are dropped: a few pixels over the
  level are more often clutter than a storm;
* :func:`with_merging` reads, from an archive, what the «Merging» (radar and
  rain gauges) says of the same hours at the same place. The radar alone
  reads echoes that are not rain: an event the «Merging» does not see
  (:attr:`Event.confirmed` false) is most likely one of those.

What it cannot see: rain where the radar is blind or too far, rain the radar
under-reads (the climatology is in radar millimetres, so a constant
under-reading cancels, a storm behind a mountain does not), and anything
shorter than the season it is given. It needs SciPy (extra ``storms``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar.climatology import (
    WINDOWS,
    Climatology,
    _gev_level,
    _gev_years,
    climatology,
)
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import BBox, RainGrid
from dpc_radar.reliability import reliability_map
from dpc_radar.storms import MIN_AREA_KM2, _ndimage, _pixel_km2

if TYPE_CHECKING:
    from dpc_radar.archive import MergingArchive

#: Rain is rare from this return period (years) unless the caller says otherwise.
YEARS = 10.0
#: Pixels where the radar's reliability score is under this are left out.
MIN_RELIABILITY = 0.5
#: An event is confirmed when the «Merging» has at least this share of the radar's rain.
CONFIRMED_SHARE = 0.5
#: :func:`with_merging` looks this far (degrees) around the rarest pixel.
_AROUND = 0.05
_HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Event:
    """Rain over the level of its place, followed while it stayed over it.

    ``lon``/``lat`` is where the rain was rarest: ``mm`` fell there in the
    ``window`` hours that end at ``time``, against a ``level_mm`` passed once
    in the ``years`` the catalogue was asked for; ``return_period`` is what the
    climatology says of those millimetres (it stops at 50 years). It is not
    the single rarest pixel, which is too often an echo or hail: it is the
    pixel that closes the rarest ``min_area_km2`` of the event, so at least
    that much ground had rain as rare or rarer.
    ``first`` and ``last`` are the hours some window was over its level,
    ``area_km2`` the largest area over the level in one hour, ``bbox`` all the
    ground the event touched.

    ``radar_mm`` and ``merging_mm`` are filled by :func:`with_merging`.
    """

    time: datetime
    window: int
    lon: float
    lat: float
    mm: float
    level_mm: float
    return_period: float
    first: datetime
    last: datetime
    area_km2: float
    bbox: BBox
    radar_mm: float | None = None
    merging_mm: float | None = None

    @property
    def start(self) -> datetime:
        """When the rarest window began."""
        return self.time - timedelta(hours=self.window)

    @property
    def times_level(self) -> float:
        """The rain as a multiple of the level: it ranks events at the 50-year ceiling."""
        return self.mm / self.level_mm

    @property
    def confirmed(self) -> bool | None:
        """Whether the «Merging» saw the rain too; ``None`` before :func:`with_merging`."""
        if self.radar_mm is None or self.merging_mm is None:
            return None
        return self.merging_mm >= CONFIRMED_SHARE * self.radar_mm


@dataclass
class _Open:
    """An event while the frames are still being read (pixels, not degrees)."""

    key: float
    time: datetime
    window: int
    row: int
    col: int
    mm: float
    level: float
    years: float
    first: datetime
    last: datetime
    area: float
    box: tuple[int, int, int, int]

    def take(self, other: _Open) -> None:
        if other.key > self.key:
            self.key, self.time, self.window = other.key, other.time, other.window
            self.row, self.col, self.mm = other.row, other.col, other.mm
            self.level, self.years = other.level, other.years
        self.first, self.last = min(self.first, other.first), max(self.last, other.last)
        self.area = max(self.area, other.area)
        a, b = self.box, other.box
        self.box = (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))


def _on(grid: RainGrid, source: np.ndarray, like: Climatology) -> np.ndarray:
    """An array of the climatology on the pixels of ``grid``."""
    out = np.full(grid.data.shape, np.nan, dtype="float32")
    reproject(
        source.astype("float32"),
        out,
        src_transform=like.transform,
        src_crs=like.crs,
        src_nodata=np.nan,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    return out


def rare_events(
    frames: Iterable[RainGrid],
    *,
    years: float = YEARS,
    min_area_km2: float = MIN_AREA_KM2,
    min_reliability: float = MIN_RELIABILITY,
    inside: np.ndarray | None = None,
    source: Climatology | None = None,
) -> list[Event]:
    """The rare rain of a run of hourly frames, the rarest first.

    ``frames``: one-hour accumulations (``SRT1``) on one grid, in order of
    time. An hour that is missing makes the windows over it unknown, so pass
    what there is and nothing else. ``years``: the return period from which
    rain is rare. ``min_reliability``: 0 keeps every pixel. ``inside``: an
    array of the shape of the frames, true on the pixels to look at (the land,
    a region); the others are left out. ``source``: a climatology other than
    the one shipped.
    """
    ndimage = _ndimage()
    like = source if source is not None else climatology()
    if not years > 1:
        raise DpcRadarError("a return period is over 1 year")
    span = max(WINDOWS)
    first: RainGrid | None = None
    ring = np.empty(0, dtype="float32")
    fits: dict[int, tuple[np.ndarray, ...]] = {}
    keep = np.empty(0, dtype=bool)
    km2 = np.empty(0)
    opened: list[_Open] = []
    parent: list[int] = []
    before: tuple[datetime, np.ndarray] | None = None
    last_hour = -1

    def root(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for grid in frames:
        if grid.product.hours != 1:
            raise DpcRadarError(f"{grid.product.name} is not an hourly accumulation")
        if first is None:
            first = grid
            ring = np.full((span, *grid.data.shape), np.nan, dtype="float32")
            for window in WINDOWS:
                p = like.parts[window]
                loc, scale, shape = (_on(grid, p[name], like) for name in ("loc", "scale", "shape"))
                fits[window] = (loc, scale, shape, _gev_level(loc, scale, shape, years))
            keep = np.ones(grid.data.shape, dtype=bool)
            if min_reliability > 0:
                with np.errstate(invalid="ignore"):
                    keep = reliability_map().like(grid) >= min_reliability
            if inside is not None:
                if inside.shape != grid.data.shape:
                    raise DpcRadarError("``inside`` has not the shape of the frames")
                keep &= inside.astype(bool)
            km2 = _pixel_km2(grid)
        if grid.data.shape != first.data.shape or grid.transform != first.transform:
            raise DpcRadarError("frames on different grids cannot be followed")
        hour, rest = divmod(grid.time - first.time, _HOUR)
        if rest or hour <= last_hour:
            raise DpcRadarError("the frames must be hourly and in order of time")
        for missing in range(max(last_hour + 1, hour - span), hour):
            ring[missing % span] = np.nan
        last_hour = hour
        ring[hour % span] = grid.data

        sums = {
            w: ring[[(hour - j) % span for j in range(w)]].sum(axis=0, dtype="float32")
            for w in WINDOWS
        }
        over = np.zeros(grid.data.shape, dtype=bool)
        with np.errstate(invalid="ignore"):
            for w in WINDOWS:
                over |= sums[w] >= fits[w][3]
        over &= keep
        labels, count = ndimage.label(over, structure=np.ones((3, 3)))
        if count == 0:
            before = None
            continue
        rows, cols = np.nonzero(labels)
        patch = labels[rows, cols]
        area = np.bincount(patch, weights=km2[rows], minlength=count + 1)
        large = area[patch] >= min_area_km2
        rows, cols, patch = rows[large], cols[large], patch[large]
        if patch.size == 0:
            before = None
            continue
        ids = np.zeros(grid.data.shape, dtype=np.int32)

        # Of every pixel, the window where its rain is rarest: by return period, then by
        # how far over the level it is (the return period stops at a ceiling).
        key = np.full((len(WINDOWS), patch.size), -1.0)
        rare = np.zeros_like(key)
        for n, w in enumerate(WINDOWS):
            loc, scale, shape, level = (a[rows, cols].astype(np.float64) for a in fits[w])
            mm = sums[w][rows, cols].astype(np.float64)
            rare[n] = _gev_years(loc, scale, shape, mm)
            with np.errstate(invalid="ignore", divide="ignore"):
                key[n] = np.nan_to_num(rare[n] * 1e3 + np.minimum(mm / level, 999.0), nan=-1.0)
        best = key.argmax(axis=0)
        top = key.max(axis=0)

        linked = np.zeros(patch.size, dtype=np.int32)
        if before is not None and grid.time - before[0] == _HOUR:
            linked = before[1][rows, cols]
        for k in np.unique(patch):
            inside = patch == k
            r, c = rows[inside], cols[inside]
            # Not the rarest pixel, which may be an echo: the rarest ``min_area_km2``.
            order = np.argsort(-top[inside], kind="stable")
            reached = int(np.searchsorted(np.cumsum(km2[r[order]]), min_area_km2))
            i = int(order[min(reached, order.size - 1)])
            w = WINDOWS[int(best[inside][i])]
            now = _Open(
                float(top[inside][i]),
                grid.time,
                w,
                int(r[i]),
                int(c[i]),
                float(sums[w][r[i], c[i]]),
                float(fits[w][3][r[i], c[i]]),
                float(rare[int(best[inside][i]), np.flatnonzero(inside)[i]]),
                grid.time,
                grid.time,
                float(area[k]),
                (int(r.min()), int(r.max()) + 1, int(c.min()), int(c.max()) + 1),
            )
            owners = sorted({root(int(o) - 1) for o in np.unique(linked[inside]) if o > 0})
            if not owners:
                owners = [len(opened)]
                opened.append(now)
                parent.append(owners[0])
            else:
                opened[owners[0]].take(now)
            for other in owners[1:]:  # two events grew into each other
                opened[owners[0]].take(opened[other])
                parent[other] = owners[0]
            ids[r, c] = owners[0] + 1
        before = (grid.time, ids)

    if first is None:
        return []
    done = [e for k, e in enumerate(opened) if parent[k] == k]
    t = first.transform
    events = []
    for e in done:
        r0, r1, c0, c1 = e.box
        xs = [t.a * (e.col + 0.5) + t.c, *(t.a * c + t.c for c in (c0, c1, c0, c1))]
        ys = [t.e * (e.row + 0.5) + t.f, *(t.e * r + t.f for r in (r0, r0, r1, r1))]
        lons, lats = warp_points(first.crs, "EPSG:4326", xs, ys)
        events.append(
            Event(
                e.time,
                e.window,
                round(lons[0], 4),
                round(lats[0], 4),
                round(e.mm, 1),
                round(e.level, 1),
                round(e.years, 1),
                e.first,
                e.last,
                round(e.area, 1),
                (min(lons[1:]), min(lats[1:]), max(lons[1:]), max(lats[1:])),
            )
        )
    return sorted(events, key=lambda e: (-e.return_period, -e.times_level))


def with_merging(event: Event, archive: MergingArchive) -> Event:
    """``event`` with what the radar alone and the «Merging» say of its rarest window.

    The «Merging» comes in three-hour blocks (``CUM3``), so both are summed
    over the blocks that cover the window, and each is the highest value within
    5 km of the rarest pixel: the two grids are not the same, and a storm core
    falls a pixel apart in them. Unchanged when the archive lacks a frame.
    """
    end = event.time + timedelta(hours=(-event.time.hour) % 3)
    start = event.start - timedelta(hours=event.start.hour % 3)
    box = (
        event.lon - _AROUND,
        event.lat - _AROUND,
        event.lon + _AROUND,
        event.lat + _AROUND,
    )
    totals = []
    for product, step in (("SRT1", 1), ("CUM3", 3)):
        parts = []
        for k in range(step, int((end - start) / _HOUR) + 1, step):
            grid = archive.read(product, start + timedelta(hours=k))
            if grid is None:
                return event
            parts.append(grid.crop(box).data)
        total = np.sum(parts, axis=0)
        if not np.isfinite(total).any():
            return event
        totals.append(round(float(np.nanmax(total)), 1))
    return replace(event, radar_mm=totals[0], merging_mm=totals[1])


__all__ = ["CONFIRMED_SHARE", "MIN_RELIABILITY", "YEARS", "Event", "rare_events", "with_merging"]
