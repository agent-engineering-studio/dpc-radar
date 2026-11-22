"""Build the radar bias map shipped with the package (``dpc_radar/data/bias.tif``).

    uv run python -m tools.build_bias --archive <folder of archive()> --dem <folder> [--measure]

It needs a local archive of SRT1 and CUM3 frames (:func:`dpc_radar.archive.archive`)
and the terrain tiles of ``tools/build_reliability.py`` (``--dem``), to tell land from sea.
For every CUM3 frame it sums the three SRT1 hours of the same window on the
1 km grid and compares the two (see :mod:`dpc_radar.bias`):

* a window in which the «Merging» has 30 mm where the radar saw nothing, at a
  place the radar sees well, is a rain gauge gone wrong (the ``gauge_only`` rule
  of :mod:`dpc_radar.quality`): everything within 15 km of it is left out;
* a place where the «Merging» is wet under a dry radar in more than
  ``CHRONIC_SHARE`` of its wet windows has a gauge that drips all the time: it
  is left out of the sums, and its factor is 1;
* the factor is the sum of the «Merging» over the sum of the radar, both over
  ``SMOOTH`` km, on land (at least ``MIN_LAND`` of the ``SMOOTH`` km around the
  pixel) where the «Merging» measured at least ``MIN_RAIN_MM``. Elsewhere it is 1:
  over the sea there are no gauges, and the «Merging» is the radar.

``--measure`` tests the map the way it will be used: for every month, the factor
built on the other months is applied to that month, and the result is compared
with the «Merging». ``--cache`` keeps the sums of the first pass in a file.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar.archive import MergingArchive
from dpc_radar.bias import _NODATA, _STEP, BIAS_FILE, FACTOR_MAX, FACTOR_MIN
from dpc_radar.reliability import reliability_map
from tools.build_reliability import CRS_TM, SHAPE, TRANSFORM, box, dem, frame, stored

SMOOTH = 9
MIN_RAIN_MM = 20.0
MIN_LAND = 0.25
FAULT_MIN_MM = 30.0  # as GAUGE_MIN_MM in dpc_radar.quality
DRY_SHARE = 0.05  # the radar saw less than this share of the «Merging»: it saw nothing
FAULT_AROUND_KM = 31  # a fault spreads below 30 mm too
WET_MM = 1.0
CHRONIC_SHARE = 0.3
CHRONIC_MIN_WINDOWS = 20
RECORD_MM = 472.0  # above the record of 3 hours with its margin: not rain
THRESHOLDS = (10, 30, 50, 100)
Sums = dict[str, dict[str, np.ndarray]]


def highest(a: np.ndarray, size: int) -> np.ndarray:
    """The highest value in the ``size`` x ``size`` window around every pixel."""
    pad = size // 2
    for axis in (0, 1):
        width = [(pad, pad) if i == axis else (0, 0) for i in (0, 1)]
        a = np.lib.stride_tricks.sliding_window_view(np.pad(a, width), size, axis=axis).max(-1)
    return a


def sees_well() -> np.ndarray:
    rmap = reliability_map()
    seen: np.ndarray = (rmap.visibility >= 0.5) & (rmap.coverage >= 0.5)
    return seen


def windows(
    folder: Path, skip: tuple[date, date] | None = None
) -> Iterator[tuple[datetime, np.ndarray, np.ndarray, np.ndarray]]:
    """Every CUM3 frame with its three SRT1 hours: time, radar, «Merging», where to compare.

    Both on the 1 km grid, in mm over the same three hours. The last array is
    False where either has no data and around a rain gauge gone wrong.
    ``skip``: the days (first and last) whose windows are left out.
    """
    arc = MergingArchive(folder, cache_frames=8)
    have = set(stored(folder, "SRT1"))
    sees = sees_well()
    for t in stored(folder, "CUM3"):
        if skip and skip[0] <= t.date() <= skip[1]:
            continue
        three = [t - timedelta(hours=h) for h in range(3)]
        if not all(h in have for h in three):
            continue
        cum = arc.read("CUM3", t)
        if cum is None:
            continue
        merged = np.full(SHAPE, np.nan, dtype="float32")
        reproject(
            cum.data.astype("float32"),
            merged,
            src_transform=cum.transform,
            src_crs=cum.crs,
            src_nodata=np.nan,
            dst_transform=TRANSFORM,
            dst_crs=CRS_TM,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        radar = np.stack([frame(arc, "SRT1", h) for h in three]).sum(0)
        both = np.isfinite(merged) & np.isfinite(radar)
        fault = (
            sees
            & (merged >= FAULT_MIN_MM)
            & (highest(np.nan_to_num(radar), 5) <= DRY_SHARE * merged)
        )
        if fault.any():
            fault = box(fault.astype(float), FAULT_AROUND_KM) > 1e-9
        yield t, radar, merged, both & ~fault


def sums(folder: Path, skip: tuple[date, date] | None = None) -> Sums:
    """Per month: the rain of the radar and of the «Merging», and the wet and dry windows."""
    out: Sums = {}
    for t, radar, merged, ok in windows(folder, skip):
        month = out.setdefault(
            f"{t:%Y-%m}",
            {
                "radar": np.zeros(SHAPE, dtype="float32"),
                "merged": np.zeros(SHAPE, dtype="float32"),
                "wet": np.zeros(SHAPE, dtype="int16"),
                "dry": np.zeros(SHAPE, dtype="int16"),
            },
        )
        month["radar"] += np.where(ok, radar, 0)
        month["merged"] += np.where(ok, merged, 0)
        wet = np.isfinite(radar) & (merged >= WET_MM)
        month["wet"] += wet
        month["dry"] += wet & (highest(np.nan_to_num(radar), 11) <= DRY_SHARE * merged)
    return out


def total(s: Sums, part: str, months: list[str]) -> np.ndarray:
    return np.sum([s[m][part] for m in months], axis=0, dtype="float64")


def chronic(s: Sums, months: list[str]) -> np.ndarray:
    """Where a gauge reports rain under a dry radar most of the time."""
    wet, dry = total(s, "wet", months), total(s, "dry", months)
    bad: np.ndarray = sees_well() & (wet >= CHRONIC_MIN_WINDOWS) & (dry > CHRONIC_SHARE * wet)
    return bad


def land(dem_folder: Path) -> np.ndarray:
    """Where there may be rain gauges: land, and the sea just off it."""
    near: np.ndarray = box((dem(dem_folder) > 0).astype(float), SMOOTH) >= MIN_LAND
    return near


def factor(s: Sums, months: list[str], on_land: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The factor map built on ``months``, and the places left out as chronic faults."""
    bad = chronic(s, months)
    radar = box(np.where(bad, 0, total(s, "radar", months)), SMOOTH)
    merged = box(np.where(bad, 0, total(s, "merged", months)), SMOOTH)
    known = on_land & ~bad
    known &= (merged >= MIN_RAIN_MM) & (radar > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(known, np.clip(merged / radar, FACTOR_MIN, FACTOR_MAX), 1.0)
    return out, bad


class Score:
    """Running sums of one estimate against the «Merging»."""

    def __init__(self) -> None:
        self.n = self.err = self.est = self.obs = self.heavy_est = self.heavy_obs = 0.0
        self.ee = self.oo = self.eo = 0.0
        self.passes = dict.fromkeys(THRESHOLDS, 0)
        self.real = dict.fromkeys(THRESHOLDS, 0)
        self.hits = dict.fromkeys(THRESHOLDS, 0)

    def add(self, est: np.ndarray, obs: np.ndarray) -> None:
        est, obs = est.astype("float64"), obs.astype("float64")
        self.n += est.size
        self.err += np.abs(est - obs).sum()
        self.est += est.sum()
        self.obs += obs.sum()
        self.ee += (est * est).sum()
        self.oo += (obs * obs).sum()
        self.eo += (est * obs).sum()
        heavy = obs >= 30
        self.heavy_est += est[heavy].sum()
        self.heavy_obs += obs[heavy].sum()
        for mm in THRESHOLDS:
            self.passes[mm] += int((est >= mm).sum())
            self.real[mm] += int((obs >= mm).sum())
            self.hits[mm] += int(((est >= mm) & (obs >= mm)).sum())

    def row(self) -> str:
        if not self.n:
            return "| - |"
        cov = self.eo / self.n - self.est * self.obs / self.n**2
        var = (self.ee / self.n - (self.est / self.n) ** 2) * (
            self.oo / self.n - (self.obs / self.n) ** 2
        )
        cells = [f"{self.err / self.n:.2f}", f"{self.est / self.obs:.2f}", f"{cov / var**0.5:.2f}"]
        for mm in THRESHOLDS:
            union = self.passes[mm] + self.real[mm] - self.hits[mm]
            if self.real[mm] and union:
                cells.append(f"{self.passes[mm] / self.real[mm]:.2f} ({self.hits[mm] / union:.2f})")
            else:
                cells.append("-")
        cells.append(f"{self.heavy_est / self.heavy_obs:.2f}" if self.heavy_obs else "-")
        return "| " + " | ".join(cells) + " |"


def areas(land: np.ndarray) -> dict[str, np.ndarray]:
    """The parts of the map the test reports on."""
    rmap = reliability_map()
    rows, cols = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    xs, ys = TRANSFORM.c + (cols + 0.5) * 1000.0, TRANSFORM.f - (rows + 0.5) * 1000.0
    lons, lats = warp_points(CRS_TM, "EPSG:4326", xs.ravel(), ys.ravel())
    lon, lat = np.reshape(lons, SHAPE), np.reshape(lats, SHAPE)
    sardinia = land & (lon < 10.0) & (lat < 41.4)
    sicily = land & (lat < 38.35) & (lon > 12.0) & ~((lon > 15.62) & (lat > 37.9))
    rest = land & ~sardinia & ~sicily
    score = np.nan_to_num(rmap.score, nan=0.0)
    return {
        "everywhere": np.ones(SHAPE, dtype=bool),
        "land": land,
        "sea": ~land,
        "north (from 44°N)": rest & (lat >= 44.0),
        "centre (41.5-44°N)": rest & (lat >= 41.5) & (lat < 44.0),
        "south (below 41.5°N)": rest & (lat < 41.5),
        "Sicily": sicily,
        "Sardinia": sardinia,
        "land, radar reliable (score from 0.5)": land & (score >= 0.5),
        "land, radar not reliable": land & (score < 0.5),
    }


def measure(folder: Path, s: Sums, on_land: np.ndarray) -> str:
    """Leave one month out: the table of the radar alone and of the radar times the factor."""
    held_out = {m: factor(s, [k for k in s if k != m], on_land)[0] for m in s}
    national = {
        m: sum(float(s[k]["merged"].sum()) for k in s if k != m)
        / sum(float(s[k]["radar"].sum()) for k in s if k != m)
        for m in s
    }
    parts = areas(on_land)
    methods = ("radar alone", "one factor for Italy", "factor map")
    classes = {
        "«Merging» under 10 mm": (0, 10),
        "«Merging» 10-30 mm": (10, 30),
        "«Merging» 30-50 mm": (30, 50),
        "«Merging» from 50 mm": (50, 1e9),
    }
    by_area = {(a, m): Score() for a in parts for m in methods}
    by_class = {(c, m): Score() for c in classes for m in methods}
    by_month = {(k, m): Score() for k in s for m in methods}
    for t, radar, merged, ok in windows(folder):
        month = f"{t:%Y-%m}"
        ok &= (np.maximum(radar, merged) >= WET_MM) & (merged < RECORD_MM)
        estimates = {
            "radar alone": radar,
            "one factor for Italy": radar * national[month],
            "factor map": radar * held_out[month],
        }
        for method, est in estimates.items():
            for area, where in parts.items():
                sel = ok & where
                by_area[area, method].add(est[sel], merged[sel])
            by_month[month, method].add(est[ok], merged[ok])
            for name, (low, high) in classes.items():
                sel = ok & (merged >= low) & (merged < high)
                by_class[name, method].add(est[sel], merged[sel])
    head = (
        "| | | MAE (mm) | total | correlation | "
        + " | ".join(f"≥ {mm} mm" for mm in THRESHOLDS)
        + " | rain where the «Merging» has ≥ 30 mm |\n|"
        + "---|" * (6 + len(THRESHOLDS))
    )
    lines = [head]
    for table in (by_area, by_class, by_month):
        for (group, method), score in table.items():
            lines.append(f"| {group} | {method} {score.row()}")
    return "\n".join(lines)


def write(values: np.ndarray, built_on: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    in_mosaic = reliability_map().coverage > 0
    with rasterio.open(
        out,
        "w",
        driver="GTiff",
        height=SHAPE[0],
        width=SHAPE[1],
        count=1,
        dtype="uint8",
        crs=CRS_TM,
        transform=TRANSFORM,
        nodata=_NODATA,
        compress="deflate",
        predictor=2,
        zlevel=9,
        tiled=True,
    ) as dst:
        dst.write(np.where(in_mosaic, np.rint(values / _STEP), _NODATA).astype("uint8"), 1)
        dst.set_band_description(1, "factor")
        dst.update_tags(
            built_on=built_on,
            built_at=f"{datetime.now(UTC):%Y-%m-%d}",
            source="DPC radar mosaic and «Merging», CC BY-SA 4.0",
        )


def load(folder: Path, cache: Path | None, skip: tuple[date, date] | None = None) -> Sums:
    """The sums of :func:`sums`, from ``cache`` if it is there (made with any ``skip``)."""
    if cache and cache.exists():
        out: Sums = {}
        with np.load(cache) as z:
            for key in z.files:
                month, part = key.split("/")
                out.setdefault(month, {})[part] = z[key]
        return out
    s = sums(folder, skip)
    if cache:
        flat = {f"{m}/{p}": a for m, parts in s.items() for p, a in parts.items()}
        np.savez_compressed(cache, **flat)
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--cache", type=Path, help="keep the sums of the first pass here")
    parser.add_argument("--measure", action="store_true", help="leave-one-month-out test")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parents[1] / "src/dpc_radar/data" / BIAS_FILE
    )
    args = parser.parse_args()
    s = load(args.archive, args.cache)
    on_land = land(args.dem)
    if args.measure:
        print(measure(args.archive, s, on_land))
        return
    values, bad = factor(s, sorted(s), on_land)
    hours = stored(args.archive, "SRT1")
    write(values, f"{hours[0]:%Y-%m-%d}/{hours[-1]:%Y-%m-%d}", args.out)
    known = values != 1.0
    print(
        f"{args.out}: {args.out.stat().st_size / 1024:.0f} kB, {int(known.sum())} km² with a "
        f"factor (median {np.median(values[known]):.2f}), {int(bad.sum())} km² left out as "
        f"chronic gauge faults"
    )


if __name__ == "__main__":
    main()
