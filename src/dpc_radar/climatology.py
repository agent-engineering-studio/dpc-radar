"""How rare is this rain, here? A rain climatology of Italy from eight years of radar.

Fifty millimetres in an hour are a storm of every summer in Genoa and a rare
one in Bari. :func:`rarity` says how rare a rain is at a place, for a window of
1, 3, 6 or 24 hours::

    from dpc_radar import rarity, return_level, return_period

    rarity(24, 8.93, 44.41, 100.0).return_period  # 1.7   Genoa: most years
    rarity(24, 16.87, 41.12, 100.0)  # Bari: 32 years, between 9 and 50 or more
    return_level(24, 8.93, 44.41, 10)  # Level(mm=203.1, low=166.6, high=264.1)
    years = return_period(srt1)  # the return period of every pixel of a frame

Two answers, in a :class:`Rarity`:

* ``percentile``: the share of the windows of those years with less rain
  (99.9: one window in a thousand had more). It counts, it assumes nothing,
  and it stops at 99.99.
* ``return_period``: once in how many years the highest window of the year
  passes that rain, from a GEV distribution fitted to the highest window of
  each year, with the 90 % interval of the fit (``low``, ``high``). It is 1
  for the rain of every year and stops at :data:`MAX_RETURN_YEARS`.

:func:`return_level` is the same read backwards: the millimetres of a return
period. :func:`return_period` does it for every pixel of an accumulation frame.

**What it is made of.** The IT-DPC-SRI history (:mod:`dpc_radar.history`), from
2018 to 2025: the rain of every hour at 1 km, without the corrupted frames,
summed over the windows that end at every hour. The map ships with the package
(1.6 MB) in cells of 5 km: about 35,000 cells with a fit, wherever the mosaic
has data in at least six of the eight years. In every cell the location of the
GEV is the mean of its pixels, the spread over the location is the one of the
55 km around it, and the shape is one for the whole of Italy (0.20 for 1 h,
0.19 for 3 h, 0.17 for 6 h, 0.13 for 24 h): eight values a pixel cannot say
more. ``tools/build_climatology.py`` builds it and measures it.

**Why eight years and not sixteen.** The history is not one series. The highest
hour of the year, over the location of the fit, has a median of 0.64 to 0.90 in
the years 2010-2017 (1.14 in 2014, the year with most corrupted frames) and of
1.09 to 1.35 since 2018: until 2017 the mosaic read about two thirds of what it
reads now. A fit on all the years puts 61 % of the pixels over their 2-year
level in five months of 2026. So the map stands on the years that read like today, and pays for
it with a wider doubt.

**How far to trust it** (measured; the tables are in the reference):

* Fitted on six years and asked about the other two, the levels are passed
  more often than they say: the 2-year level by 52 % of the pixels in a year
  (it should be 50), the 5-year by 24 % (20), the 10-year by 14 % (10), the
  20-year by 8 % (5). What it calls "10 years" came about every 7, "20 years"
  every 13. Beyond that nothing can be checked with eight years: 50 years is an
  extrapolation, and no number is given further.
* On the hourly radar frames of June-November 2026 (``SRT1``), which it was
  not built on, the 10-year level of 1 h was passed in five months by 12 % of
  the pixels, about what a held-out year does; the one of 24 h by 20 %: on
  long windows the frames of 2026 read more than the history.
* The interval is the doubt of eight years only (the years drawn again, a
  hundred times). It does not know the errors of the radar.

**It is in radar millimetres.** The history is radar alone, and the radar alone
is not a rain gauge: in 2026 it read 20 % more rain than the «Merging»
(:mod:`dpc_radar.bias`), much more in some places, less in others. So:

* do not compare these levels with those of rain gauges (the official
  depth-duration-frequency curves): in the Po valley the radar reads 60 mm in
  an hour once in ten years;
* on a radar-only frame (``SRT1``, :func:`dpc_radar.radar_total`) the return
  period inherits the faults of the frame: in the 24 hours to 2 November 2026
  09:00 UTC the radar alone was over the 10-year level on 35,000 km², the
  «Merging» on 19,000, and the highest radar pixel (382 mm) had 7 mm in the
  «Merging». Clean the frame first (:meth:`dpc_radar.RainGrid.quality`) and
  look at :func:`dpc_radar.reliability`;
* on a «Merging» frame (``CUM3``, ``CUM6``, ``CUM24``) the answer leans to
  "less rare" where the radar reads more than the gauges, which is most of the
  land.

Where the radar is blind the map has less rain than falls, and outside the
mosaic it has nothing (``None``, NaN). What the cleaning of the history did not
catch is in the map: a ray of clutter west of the radar of Sicily shows in it.
It is not a design rainfall and it does not replace the official alert.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from importlib.resources import as_file, files
from typing import Final

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import RainGrid

#: The file of the map inside the package.
CLIMATOLOGY_FILE: Final = "climatology.tif"
#: The windows the climatology knows, in hours.
WINDOWS: Final = (1, 3, 6, 24)
#: The percentiles kept for every window.
PERCENTILES: Final = (90.0, 99.0, 99.9, 99.99)
#: The return periods at which the uncertainty of the fit is kept, in years.
SPREAD_YEARS: Final = (2, 50)
#: No return period is given beyond this: eight years cannot tell further.
MAX_RETURN_YEARS: Final = 50.0
#: The bands of the file for every window, in order. 16-bit: millimetres in
#: tenths, ``shape`` as (shape + 1) in hundredths, the ``low``/``high`` ratios
#: in hundredths, 65535 is "not known".
BANDS: Final = (
    "loc",
    "scale",
    "shape",
    "years",
    "p90",
    "p99",
    "p99.9",
    "p99.99",
    "low2",
    "low50",
    "high2",
    "high50",
)
_MM_STEP = 0.1
_RATIO_STEP = 0.01
_NODATA = 65535
#: The return periods on which the interval of a rarity is looked up.
_LADDER = np.geomspace(1.01, MAX_RETURN_YEARS, 60)


@dataclass(frozen=True)
class Level:
    """The rain of a return period at a place, in mm, with its 90 % interval."""

    mm: float
    low: float
    high: float


@dataclass(frozen=True)
class Rarity:
    """How rare ``mm`` of rain in ``window`` hours are at a place.

    ``return_period`` is in years, between 1 ("every year") and
    :data:`MAX_RETURN_YEARS` ("that or more"); ``low`` and ``high`` are its
    90 % interval. ``percentile`` is the share of the windows of those
    years with less rain, between 90 ("90 or less") and 99.99 ("that or more").
    All ``None`` where the climatology has no data.
    """

    window: int
    mm: float
    return_period: float | None
    low: float | None
    high: float | None
    percentile: float | None
    #: The years of radar the fit stands on at this place (0: none).
    years: int


def _fit(part: dict[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The three numbers of the GEV of one cell."""
    loc, scale, shape = (np.asarray(part[k], dtype=np.float64) for k in ("loc", "scale", "shape"))
    return loc, scale, shape


def _not_zero(shape: np.ndarray) -> np.ndarray:
    """A shape of zero (a Gumbel) as the GEV next to it, which the formulas can divide by."""
    out: np.ndarray = np.where(np.abs(shape) < 1e-6, 1e-6, shape)
    return out


def _gev_level(loc: np.ndarray, scale: np.ndarray, shape: np.ndarray, years: float) -> np.ndarray:
    y = -math.log(1 - 1 / years)
    shape = _not_zero(shape)
    out: np.ndarray = loc + scale / shape * (y**-shape - 1)
    return out


def _gev_years(loc: np.ndarray, scale: np.ndarray, shape: np.ndarray, mm: np.ndarray) -> np.ndarray:
    """The return period of ``mm``, within 1 and :data:`MAX_RETURN_YEARS` (NaN: not known)."""
    shape = _not_zero(shape)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        z = 1 + shape * (mm - loc) / scale
        # Under the lower end of a heavy tail: every year. Over the upper end of a short one: never.
        never = np.exp(-(np.maximum(z, 1e-12) ** (-1 / shape)))
        below = np.where(z > 0, never, np.where(shape > 0, 0.0, 1.0))
        years = 1 / np.maximum(1 - below, 1 / MAX_RETURN_YEARS)
    out: np.ndarray = np.where(np.isfinite(z), np.clip(years, 1, MAX_RETURN_YEARS), np.nan)
    return out


@dataclass(frozen=True)
class Climatology:
    """The climatology as arrays on a grid of 5 km cells (NaN: not known).

    ``parts[window][band]`` for every window of :data:`WINDOWS` and band of
    :data:`BANDS`: the GEV of the highest window of the year (``loc`` and
    ``scale`` in mm, ``shape`` positive for a heavy tail), the ``years`` it
    stands on, the percentiles in mm, and the 90 % interval of the 2- and
    50-year levels as ratios to the level of the fit.
    """

    parts: dict[int, dict[str, np.ndarray]]
    transform: Affine
    crs: CRS
    #: The years of radar frames the climatology was built on.
    built_on: str

    def _cell(self, lon: float, lat: float) -> tuple[int, int] | None:
        xs, ys = warp_points("EPSG:4326", self.crs, [lon], [lat])
        inv = ~self.transform  # written out: affine 2 has no ``@``, affine 3 deprecates ``*``
        r, c = math.floor(inv.e * ys[0] + inv.f), math.floor(inv.a * xs[0] + inv.c)
        h, w = self.parts[WINDOWS[0]]["loc"].shape
        return (r, c) if 0 <= r < h and 0 <= c < w else None

    def _window(self, window: int) -> dict[str, np.ndarray]:
        if window not in self.parts:
            raise DpcRadarError(f"no climatology for {window} h: the windows are {WINDOWS}")
        return self.parts[window]

    def _curves(self, part: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
        """The low and the high end of the level at every return period of the ladder."""
        loc, scale, shape = _fit(part)
        fit = np.array([_gev_level(loc, scale, shape, float(t)) for t in _LADDER])
        where = np.log(_LADDER)
        ends = [math.log(t) for t in SPREAD_YEARS]
        low = fit * np.interp(where, ends, [part[f"low{t}"] for t in SPREAD_YEARS])
        high = fit * np.interp(where, ends, [part[f"high{t}"] for t in SPREAD_YEARS])
        return np.maximum.accumulate(low), np.maximum.accumulate(high)

    def level(self, window: int, lon: float, lat: float, years: float) -> Level | None:
        """The rain in ``window`` hours passed once in ``years`` years at a point.

        ``years`` between 1 (not included) and :data:`MAX_RETURN_YEARS`; ``None``
        where the climatology has no data.
        """
        if not 1 < years <= MAX_RETURN_YEARS:
            raise DpcRadarError(f"a return period is over 1 and up to {MAX_RETURN_YEARS:g} years")
        parts, cell = self._window(window), self._cell(lon, lat)
        if cell is None or math.isnan(parts["loc"][cell]):
            return None
        part = {name: float(a[cell]) for name, a in parts.items()}
        mm = float(_gev_level(*_fit(part), years))
        low, high = self._curves(part)
        where = math.log(years)
        return Level(
            round(mm, 1),
            round(float(np.interp(where, np.log(_LADDER), low)), 1),
            round(float(np.interp(where, np.log(_LADDER), high)), 1),
        )

    def rarity(self, window: int, lon: float, lat: float, mm: float) -> Rarity:
        """How rare ``mm`` of rain in ``window`` hours are at a point."""
        parts, cell = self._window(window), self._cell(lon, lat)
        if cell is None or math.isnan(parts["p99"][cell]):
            return Rarity(window, mm, None, None, None, None, 0)
        part = {name: float(a[cell]) for name, a in parts.items()}
        nodes = [part[f"p{p:g}"] for p in PERCENTILES]
        # Between two percentiles the share of windows over a rain falls as a power of it.
        over = float(
            np.interp(
                math.log(max(mm, 1e-6)),
                [math.log(max(n, 1e-6)) + 1e-9 * i for i, n in enumerate(nodes)],
                [math.log(1 - p / 100) for p in PERCENTILES],
            )
        )
        percentile = round(100 * (1 - math.exp(over)), 4)
        if math.isnan(part["loc"]):
            return Rarity(window, mm, None, None, None, percentile, 0)
        years = float(_gev_years(*_fit(part), np.asarray(mm, dtype=np.float64)))
        low, high = self._curves(part)
        # Where the high end of the levels reaches ``mm`` the rain is the least rare it can be.
        least = float(np.interp(mm, high, _LADDER, left=1.0, right=MAX_RETURN_YEARS))
        most = float(np.interp(mm, low, _LADDER, left=1.0, right=MAX_RETURN_YEARS))
        return Rarity(
            window,
            mm,
            round(years, 1),
            round(min(least, years), 1),
            round(max(most, years), 1),
            percentile,
            int(part["years"]),
        )

    def like(self, grid: RainGrid, window: int | None = None) -> np.ndarray:
        """The return period, in years, of the rain of every pixel of an accumulation frame.

        The window is the one of the product (``SRT1``: 1 h, ``CUM3``: 3 h, ...)
        unless ``window`` says how many hours the millimetres of the frame were
        summed over (:func:`dpc_radar.radar_total` of 24 hourly frames: 24).
        Between 1 and :data:`MAX_RETURN_YEARS`; NaN where the frame or the
        climatology has no data.
        """
        if grid.product.hours is None:
            raise DpcRadarError(f"{grid.product.name} is an intensity: it has no window")
        parts = self._window(grid.product.hours if window is None else window)
        fit = []
        for name in ("loc", "scale", "shape"):
            out = np.full(grid.data.shape, np.nan, dtype="float32")
            reproject(
                parts[name].astype("float32"),
                out,
                src_transform=self.transform,
                src_crs=self.crs,
                src_nodata=np.nan,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=np.nan,
                resampling=Resampling.nearest,
            )
            fit.append(out.astype(np.float64))
        years = _gev_years(fit[0], fit[1], fit[2], grid.data.astype(np.float64))
        return years.astype("float32")


@cache
def climatology() -> Climatology:
    """The climatology shipped with the package (read once)."""
    with (
        as_file(files("dpc_radar") / "data" / CLIMATOLOGY_FILE) as path,
        rasterio.open(path) as src,
    ):
        raw = src.read()
        transform, crs, built_on = src.transform, src.crs, src.tags().get("built_on", "")
    parts: dict[int, dict[str, np.ndarray]] = {}
    for n, window in enumerate(WINDOWS):
        parts[window] = {}
        for b, name in enumerate(BANDS):
            band = raw[n * len(BANDS) + b]
            if name == "years":
                value = band.astype(np.float64)
            elif name == "shape":
                value = band * _RATIO_STEP - 1
            elif name.startswith(("low", "high")):
                value = band * _RATIO_STEP
            else:
                value = band * _MM_STEP
            parts[window][name] = np.where(band == _NODATA, np.nan, value)
    return Climatology(parts, transform, crs, built_on)


def rarity(window: int, lon: float, lat: float, mm: float) -> Rarity:
    """How rare ``mm`` of rain in ``window`` hours (1, 3, 6 or 24) are at a point."""
    return climatology().rarity(window, lon, lat, mm)


def return_level(window: int, lon: float, lat: float, years: float) -> Level | None:
    """The rain in ``window`` hours passed once in ``years`` years at a point."""
    return climatology().level(window, lon, lat, years)


def return_period(grid: RainGrid, window: int | None = None) -> np.ndarray:
    """The return period in years of the rain of every pixel of an accumulation frame.

    ``window``: the hours the frame was summed over, when not those of its product.
    """
    return climatology().like(grid, window)
