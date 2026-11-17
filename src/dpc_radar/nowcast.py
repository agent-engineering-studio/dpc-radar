"""The rain of the next two hours: the radar field moved forward along its own motion.

The radar shows rain that has already fallen. Over an hour or two most of what
changes is where the rain is, not what it is: the field of now, moved along the
motion it had in the last half hour, is a forecast (an *extrapolation*, the
first step of pySTEPS and of every radar nowcast).

* :func:`motion` measures how the rain moved between the frames it is given.
* :func:`nowcast` gives the intensity (mm/h) at every step up to two hours.
* :func:`rain_ahead` sums it: the millimetres of the next ``minutes``.
* :func:`chance` turns it into a probability: the chance of at least ``mm`` in
  the next ``minutes`` at every pixel.

Everything here returns a :class:`Forecast`, never a :class:`dpc_radar.RainGrid`:
it carries when it was issued and how far ahead it looks, and it cannot be
passed by mistake where observed rain is expected.

**How it works.** The motion is found by matching blocks of 192 km of the
oldest frame of the last half hour with the latest one (the shift that makes
them most alike, on the logarithm of the rain, up to 100 km/h); a block without rain takes the
motion of the nearest one with rain. Each pixel of the forecast then takes the
rain of the place the motion says it comes from (semi-Lagrangian, a step at a
time, so a field that turns is followed along the turn). Where the rain would
come from outside the frame, or from a place without data, the forecast is
``NaN``: not known, which is not dry.

**What it cannot do.** The rain is moved, not changed: a storm that is born
after the last frame is not there, one that dies is still there, and one that
stays over a mountain while the others pass is moved away with them. So the
forecast is good on where the rain area will be and poor on where its peaks
will be, and it gets worse with every minute: the numbers are in the
reference. :func:`chance` is not an ensemble: it is the share of the forecast
around a pixel that is over the threshold, within a distance that grows by
0.2 km a minute, the distance that scored best on the storms of 2021-2025.

It needs SciPy (extra ``nowcast``, ``pip install "dpc-radar[nowcast]"``) and
frames in metres a few minutes apart: the SRI.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from affine import Affine
from rasterio.crs import CRS

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import RainGrid
from dpc_radar.products import SRI

#: Nothing is forecast further than this.
MAX_MINUTES = 120
#: The motion is measured between the last frame and the oldest one within this.
MAX_BASELINE = timedelta(minutes=30)
#: The last two frames cannot be further apart than this.
MAX_STEP = timedelta(minutes=15)
#: Rain is not looked for faster than this.
MAX_SPEED_KMH = 100.0
#: The distance within which :func:`chance` looks around a pixel grows by this every minute.
SPREAD_KM_PER_MINUTE = 0.2

_BLOCK_KM = 192.0
_STRIDE_KM = 64.0
_SMOOTH_KM = 2.0
_WET = 0.5  # mm/h
_MIN_WET = 0.02  # a block with less rain than this share of its pixels says nothing
_MIN_SCORE = 0.3  # nor does a match worse than this
_FLAT = 1e-3  # nor one where the rain has no pattern


def _ndimage() -> Any:
    try:
        from scipy import ndimage  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError('a nowcast needs SciPy: pip install "dpc-radar[nowcast]"') from exc
    return ndimage


def _at(
    data: np.ndarray, transform: Affine, crs: CRS, points: list[tuple[float, float]]
) -> list[float | None]:
    return RainGrid(SRI, datetime.min, data, transform, crs).sample(points)


@dataclass(frozen=True)
class Forecast:
    """Rain that has not fallen yet: one field, when it was issued and how far ahead it is.

    ``unit`` says what ``data`` holds: ``"mm/h"`` (the intensity at ``time``),
    ``"mm"`` (the rain of the ``window`` that ends at ``time``) or
    ``"probability"`` (between 0 and 1: the chance of at least ``threshold``
    mm in the ``window`` that ends at ``time``). ``NaN`` is "not known".
    """

    issued: datetime
    lead: timedelta
    unit: str
    data: np.ndarray
    transform: Affine
    crs: CRS
    window: timedelta | None = None
    threshold: float | None = None

    @property
    def time(self) -> datetime:
        """The time the forecast is for."""
        return self.issued + self.lead

    def sample(self, points: list[tuple[float, float]]) -> list[float | None]:
        """The forecast at each lon/lat point, as :meth:`dpc_radar.RainGrid.sample`."""
        return _at(self.data, self.transform, self.crs, points)


@dataclass(frozen=True)
class Motion:
    """How the rain moved: km/h towards the east and the north of the grid, at every pixel.

    ``found`` is false when no block of the frames had rain enough to be
    followed: the motion is then zero everywhere, and a nowcast is the last
    frame, still.
    """

    east: np.ndarray
    north: np.ndarray
    found: bool
    transform: Affine
    crs: CRS

    def at(self, lon: float, lat: float) -> tuple[float, float] | None:
        """Speed (km/h) and heading (degrees from north, where the rain goes) at a point."""
        east, north = (
            _at(part, self.transform, self.crs, [(lon, lat)])[0] for part in (self.east, self.north)
        )
        if east is None or north is None:
            return None
        return math.hypot(east, north), math.degrees(math.atan2(east, north)) % 360.0


def _frames(grids: Iterable[RainGrid]) -> tuple[RainGrid, RainGrid, float]:
    """The frame the motion starts from, the last one, and the km of a pixel."""
    frames = list(grids)
    if len(frames) < 2:
        raise DpcRadarError("a nowcast needs at least two frames")
    now = frames[-1]
    for before, after in itertools.pairwise(frames):
        if before.product.hours is not None or after.product.hours is not None:
            raise DpcRadarError("a nowcast needs frames of intensity (SRI), not accumulations")
        if before.data.shape != after.data.shape or before.transform != after.transform:
            raise DpcRadarError("frames on different grids cannot be followed")
        if after.time <= before.time:
            raise DpcRadarError("the frames must be in order of time")
    if now.crs.is_geographic:
        raise DpcRadarError("a nowcast needs a grid in metres, not in degrees")
    if now.time - frames[-2].time > MAX_STEP:
        raise DpcRadarError("the last two frames are too far apart to say where the rain goes")
    first = next(f for f in frames if now.time - f.time <= MAX_BASELINE)
    return first, now, abs(now.transform.a) / 1000.0


def _pattern(data: np.ndarray, sigma: float) -> np.ndarray:
    """Rain on a logarithmic scale, 0 where dry or unknown: the pattern, not the peaks."""
    smooth = _ndimage().gaussian_filter(np.nan_to_num(data), sigma) if sigma > 0 else data
    return np.log1p(np.nan_to_num(smooth) / _WET).astype(np.float32)


def _shift(a: np.ndarray, b: np.ndarray, reach: int) -> tuple[float, float, float]:
    """The pixels (rows, columns) the pattern of ``a`` moved to become ``b``, and how alike."""
    if a.std() < _FLAT or b.std() < _FLAT:  # rain without a pattern: nothing to follow
        return 0.0, 0.0, 0.0
    a, b = a - a.mean(), b - b.mean()
    norm = float(np.sqrt((a**2).sum() * (b**2).sum()))
    alike = np.fft.fftshift(np.fft.irfft2(np.fft.rfft2(b) * np.conj(np.fft.rfft2(a)), s=a.shape))
    r0, c0 = a.shape[0] // 2, a.shape[1] // 2
    near = alike[r0 - reach : r0 + reach + 1, c0 - reach : c0 + reach + 1]
    r, c = (int(v) for v in np.unravel_index(np.argmax(near), near.shape))
    rows, cols = float(r - reach), float(c - reach)
    # A parabola through the peak and its two neighbours: a fraction of a pixel.
    if 0 < r < near.shape[0] - 1:
        bend = near[r - 1, c] - 2 * near[r, c] + near[r + 1, c]
        if bend < 0:
            rows += float(0.5 * (near[r - 1, c] - near[r + 1, c]) / bend)
    if 0 < c < near.shape[1] - 1:
        bend = near[r, c - 1] - 2 * near[r, c] + near[r, c + 1]
        if bend < 0:
            cols += float(0.5 * (near[r, c - 1] - near[r, c + 1]) / bend)
    return rows, cols, float(near[r, c]) / norm


def _moved(
    before: np.ndarray, now: np.ndarray, km: float, minutes: float
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Pixels a minute (rows, columns) at every pixel, and whether any block could say."""
    ndimage = _ndimage()
    a, b = _pattern(before, _SMOOTH_KM / km), _pattern(now, _SMOOTH_KM / km)
    h, w = a.shape
    block = min(round(_BLOCK_KM / km), h, w)
    stride = max(round(_STRIDE_KM / km), 1)
    reach = max(min(math.ceil(MAX_SPEED_KMH * minutes / 60.0 / km), block // 2 - 1), 1)
    tops = list(range(0, h - block + 1, stride))
    lefts = list(range(0, w - block + 1, stride))
    rows = np.full((len(tops), len(lefts)), np.nan)
    cols = np.full_like(rows, np.nan)
    for i, top in enumerate(tops):
        for j, left in enumerate(lefts):
            pa = a[top : top + block, left : left + block]
            pb = b[top : top + block, left : left + block]
            if (pa > 0).mean() < _MIN_WET or (pb > 0).mean() < _MIN_WET:
                continue
            dr, dc, score = _shift(pa, pb, reach)
            if score >= _MIN_SCORE:
                rows[i, j], cols[i, j] = dr / minutes, dc / minutes
    known = np.isfinite(rows)
    if not known.any():
        still = np.zeros((h, w), np.float32)
        return still, still, False
    # A block without rain takes the motion of the nearest block with rain.
    nearest = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    rows, cols = rows[tuple(nearest)], cols[tuple(nearest)]
    if min(rows.shape) >= 3:  # one block that disagrees with all its neighbours is wrong
        rows = ndimage.median_filter(rows, 3, mode="nearest")
        cols = ndimage.median_filter(cols, 3, mode="nearest")
    # From the centres of the blocks to every pixel.
    at = np.meshgrid(
        np.clip((np.arange(h) - block / 2) / stride, 0, len(tops) - 1),
        np.clip((np.arange(w) - block / 2) / stride, 0, len(lefts) - 1),
        indexing="ij",
    )
    return (
        ndimage.map_coordinates(rows, at, order=1, mode="nearest").astype(np.float32),
        ndimage.map_coordinates(cols, at, order=1, mode="nearest").astype(np.float32),
        True,
    )


def _ahead(now: np.ndarray, rows: np.ndarray, cols: np.ndarray, steps: int) -> list[np.ndarray]:
    """``now`` after 1, 2, ... ``steps`` steps of the motion (pixels a step)."""
    ndimage = _ndimage()
    h, w = now.shape
    here = np.mgrid[0:h, 0:w].astype(np.float32)
    back = np.zeros((2, h, w), np.float32)
    rain = np.nan_to_num(now)
    known = np.isfinite(now).astype(np.float32)
    out = []
    for _ in range(steps):
        at = here - back
        back[0] += ndimage.map_coordinates(rows, at, order=1, mode="nearest")
        back[1] += ndimage.map_coordinates(cols, at, order=1, mode="nearest")
        at = here - back
        moved = ndimage.map_coordinates(rain, at, order=1, mode="constant", cval=0.0)
        seen = ndimage.map_coordinates(known, at, order=1, mode="constant", cval=0.0)
        out.append(np.where(seen >= 0.5, moved, np.nan).astype(np.float32))
    return out


def _run(grids: Iterable[RainGrid], minutes: int) -> tuple[RainGrid, timedelta, list[np.ndarray]]:
    frames = list(grids)
    first, now, km = _frames(frames)
    step = now.time - frames[-2].time
    if not step <= timedelta(minutes=minutes) <= timedelta(minutes=MAX_MINUTES):
        raise DpcRadarError(
            f"a nowcast goes from one step ({step}) to {MAX_MINUTES} minutes, not to {minutes}"
        )
    rows, cols, _ = _moved(first.data, now.data, km, (now.time - first.time) / timedelta(minutes=1))
    per_step = step / timedelta(minutes=1)
    steps = int(timedelta(minutes=minutes) / step)
    return now, step, _ahead(now.data, rows * per_step, cols * per_step, steps)


def motion(grids: Iterable[RainGrid]) -> Motion:
    """How the rain moved between the last frame and the oldest one of its last half hour."""
    first, now, km = _frames(grids)
    rows, cols, found = _moved(
        first.data, now.data, km, (now.time - first.time) / timedelta(minutes=1)
    )
    return Motion(cols * km * 60.0, -rows * km * 60.0, found, now.transform, now.crs)


def nowcast(grids: Iterable[RainGrid], *, minutes: int = MAX_MINUTES) -> list[Forecast]:
    """The intensity (mm/h) of the next ``minutes``, one :class:`Forecast` per step.

    ``grids`` are frames of intensity on one grid in order of time, the last
    one being now; the step is the interval between the last two (five minutes
    for the SRI). Give the frames of the last half hour: with only two the
    motion is measured on five minutes, and it is less sure.
    """
    now, step, fields = _run(grids, minutes)
    return [
        Forecast(now.time, step * (k + 1), "mm/h", field, now.transform, now.crs)
        for k, field in enumerate(fields)
    ]


def _sum(grids: Iterable[RainGrid], minutes: int) -> tuple[RainGrid, timedelta, np.ndarray]:
    now, step, fields = _run(grids, minutes)
    total = np.sum(fields, axis=0, dtype=np.float32) * np.float32(step / timedelta(hours=1))
    return now, step * len(fields), total


def rain_ahead(grids: Iterable[RainGrid], *, minutes: int = 60) -> Forecast:
    """The millimetres of the next ``minutes``: the nowcast, summed.

    ``NaN`` where any step of it is not known.
    """
    now, window, total = _sum(grids, minutes)
    return Forecast(now.time, window, "mm", total, now.transform, now.crs, window)


def _share(total: np.ndarray, mm: float, reach: int) -> np.ndarray:
    """The share of the known pixels within ``reach`` pixels of each one that reach ``mm``."""
    ndimage = _ndimage()
    known = np.isfinite(total)
    size = 2 * reach + 1
    over = ndimage.uniform_filter((total >= mm).astype(np.float32), size, mode="constant")
    seen = ndimage.uniform_filter(known.astype(np.float32), size, mode="constant")
    share = np.where(known, np.clip(over / np.maximum(seen, 1e-6), 0.0, 1.0), np.nan)
    return share.astype(np.float32)


def chance(grids: Iterable[RainGrid], *, mm: float, minutes: int = 60) -> Forecast:
    """The chance (0 to 1) of at least ``mm`` in the next ``minutes``, at every pixel.

    It is the share of :func:`rain_ahead` that reaches ``mm`` within
    ``SPREAD_KM_PER_MINUTE * minutes`` km of the pixel (12 km for an hour):
    the forecast is rarely wrong on whether a storm is around, and often on
    where exactly. ``NaN`` where the forecast itself is not known.
    """
    now, window, total = _sum(grids, minutes)
    reach = round(SPREAD_KM_PER_MINUTE * minutes / (abs(now.transform.a) / 1000.0))
    share = _share(total, mm, reach)
    return Forecast(
        now.time,
        window,
        "probability",
        share,
        now.transform,
        now.crs,
        window,
        mm,
    )


__all__ = [
    "MAX_MINUTES",
    "SPREAD_KM_PER_MINUTE",
    "Forecast",
    "Motion",
    "chance",
    "motion",
    "nowcast",
    "rain_ahead",
]
