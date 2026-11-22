"""How far the radar alone is from the «Merging», place by place, and a factor for it.

The radar measures an echo, not rain. Summed over the months from June to
November 2026 it reads 20 % more rain than the «Merging» of radar and rain
gauges (33 % more on land), and it passes 30 mm in three hours 70 % more often.
Not everywhere in the same direction: around Rome, Genoa and Milan it reads
1.6-1.7 times the «Merging», around Palermo less than half of it.

:func:`bias_map` is the ratio «Merging» / radar at every km of land where the
two can be compared: the rain of CUM3 over the rain of the three ``SRT1`` hours
of the same window, both summed over the months and over 9 km. :func:`correct`
multiplies a radar-only frame by it. The map ships with the package;
``tools/build_bias.py`` builds it again and measures it.

What the measure says (every month corrected with the map of the other months,
against CUM3, three-hour windows with at least 1 mm): the mean error goes from
1.91 to 1.55 mm, the total from 1.20 to 1.00 of the «Merging», and 30 mm is
passed 0.89 times as often as in the «Merging» instead of 1.70. One factor for
the whole of Italy does not reduce the error at all: the bias is local.

What it does not do, and where it is 1 (no correction):

* it does not find rain the radar did not see: zero times a factor is zero.
  Where the radar is blind (:func:`dpc_radar.reliability`) it stays blind;
* it corrects how much, not where: on land the correlation with the «Merging»
  stays at 0.72;
* it lowers heavy rain: where the «Merging» has 30 mm or more, the radar alone
  reads 0.91 of it and the corrected radar 0.75. In July and August, the
  months of the storms, the corrected radar passes 30 mm half to two thirds as
  often as the «Merging». Use it for totals and for how often a threshold is
  passed over a season, not for the peak of one storm;
* in Sicily it helps little (mean error from 3.58 to 3.45 mm): the radar sees
  the west of the island badly, and a factor cannot repair that. No factor is
  above :data:`FACTOR_MAX`;
* over the sea there are no gauges, and the «Merging» is the radar: factor 1;
* where a rain gauge reports rain under a dry sky most of the time (near Foggia
  one did so in 504 of 573 wet windows) the comparison says nothing: factor 1,
  on 2,660 km² in all. The ``agreement`` of :mod:`dpc_radar.reliability` is
  not known there, for the same reason;
* the «Merging» contains the radar: it is the best field there is, not an
  independent truth;
* it describes the months it was built on (:attr:`BiasMap.built_on`). The hour
  of the day changes nothing (the ratio stays within 1.18-1.23); the month
  does (1.11-1.33), and the map does not follow it. It was measured on windows
  of three hours: on one hour or on an intensity it is an assumption;
* it is not applied to the 2010-2025 history (:mod:`dpc_radar.history`): the
  radars, their calibration and the mosaic changed over those years, and there
  is no «Merging» of those years to test it on.

A model with more inputs (gradient boosting on place, beam height, hour and
month) had about the same mean error and was not kept: it passed 50 mm 0.40
times as often as the «Merging» and almost never 100 mm. A corrected field
that cannot rain hard is of no use to a warning system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
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

#: The file of the map inside the package: one band of bytes, the factor in
#: steps of 0.02; 255 outside the mosaic.
BIAS_FILE: Final = "bias.tif"
#: The factor never goes beyond these. Above 2 it would multiply the little the
#: radar sees where it sees badly: measured in Sicily, that adds false storms.
FACTOR_MIN: Final = 0.2
FACTOR_MAX: Final = 2.0
_STEP = 0.02
_NODATA = 255


@dataclass(frozen=True)
class BiasMap:
    """The factor «Merging» / radar on the 1 km grid of the mosaic (NaN outside it)."""

    factor: np.ndarray
    transform: Affine
    crs: CRS
    #: The months of frames the map was built on.
    built_on: str

    def at(self, lon: float, lat: float) -> float | None:
        """The factor at a point: 1.0 where there is nothing to correct with, ``None`` outside."""
        xs, ys = warp_points("EPSG:4326", self.crs, [lon], [lat])
        inv = ~self.transform  # written out: affine 2 has no ``@``, affine 3 deprecates ``*``
        r, c = math.floor(inv.e * ys[0] + inv.f), math.floor(inv.a * xs[0] + inv.c)
        if not (0 <= r < self.factor.shape[0] and 0 <= c < self.factor.shape[1]):
            return None
        value = float(self.factor[r, c])
        return None if math.isnan(value) else round(value, 2)

    def like(self, grid: RainGrid) -> np.ndarray:
        """The factor on the pixels of ``grid`` (1.0 where the map has none)."""
        out = np.full(grid.data.shape, np.nan, dtype="float32")
        reproject(
            self.factor.astype("float32"),
            out,
            src_transform=self.transform,
            src_crs=self.crs,
            src_nodata=np.nan,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=Resampling.nearest,
        )
        out[np.isnan(out)] = 1.0
        return out


@cache
def bias_map() -> BiasMap:
    """The map shipped with the package (read once)."""
    with (
        as_file(files("dpc_radar") / "data" / BIAS_FILE) as path,
        rasterio.open(path) as src,
    ):
        raw = src.read(1)
        transform, crs, built_on = src.transform, src.crs, src.tags().get("built_on", "")
    return BiasMap(np.where(raw == _NODATA, np.nan, raw * _STEP), transform, crs, built_on)


def radar_factor(lon: float, lat: float) -> float | None:
    """What to multiply the radar-only rain by at a point to bring it to the «Merging»."""
    return bias_map().at(lon, lat)


def correct(grid: RainGrid) -> RainGrid:
    """A radar-only frame (SRI, SRT1) brought towards the «Merging»: the frame times the factor.

    The same product, time and grid. A «Merging» frame already has the gauges
    in it: correcting it again would be an error, and is refused.
    """
    if grid.product.merging:
        raise DpcRadarError(f"{grid.product.name} is a «Merging» product: nothing to correct")
    return replace(grid, data=grid.data * bias_map().like(grid))
