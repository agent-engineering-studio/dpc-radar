"""What to trust in a frame, pixel by pixel, and why.

:meth:`dpc_radar.RainGrid.quality` looks at a frame from four sides. Each one
is a rule a person can check, with a threshold measured on the frames kept from
June to November 2026, and each names the pixels it does not trust:

* ``jump``: the frame against the one before it (:meth:`RainGrid.check`). The
  only side that can reject the whole frame; it needs ``previous``;
* ``beyond_record``: more rain than was ever measured in Italy over the same
  window (:data:`RECORDS`, with a margin). It needs nothing, so it also works
  on the first frame;
* ``outlier``: a pixel far above the 5x5 pixels around it, measured with their
  median and their spread (MAD): a hot pixel, not a storm;
* ``gauge_only``: in a «Merging» frame, rain that the radar alone did not see
  at all, at a place where the radar sees well. It needs ``radar``, the
  radar-only rain over the same window. That is a rain gauge reporting rain
  under a dry sky: on 6-8 October 2026 one of them put 126.6 mm in every
  three-hour frame for 33 hours on the Lazio-Abruzzo border.

There is no trained model here. Fourteen labelled cases are too few to train
one, and a rule that can be read is worth more than a model that cannot be
checked: the rules above are the features a model would use, when the labels
exist. Two features of the first plan are missing because we have no confirmed
case to measure them on: radial symmetry around a radar and rain over the sea
against the nearby land. The one case we had, the "disks" west of Sardinia of
20 October 2026, turned out to be rain.

What it cannot see: a wrong value that is plausible, moves like rain and
agrees with the radar; a gauge that fails where the radar is blind (there the
gauge is all we have, and it is kept); a gauge that reads too little.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from rasterio.warp import Resampling, reproject

from dpc_radar.errors import DpcRadarError

if TYPE_CHECKING:
    from dpc_radar.grid import RainGrid, Verdict

#: The most rain ever measured in Italy over a window, hours → mm: 181.0 in
#: 1 h (Vicomorasso, 4 November 2011); 377.8 in 3 h, 496.0 in 6 h and 740.6 in
#: 12 h (Urbe, Montenotte Inferiore and Rossiglione, 4 October 2021); 948.4 in
#: 24 h (Genova Bolzaneto, 7-8 October 1970). All in Liguria.
RECORDS: Final = {1: 181.0, 3: 377.8, 6: 496.0, 12: 740.6, 24: 948.4}
#: A value is "beyond the record" above the record times this: room for a new
#: record, and for the radar, which over hail reads more than the rain.
RECORD_MARGIN: Final = 1.25
#: ``outlier``: the pixel must be at least this far (mm) above the median of
#: its 5x5 neighbours, and at least this many times their spread.
OUTLIER_MIN_MM: Final = 50.0
OUTLIER_SPREADS: Final = 10.0
#: ``gauge_only``: the «Merging» has at least this much (mm), the radar alone
#: less than this share of it anywhere within 2 km, and the radar sees the
#: place well (``visibility`` and ``coverage`` of the reliability map).
GAUGE_MIN_MM: Final = 30.0
GAUGE_RADAR_SHARE: Final = 0.05
GAUGE_RADAR_SEES: Final = 0.5
#: A pixel counts as "wet" in the score from this value up (mm).
_WET_MM = 1.0


@dataclass(frozen=True, slots=True)
class Quality:
    """The answer of :meth:`RainGrid.quality`.

    ``grid`` is the frame to use, with the pixels of ``mask`` set to ``NaN``;
    ``None`` when the whole frame is rejected. ``mask`` is ``True`` where a
    pixel that had a value is not trusted. ``reasons`` counts those pixels by
    the rule that named them (a pixel can be under more than one). ``score``
    is the share of the wet pixels that are trusted: 1 for a clean frame, 0
    for a rejected one. ``verdict`` is the answer of the comparison with the
    frame before, ``None`` without one.
    """

    grid: RainGrid | None
    mask: np.ndarray
    reasons: dict[str, int]
    score: float
    verdict: Verdict | None

    @property
    def usable(self) -> bool:
        return self.grid is not None


def record_ceiling(hours: int | None) -> float | None:
    """The value no frame of this window should pass (mm); ``None`` for an intensity."""
    if hours is None:
        return None
    longer = [h for h in RECORDS if h >= hours]
    return RECORDS[min(longer) if longer else max(RECORDS)] * RECORD_MARGIN


def radar_total(frames: list[RainGrid]) -> RainGrid:
    """The radar-only rain over a window: the sum of its hourly ``SRT1`` frames.

    For a CUM3 issued at ``t``, the frames of ``t``, ``t - 1 h`` and ``t - 2 h``.
    Where one hour has no data, the sum has none (``NaN``).
    Pass every hour of the window: with one missing the sum is short, and rain
    the radar did see would look like rain it did not.
    """
    if not frames:
        raise DpcRadarError("no frames to sum")
    first = frames[0]
    if any(f.data.shape != first.data.shape or f.transform != first.transform for f in frames):
        raise DpcRadarError("frames on different grids cannot be summed")
    total = np.sum([f.data for f in frames], axis=0, dtype="float32")
    return replace(max(frames, key=lambda f: f.time), data=total)


def quality(
    grid: RainGrid,
    previous: RainGrid | None,
    radar: RainGrid | None,
    *,
    max_step: float,
    min_pixels: int,
) -> Quality:
    """See :meth:`dpc_radar.RainGrid.quality`."""
    valid = np.isfinite(grid.data)
    reasons = {"jump": 0, "beyond_record": 0, "outlier": 0, "gauge_only": 0}
    mask = np.zeros(grid.data.shape, dtype=bool)
    verdict: Verdict | None = None

    if previous is not None:
        check = grid.check(previous, max_step=max_step, min_pixels=min_pixels)
        verdict = check.verdict
        if check.grid is None:
            reasons["jump"] = check.jumps
            return Quality(None, valid, reasons, 0.0, verdict)
        if verdict == "pixels_masked":
            jumped = valid & ~np.isfinite(check.grid.data)
            reasons["jump"] = int(jumped.sum())
            mask |= jumped

    ceiling = record_ceiling(grid.product.hours)
    parts = {
        "beyond_record": valid & (grid.data > ceiling) if ceiling is not None else None,
        "outlier": _outliers(grid.data),
        "gauge_only": _gauge_only(grid, radar) if radar is not None else None,
    }
    for name, part in parts.items():
        if part is not None:
            reasons[name] = int(part.sum())
            mask |= part

    wet = int((valid & (grid.data >= _WET_MM)).sum())
    lost = int((mask & (grid.data >= _WET_MM)).sum())
    score = 1.0 if wet == 0 else 1.0 - lost / wet
    if not mask.any():
        return Quality(grid, mask, reasons, score, verdict)
    data = grid.data.copy()
    data[mask] = np.nan
    return Quality(replace(grid, data=data), mask, reasons, score, verdict)


def _outliers(data: np.ndarray) -> np.ndarray:
    """Pixels far above the 5x5 around them. Only the high ones are looked at."""
    out = np.zeros(data.shape, dtype=bool)
    rows, cols = np.nonzero(np.nan_to_num(data) >= OUTLIER_MIN_MM)
    if rows.size == 0:
        return out
    padded = np.pad(data, 2, constant_values=np.nan)
    tiles = sliding_window_view(padded, (5, 5))[rows, cols].reshape(rows.size, 25)
    median = np.nanmedian(tiles, axis=1)
    spread = 1.4826 * np.nanmedian(np.abs(tiles - median[:, None]), axis=1)
    above = data[rows, cols] - median
    out[rows, cols] = above > np.maximum(OUTLIER_MIN_MM, OUTLIER_SPREADS * spread)
    return out


def _gauge_only(grid: RainGrid, radar: RainGrid) -> np.ndarray:
    """«Merging» rain the radar did not see, where the radar sees well."""
    from dpc_radar.reliability import reliability_map  # it imports the grid module

    if not grid.product.merging:
        return np.zeros(grid.data.shape, dtype=bool)
    # the most the radar saw within 2 km: a storm is never placed to the pixel
    padded = np.pad(np.nan_to_num(radar.data), 2)
    near = sliding_window_view(padded, (5, 5)).max(axis=(2, 3))
    near = np.where(np.isfinite(radar.data), near, np.nan).astype("float32")
    seen = np.full(grid.data.shape, np.nan, dtype="float32")
    reproject(
        near,
        seen,
        src_transform=radar.transform,
        src_crs=radar.crs,
        src_nodata=np.nan,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    rmap = reliability_map()
    sees_well = (rmap.like(grid, "visibility") >= GAUGE_RADAR_SEES) & (
        rmap.like(grid, "coverage") >= GAUGE_RADAR_SEES
    )
    with np.errstate(invalid="ignore"):
        return (
            sees_well
            & (grid.data >= GAUGE_MIN_MM)
            & np.isfinite(seen)
            & (seen <= GAUGE_RADAR_SHARE * grid.data)
        )


__all__ = [
    "GAUGE_MIN_MM",
    "GAUGE_RADAR_SEES",
    "GAUGE_RADAR_SHARE",
    "OUTLIER_MIN_MM",
    "OUTLIER_SPREADS",
    "RECORDS",
    "RECORD_MARGIN",
    "Quality",
    "quality",
    "radar_total",
    "record_ceiling",
]
