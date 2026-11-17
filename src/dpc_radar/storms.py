"""Storms as objects: where each one is, how large, how strong, and where it goes.

A frame is a field of pixels; a storm is the patch of touching pixels at or
over a threshold. :func:`storms` finds the patches of one frame and describes
each one (:class:`Storm`): centre, area, peak, mean. :func:`track` follows
them from frame to frame (:class:`Track`): how long each storm lived, the path
of its centre, its speed, the water it dropped.

Two patches of consecutive frames are the same storm when they overlap: with a
frame every five minutes a storm of a few km moves less than its own width.
When a storm splits, the piece that overlaps it most keeps its track and the
others start a new one; when two merge, the track of the one that overlaps
most goes on and the other ends. A gap between frames longer than ``max_gap``
ends every track: after half an hour a patch in the same place is another storm.

The threshold is the caller's: 10 mm/h on an SRI frame draws the convective
cores, 1 mm/h the whole rain area. Patches smaller than ``min_area_km2`` are
dropped: a few pixels over the threshold are more often clutter than rain.

It needs SciPy for the labelling (extra ``storms``,
``pip install "dpc-radar[storms]"``). Radar only sees what the radar sees:
where it is blind (:func:`dpc_radar.reliability`) there are no storms to find.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import BBox, RainGrid

#: Patches smaller than this are dropped unless the caller says otherwise.
MIN_AREA_KM2 = 10.0
#: Frames further apart than this are not linked.
MAX_GAP = timedelta(minutes=20)


def _ndimage() -> Any:
    try:
        from scipy import ndimage  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError('finding storms needs SciPy: pip install "dpc-radar[storms]"') from exc
    return ndimage


@dataclass(frozen=True)
class Storm:
    """One patch of touching pixels at or over ``threshold`` in one frame.

    ``peak`` and ``mean`` are in the unit of the frame (mm/h for SRI, mm for an
    accumulation). ``lon``/``lat`` is the centre of the rain (each pixel
    weighted by its rain), not of the shape.
    """

    time: datetime
    product: str
    threshold: float
    lon: float
    lat: float
    area_km2: float
    peak: float
    peak_lon: float
    peak_lat: float
    mean: float
    bbox: BBox


@dataclass(frozen=True)
class Track:
    """One storm followed through the frames: its :class:`Storm` at every frame it was seen."""

    storms: tuple[Storm, ...]

    @property
    def start(self) -> datetime:
        return self.storms[0].time

    @property
    def end(self) -> datetime:
        return self.storms[-1].time

    @property
    def duration(self) -> timedelta:
        """From the first frame to the last: a storm seen in one frame lasted zero."""
        return self.end - self.start

    @property
    def peak(self) -> float:
        return max(s.peak for s in self.storms)

    @property
    def area_km2(self) -> float:
        """The largest area it reached."""
        return max(s.area_km2 for s in self.storms)

    @property
    def path(self) -> list[tuple[float, float]]:
        """The lon/lat of its centre, frame by frame."""
        return [(s.lon, s.lat) for s in self.storms]

    @property
    def distance_km(self) -> float:
        """From where it was first seen to where it was last seen, in a straight line."""
        return _km(*self.path[0], *self.path[-1])

    @property
    def speed_kmh(self) -> float | None:
        """Its mean speed along that line; ``None`` for a storm seen in one frame."""
        hours = self.duration.total_seconds() / 3600
        return self.distance_km / hours if hours > 0 else None

    @property
    def heading(self) -> float | None:
        """Where it goes, in degrees clockwise from north; ``None`` if it did not move."""
        (lon1, lat1), (lon2, lat2) = self.path[0], self.path[-1]
        east = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
        north = math.radians(lat2 - lat1)
        if east == 0 and north == 0:
            return None
        return math.degrees(math.atan2(east, north)) % 360

    @property
    def water_m3(self) -> float | None:
        """The water it dropped over the threshold area, in m³ (intensity frames only).

        The mean intensity times the area, held from each frame to the next.
        ``None`` for accumulations: their windows overlap and cannot be summed.
        """
        if self.storms[0].product != "SRI":
            return None
        total = 0.0
        for now, after in zip(self.storms, self.storms[1:], strict=False):
            hours = (after.time - now.time).total_seconds() / 3600
            total += now.mean * now.area_km2 * hours * 1000.0  # 1 mm on 1 km² is 1000 m³
        return total


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _pixel_km2(grid: RainGrid) -> np.ndarray:
    """The area of the pixels of every row, in km²."""
    t = grid.transform
    rows = np.arange(grid.data.shape[0])
    if grid.crs.is_geographic:
        lat = t.f + t.e * (rows + 0.5)
        return np.asarray(111.32 * abs(t.a) * 110.57 * abs(t.e) * np.cos(np.radians(lat)))
    return np.full(len(rows), abs(t.a * t.e) / 1e6)


def _find(grid: RainGrid, threshold: float, min_area_km2: float) -> tuple[np.ndarray, list[Storm]]:
    """The patches of a frame: an array with the number of the patch of every pixel, and the storms.

    Pixel value ``k`` (from 1) is ``storms[k - 1]``; 0 is no storm.
    """
    ndimage = _ndimage()
    data = grid.data
    labels, count = ndimage.label(np.nan_to_num(data) >= threshold, structure=np.ones((3, 3)))
    kept = np.zeros_like(labels)
    found: list[Storm] = []
    if count == 0:
        return kept, found
    km2 = _pixel_km2(grid)
    t = grid.transform
    xs: list[float] = []
    ys: list[float] = []
    partial: list[tuple[float, float, float, tuple[int, int, int, int]]] = []
    for k, box in enumerate(ndimage.find_objects(labels), start=1):
        inside = labels[box] == k
        area = float((inside * km2[box[0], None]).sum())
        if area < min_area_km2:
            continue
        values = np.where(inside, data[box], 0.0).astype(np.float64)
        rows, cols = np.nonzero(inside)
        weight = values[rows, cols]
        r = float((rows * weight).sum() / weight.sum()) + box[0].start + 0.5
        c = float((cols * weight).sum() / weight.sum()) + box[1].start + 0.5
        top = int(np.argmax(weight))
        pr, pc = rows[top] + box[0].start + 0.5, cols[top] + box[1].start + 0.5
        xs += [t.a * c + t.c, t.a * pc + t.c]
        ys += [t.e * r + t.f, t.e * pr + t.f]
        mean = float((weight * km2[box[0]][rows]).sum() / area)
        partial.append(
            (
                area,
                float(weight.max()),
                mean,
                (box[0].start, box[0].stop, box[1].start, box[1].stop),
            )
        )
        kept[box][inside] = len(partial)
    if not partial:
        return kept, found
    lons, lats = warp_points(grid.crs, "EPSG:4326", xs, ys)
    for k, (area, peak, mean, (r0, r1, c0, c1)) in enumerate(partial):
        cx = [t.a * c0 + t.c, t.a * c1 + t.c, t.a * c0 + t.c, t.a * c1 + t.c]
        cy = [t.e * r0 + t.f, t.e * r0 + t.f, t.e * r1 + t.f, t.e * r1 + t.f]
        blon, blat = warp_points(grid.crs, "EPSG:4326", cx, cy)
        found.append(
            Storm(
                grid.time,
                grid.product.name,
                threshold,
                round(lons[2 * k], 4),
                round(lats[2 * k], 4),
                round(area, 1),
                peak,
                round(lons[2 * k + 1], 4),
                round(lats[2 * k + 1], 4),
                mean,
                (min(blon), min(blat), max(blon), max(blat)),
            )
        )
    return kept, found


def storms(grid: RainGrid, *, threshold: float, min_area_km2: float = MIN_AREA_KM2) -> list[Storm]:
    """The storms of one frame, the strongest first."""
    return sorted(_find(grid, threshold, min_area_km2)[1], key=lambda s: -s.peak)


def track(
    grids: Iterable[RainGrid],
    *,
    threshold: float,
    min_area_km2: float = MIN_AREA_KM2,
    max_gap: timedelta = MAX_GAP,
) -> list[Track]:
    """The storms of a run of frames of one product, in order of time, followed through it.

    The tracks come in the order they began. Frames on different grids cannot
    be followed and are refused.
    """
    tracks: list[list[Storm]] = []
    before: tuple[RainGrid, np.ndarray, list[int]] | None = None
    for grid in grids:
        labels, found = _find(grid, threshold, min_area_km2)
        owner = [-1] * len(found)
        if before is not None:
            last, last_labels, last_owner = before
            if last.data.shape != grid.data.shape or last.transform != grid.transform:
                raise DpcRadarError("frames on different grids cannot be followed")
            if grid.time <= last.time:
                raise DpcRadarError("the frames must be in order of time")
            if grid.time - last.time <= max_gap and found and last_owner:
                both = (labels > 0) & (last_labels > 0)
                pairs = labels[both].astype(np.int64) * (len(last_owner) + 1) + last_labels[both]
                keys, shared = np.unique(pairs, return_counts=True)
                taken: set[int] = set()
                for i in np.argsort(-shared, kind="stable"):  # the largest overlap chooses first
                    now, then = divmod(int(keys[i]), len(last_owner) + 1)
                    if owner[now - 1] == -1 and last_owner[then - 1] not in taken:
                        owner[now - 1] = last_owner[then - 1]
                        taken.add(last_owner[then - 1])
        for k, storm in enumerate(found):
            if owner[k] == -1:
                owner[k] = len(tracks)
                tracks.append([])
            tracks[owner[k]].append(storm)
        before = (grid, labels, owner)
    return [Track(tuple(t)) for t in tracks]


__all__ = ["MAX_GAP", "MIN_AREA_KM2", "Storm", "Track", "storms", "track"]
