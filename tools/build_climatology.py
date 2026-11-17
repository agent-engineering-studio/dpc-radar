"""Build the rain climatology shipped with the package (``dpc_radar/data/climatology.tif``).

    uv run --extra history --with scipy python -m tools.build_climatology \
        --history <italian-radar-dpc-sri.zarr> --work <folder> [--measure]

It reads the whole IT-DPC-SRI history (:mod:`dpc_radar.history`, 50 GB, one
million frames) once, a year per process, and keeps in ``--work`` one file per
year (about half an hour on eleven cores; a year already there is not read
again). For every year and every window of :data:`dpc_radar.climatology.WINDOWS`:

* the rain of every hour on the 1 km grid, as :meth:`SriHistory.hourly` reads
  it: the frames of the hour weighted by the time they cover, at least half an
  hour of them, without the corrupted frames
  (:func:`dpc_radar.history.suspect`), then the median of the 3x3 pixels;
* the windows that end at every hour: a window counts if at least five sixths
  of its hours were measured, and a missing hour adds nothing; an hour or a
  window over the record of rain (:func:`dpc_radar.quality.record_ceiling`)
  is not measured;
* per pixel, the highest window of the year and how many windows were measured;
* per ``CELL_KM`` cell, how many of the windows of its pixels fell in each
  class of rain (the classes grow by 12 % from 0.1 mm).

Then, per cell (see :mod:`dpc_radar.climatology` for what the result means):

* the percentiles, from the counts of all the years in ``YEARS``;
* a GEV distribution of the highest window of the year, by L-moments: the mean
  is the one of the pixels of the cell, the spread over the mean is the one
  of the ``POOL_KM`` around it, and the skewness is one for the whole map
  (eight values hold neither: on held-out years the levels of a fit cell by
  cell are passed more often, and a skewness by area does no better than one);
* how much the fit moves when the years are drawn again (``BOOTSTRAP`` times,
  the same years everywhere).

``--drift`` reads every year since 2010 and compares the highest window of each
with the fit: it is how the years before 2018 were found to read less.
``--measure`` fits on the years outside each block of held-out years and counts
how often the held-out years go over the levels of the fit. ``--archive
<folder>`` compares the map with the hourly radar frames (``SRT1``) kept by
:mod:`dpc_radar.archive`, which it was not built on.
"""

from __future__ import annotations

import argparse
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from math import gamma as _gamma
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from dpc_radar.climatology import (
    _MM_STEP,
    _NODATA,
    _RATIO_STEP,
    BANDS,
    CLIMATOLOGY_FILE,
    PERCENTILES,
    SPREAD_YEARS,
    WINDOWS,
)
from dpc_radar.history import EPOCH, SriHistory, suspect
from dpc_radar.quality import record_ceiling
from tools.build_reliability import CRS_TM, TRANSFORM

gamma = np.vectorize(_gamma, otypes=[np.float64])
#: The years the climatology is built on: those in which the mosaic reads as it does
#: today. Until 2017 its highest hours were about two thirds of what they are since.
YEARS = (2018, 2025)
#: The first year of the history, for ``--drift``.
FIRST_YEAR = 2010
#: A year counts for a pixel with at least this share of its windows measured.
MIN_YEAR = 0.75
#: A pixel is fitted with at least this many years.
MIN_YEARS = 6
#: The spread over the mean is the one of this many km around the cell.
POOL_KM = 55
SHAPE_RANGE = (-0.1, 0.3)
BOOTSTRAP = 100
HELD_OUT = 2
MEASURE_YEARS = (2, 5, 10, 20)
#: A window counts with at least this many of its hours measured.
MIN_HOURS = {1: 1, 3: 3, 6: 5, 24: 20}
CELL_KM = 5
#: The classes of rain of the counts: ``FIRST_MM * RATIO ** k``.
FIRST_MM = 0.1
RATIO = 1.122
CLASSES = 80
_MAX_STEP_MIN = 15  # as in dpc_radar.history


def hours(history: SriHistory, start: datetime, end: datetime):  # type: ignore[no-untyped-def]
    """Every hour ``(h-1, h]`` in ``(start, end]``: its end and its rain on the grid (NaN: no data).

    ``None`` instead of the grid for an hour with no frames at all.
    """
    from scipy.ndimage import median_filter

    minutes = history._minutes
    a = int((start - EPOCH).total_seconds() // 60)
    b = int((end - EPOCH).total_seconds() // 60)
    lo = int(np.searchsorted(minutes, a, side="left"))
    hi = int(np.searchsorted(minutes, b, side="left"))
    total = np.zeros(history.shape, dtype=np.float32)
    covered = np.zeros(history.shape, dtype=np.float32)
    current = a // 60 + 1

    def close() -> np.ndarray | None:
        if not covered.any():
            return None
        ok = covered >= 30
        rain = np.where(ok, total / np.maximum(covered, 1), 0).astype(np.float32)
        if rain.max() >= FIRST_MM:
            rain = median_filter(rain, size=3, mode="nearest")
        rain[~ok] = np.nan
        return rain

    for i in range(lo, hi):
        m = int(minutes[i])
        hour = m // 60 + 1
        while current < hour:
            yield EPOCH + timedelta(hours=current), close()
            total[:] = 0
            covered[:] = 0
            current += 1
        f = history.frame(i)
        if f is None or suspect(f):
            continue
        after = int(minutes[i + 1]) if i + 1 < len(minutes) else m + 5
        weight = min(after - m, _MAX_STEP_MIN)
        ok = np.isfinite(f)
        total[ok] += f[ok] * weight
        covered[ok] += weight
    last = b // 60
    while current <= last:
        yield EPOCH + timedelta(hours=current), close()
        total[:] = 0
        covered[:] = 0
        current += 1


def one_year(args: tuple[Path, Path, int]) -> int:
    """Read one year of the history and write its file in the work folder."""
    store, work, year = args
    out = work / f"{year}.npz"
    if out.exists():
        return year
    history = SriHistory(store)
    h, w = history.shape
    ch, cw = h // CELL_KM, w // CELL_KM
    rows, cols = np.mgrid[0:h, 0:w]
    cell = ((rows // CELL_KM) * cw + cols // CELL_KM).ravel()
    longest = max(WINDOWS)
    ceilings = {window: record_ceiling(window) or np.inf for window in WINDOWS}
    ring = np.zeros((longest, h, w), dtype=np.float32)
    known = np.zeros((longest, h, w), dtype=bool)
    highest = np.full((len(WINDOWS), h, w), np.nan, dtype=np.float32)
    measured = np.zeros((len(WINDOWS), h, w), dtype=np.uint16)
    counts = np.zeros((len(WINDOWS), ch * cw * CLASSES), dtype=np.uint32)
    rain_total = np.zeros((h, w), dtype=np.float64)
    first = datetime(year, 1, 1, tzinfo=UTC)
    spin_up = first - timedelta(hours=longest - 1)
    for k, (end, rain) in enumerate(hours(history, spin_up, datetime(year + 1, 1, 1, tzinfo=UTC))):
        slot = k % longest
        if rain is None:
            ring[slot] = 0
            known[slot] = False
        else:
            # More than the record of an hour is not rain: not measured.
            known[slot] = rain <= ceilings[1]
            ring[slot] = np.where(known[slot], rain, 0)
        if end <= first:
            continue
        rain_total += ring[slot]
        for n, window in enumerate(WINDOWS):
            slots = [(k - j) % longest for j in range(window)]
            valid = known[slots].sum(axis=0) >= MIN_HOURS[window]
            if not valid.any():
                continue
            mm = ring[slots].sum(axis=0)
            valid &= mm <= ceilings[window]
            measured[n] += valid
            highest[n] = np.where(valid, np.fmax(highest[n], mm), highest[n])
            wet = np.flatnonzero((valid & (mm >= FIRST_MM)).ravel())
            if len(wet):
                klass = np.log(mm.ravel()[wet] / FIRST_MM) / np.log(RATIO)
                klass = np.clip(klass.astype(np.int64), 0, CLASSES - 1)
                np.add.at(counts[n], cell[wet] * CLASSES + klass, 1)
    np.savez_compressed(
        out,
        highest=highest,
        measured=measured,
        counts=counts.reshape(len(WINDOWS), ch, cw, CLASSES),
        total=rain_total.astype(np.float32),
    )
    return year


def read_years(store: Path, work: Path, years: list[int], processes: int) -> None:
    work.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(processes) as pool:
        for year in pool.map(one_year, [(store, work, y) for y in years]):
            print(f"{year} read", flush=True)


def load(work: Path, years: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The files of the years: highest window (NaN for a year measured too little), counts, windows.

    ``highest``: years x windows x grid; ``counts``: windows x cells x classes;
    ``measured``: windows x cells, the windows the counts are a share of.
    """
    highest, counts, measured = [], None, None
    for year in years:
        with np.load(work / f"{year}.npz") as z:
            n = z["measured"]
            in_year = (datetime(year + 1, 1, 1) - datetime(year, 1, 1)).days * 24
            highest.append(np.where(n >= MIN_YEAR * in_year, z["highest"], np.nan))
            c = z["counts"].astype(np.int64)
            m = cells(n.astype(np.float64), np.sum)
            counts = c if counts is None else counts + c
            measured = m if measured is None else measured + m
    assert counts is not None and measured is not None
    return np.stack(highest), counts, measured


def cells(a: np.ndarray, how=np.nanmean) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The pixels of every ``CELL_KM`` cell put together (the last two axes)."""
    *lead, h, w = a.shape
    blocks = a.reshape(*lead, h // CELL_KM, CELL_KM, w // CELL_KM, CELL_KM)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a cell with no pixel
        out: np.ndarray = how(blocks, axis=(-3, -1))
    return out


def around(a: np.ndarray, size: int) -> np.ndarray:
    """The sum of the ``size`` x ``size`` cells around every cell, NaN counted as nothing."""
    pad = size // 2
    a = np.pad(np.nan_to_num(a), pad)
    view = np.lib.stride_tricks.sliding_window_view(a, (size, size))
    out: np.ndarray = view.sum(axis=(-2, -1))
    return out


def l_moments(highest: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The first three L-moments of the years of every pixel, and how many years it has.

    NaN where the pixel has fewer than ``MIN_YEARS`` years.
    """
    x = np.sort(highest, axis=0)  # NaN last
    n = np.isfinite(x).sum(axis=0).astype(np.float32)
    x = np.nan_to_num(x)
    b0 = np.zeros(x.shape[1:], dtype=np.float32)
    b1 = np.zeros_like(b0)
    b2 = np.zeros_like(b0)
    with np.errstate(divide="ignore", invalid="ignore"):
        for j in range(len(x)):
            b0 += x[j]
            b1 += x[j] * (j / (n - 1))
            b2 += x[j] * (j * (j - 1) / ((n - 1) * (n - 2)))
        b0, b1, b2 = b0 / n, b1 / n, b2 / n
    few = n < MIN_YEARS
    l1 = np.where(few, np.nan, b0)
    l2 = np.where(few, np.nan, 2 * b1 - b0)
    l3 = np.where(few, np.nan, 6 * b2 - 6 * b1 + b0)
    return l1, l2, l3, n


def gev(highest: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Location, scale and shape of the GEV of every cell, and its years (Hosking 1985).

    The shape is positive for a heavy tail, and kept within ``SHAPE_RANGE``.
    """
    l1, l2, l3, n = l_moments(highest)
    enough = np.isfinite(cells(l1, np.nanmean)) & (
        cells(np.isfinite(l1).astype(np.float32), np.mean) >= 0.5
    )
    m1 = np.where(enough, cells(l1), np.nan)
    m2 = np.where(enough, cells(l2), np.nan)
    m3 = np.where(enough, cells(l3), np.nan)
    size = POOL_KM // CELL_KM
    with np.errstate(divide="ignore", invalid="ignore"):
        t3 = np.nansum(m3) / np.nansum(m2)  # one skewness for the whole map
        m2 = m1 * around(m2, size) / around(m1, size)
        c = 2 / (3 + t3) - np.log(2) / np.log(3)
        k = float(np.clip(7.8590 * c + 2.9554 * c**2, -SHAPE_RANGE[1], -SHAPE_RANGE[0]))
        k = k if abs(k) >= 0.005 else 0.005  # never a shape of zero in the file
        g = gamma(1 + k)
        scale = m2 * k / ((1 - 2.0**-k) * g)
        loc = m1 - scale * (1 - g) / k
    years = np.where(enough, np.rint(cells(np.where(np.isfinite(l1), n, np.nan))), 0)
    return loc, scale, np.where(enough, np.float32(-k), np.nan), years


def level(loc: np.ndarray, scale: np.ndarray, shape: np.ndarray, years: float) -> np.ndarray:
    """The rain passed once in ``years`` years on average."""
    y = -np.log(1 - 1 / years)
    out: np.ndarray = loc + scale / shape * (y**-shape - 1)
    return out


def percentiles(counts: np.ndarray, measured: np.ndarray) -> np.ndarray:
    """The rain under which ``PERCENTILES`` of the windows of every cell stay (0: under 0.1 mm)."""
    edges = FIRST_MM * RATIO ** np.arange(CLASSES)
    over = np.cumsum(counts[..., ::-1], axis=-1)[..., ::-1]  # windows at or over every edge
    with np.errstate(divide="ignore", invalid="ignore"):
        share = over / measured[..., None]
    out = np.zeros((len(PERCENTILES), *measured.shape), dtype=np.float32)
    for p, percentile in enumerate(PERCENTILES):
        target = 1 - percentile / 100
        k = (share >= target).sum(axis=-1)  # the first edge the share falls under
        inside = (k > 0) & (k < CLASSES)
        hi = np.take_along_axis(share, np.clip(k, 1, CLASSES - 1)[..., None], -1)[..., 0]
        lo = np.take_along_axis(share, np.clip(k - 1, 0, CLASSES - 2)[..., None], -1)[..., 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            part = np.log(lo / target) / np.log(lo / np.maximum(hi, 1e-12))
        mm = edges[np.clip(k - 1, 0, CLASSES - 1)] * RATIO ** np.clip(part, 0, 1)
        out[p] = np.where(inside, mm, np.where(k >= CLASSES, edges[-1], 0))
    out[:, ~(measured > 0)] = np.nan
    return out


def spread(highest: np.ndarray, seed: int = 0) -> np.ndarray:
    """How far the levels of ``SPREAD_YEARS`` move when the years are drawn again.

    Windows x (low, high) x levels x cells, as ratios to the level of the fit.
    The draws of eight years lose the wettest one more often than not, and
    their levels sit under the fit: the interval is the fit over the 95th and
    over the 5th percentile of the draws (the "basic" bootstrap, on ratios),
    which puts the doubt where it is, above. Below, the draws say less the
    further out the level is, which eight years cannot know: the low end of a
    level is never closer to it than the low end of the first of ``SPREAD_YEARS``.
    """
    rng = np.random.default_rng(seed)
    out = []
    for n in range(highest.shape[1]):
        base = np.stack([level(*gev(highest[:, n])[:3], t) for t in SPREAD_YEARS])
        draws = []
        for _ in range(BOOTSTRAP):
            pick = rng.integers(0, len(highest), len(highest))
            fit = gev(highest[pick, n])[:3]
            draws.append(np.stack([level(*fit, t) for t in SPREAD_YEARS]))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            low, high = np.nanpercentile(np.stack(draws), [5, 95], axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ends = np.stack([np.minimum(base / high, 1), np.maximum(base / low, 1)])
        ends[0] = np.fmin(ends[0], ends[0, 0])
        out.append(ends)
        print(f"{WINDOWS[n]} h: spread done", flush=True)
    return np.stack(out)


def held_out(highest: np.ndarray, years: list[int]) -> str:
    """Fit without a block of years, then count how often those years pass the levels."""
    lines = [
        "| Window | Level | Expected | Held-out years | Same years |",
        "|---|---|---|---|---|",
    ]
    blocks = [years[i : i + HELD_OUT] for i in range(0, len(years), HELD_OUT)]
    for n, window in enumerate(WINDOWS):
        passed = {t: [0, 0, 0, 0] for t in MEASURE_YEARS}  # out: over, all; in: over, all
        for block in blocks:
            out = np.array([y in block for y in years])
            fit = gev(highest[~out, n])[:3]
            for t in MEASURE_YEARS:
                lv = np.repeat(np.repeat(level(*fit, t), CELL_KM, 0), CELL_KM, 1)
                for part, sel in ((0, out), (2, ~out)):
                    am = highest[sel, n]
                    ok = np.isfinite(am) & np.isfinite(lv)
                    passed[t][part] += int((ok & (am > lv)).sum())
                    passed[t][part + 1] += int(ok.sum())
        for t, (a, b, c, d) in passed.items():
            lines.append(f"| {window} h | {t} years | {1 / t:.3f} | {a / b:.3f} | {c / d:.3f} |")
    return "\n".join(lines)


def against_archive(folder: Path, start: datetime, end: datetime) -> str:
    """The hourly radar frames (``SRT1``) kept in an archive, against the map just written.

    The frames the package is used on are not those the map was built on: this
    counts how much of the map their highest windows pass, where the radar is
    reliable.
    """
    from dpc_radar import reliability_map
    from dpc_radar.archive import MergingArchive, times
    from dpc_radar.climatology import climatology

    archive = MergingArchive(folder, cache_frames=2)
    longest = max(WINDOWS)
    ring: np.ndarray | None = None
    known: np.ndarray | None = None
    highest: np.ndarray | None = None
    measured: np.ndarray | None = None
    grid = None
    count = 0
    for k, time in enumerate(times("SRT1", start, end)):
        frame = archive.read("SRT1", time)
        if frame is not None and ring is None:
            grid = frame
            ring = np.zeros((longest, *frame.data.shape), dtype=np.float32)
            known = np.zeros(ring.shape, dtype=bool)
            highest = np.full((len(WINDOWS), *frame.data.shape), np.nan, dtype=np.float32)
            measured = np.zeros(highest.shape, dtype=np.uint16)
        if ring is None or known is None or highest is None or measured is None:
            continue
        slot = k % longest
        if frame is None:
            known[slot] = False
            ring[slot] = 0
        else:
            count += 1
            known[slot] = np.isfinite(frame.data)
            ring[slot] = np.where(known[slot], frame.data, 0)
        for n, window in enumerate(WINDOWS):
            slots = [(k - j) % longest for j in range(window)]
            valid = known[slots].sum(axis=0) >= MIN_HOURS[window]
            mm = ring[slots].sum(axis=0)
            measured[n] += valid
            highest[n] = np.where(valid, np.fmax(highest[n], mm), highest[n])
    assert grid is not None and highest is not None and measured is not None
    clim = climatology.__wrapped__()
    hours = (end - start).total_seconds() / 3600
    reliable = reliability_map().like(grid) >= 0.5
    lines = [
        f"{count} hourly frames from {start:%Y-%m-%d} to {end:%Y-%m-%d}",
        "",
        "| Window | Pixels | Highest window over the location of the fit | Over the 2-year level"
        " | 5-year | 10-year | 20-year | 50-year |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for n, window in enumerate(WINDOWS):
        part = clim.parts[window]
        fit = [
            np.repeat(np.repeat(part[k], CELL_KM, 0), CELL_KM, 1) for k in ("loc", "scale", "shape")
        ]
        ok = reliable & np.isfinite(fit[0]) & (measured[n] >= MIN_YEAR * hours)
        ratio = float(np.median(highest[n][ok] / fit[0][ok]))
        over = [float((highest[n][ok] > level(*fit, t)[ok]).mean()) for t in (2, 5, 10, 20, 50)]
        lines.append(
            f"| {window} h | {int(ok.sum())} | {ratio:.2f} | "
            + " | ".join(f"{o:.3f}" for o in over)
            + " |"
        )
    return "\n".join(lines)


def drift(work: Path, highest: np.ndarray) -> str:
    """Every year of the history against the fit: is the mosaic the same through the years?"""
    from dpc_radar import reliability_map

    reliable = reliability_map().score >= 0.5
    lines = [
        "| Year | " + " | ".join(f"{w} h" for w in WINDOWS) + " |",
        "|---|" + "---|" * len(WINDOWS),
    ]
    locs = [
        np.repeat(np.repeat(gev(highest[:, n])[0], CELL_KM, 0), CELL_KM, 1)
        for n in range(len(WINDOWS))
    ]
    for year in range(FIRST_YEAR, YEARS[1] + 1):
        one = load(work, [year])[0][0]
        row = []
        for n in range(len(WINDOWS)):
            ok = reliable & np.isfinite(locs[n]) & np.isfinite(one[n])
            row.append(f"{np.median(one[n][ok] / locs[n][ok]):.2f}")
        lines.append(f"| {year} | " + " | ".join(row) + " |")
    return "\n".join(lines)


def write(
    highest: np.ndarray, counts: np.ndarray, measured: np.ndarray, built_on: str, out: Path
) -> None:
    """The map as a GeoTIFF of ``CELL_KM`` cells: ``len(BANDS)`` bands per window."""
    bands = []
    ranges = spread(highest)
    for n in range(len(WINDOWS)):
        loc, scale, shape, years = gev(highest[:, n])
        pct = percentiles(counts[n], measured[n])
        low, high = ranges[n]
        parts = {
            "loc": loc / _MM_STEP,
            "scale": scale / _MM_STEP,
            "shape": (shape + 1) / _RATIO_STEP,
            "years": years,
            **{f"p{p:g}": pct[i] / _MM_STEP for i, p in enumerate(PERCENTILES)},
            **{f"low{t}": low[i] / _RATIO_STEP for i, t in enumerate(SPREAD_YEARS)},
            **{f"high{t}": high[i] / _RATIO_STEP for i, t in enumerate(SPREAD_YEARS)},
        }
        fitted = np.isfinite(loc)
        for name in BANDS:
            v = parts[name]
            known = np.isfinite(v) & (fitted | name.startswith("p") | (name == "years"))
            bands.append(np.where(known, np.clip(np.rint(v), 0, _NODATA - 1), _NODATA))
    data = np.stack(bands).astype("uint16")
    transform = TRANSFORM * Affine.scale(CELL_KM)
    with rasterio.open(
        out,
        "w",
        driver="GTiff",
        height=data.shape[1],
        width=data.shape[2],
        count=len(data),
        dtype="uint16",
        crs=CRS_TM,
        transform=transform,
        nodata=_NODATA,
        compress="deflate",
        predictor=2,
        zlevel=9,
        tiled=True,
    ) as dst:
        dst.write(data)
        dst.update_tags(
            built_on=built_on, windows=",".join(map(str, WINDOWS)), bands=",".join(BANDS)
        )
    print(f"{out}: {out.stat().st_size / 1e6:.2f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--processes", type=int, default=8)
    parser.add_argument("--measure", action="store_true", help="held-out years test")
    parser.add_argument(
        "--drift", action="store_true", help="every year since 2010 against the fit"
    )
    parser.add_argument("--archive", type=Path, help="a folder of SRT1 frames to compare with")
    parser.add_argument(
        "--start", type=datetime.fromisoformat, default=datetime(2026, 6, 9, tzinfo=UTC)
    )
    parser.add_argument(
        "--end", type=datetime.fromisoformat, default=datetime(2026, 11, 16, tzinfo=UTC)
    )
    parser.add_argument("--out", type=Path, default=Path("src/dpc_radar/data") / CLIMATOLOGY_FILE)
    args = parser.parse_args()
    first = FIRST_YEAR if args.drift else YEARS[0]
    read_years(args.history, args.work, list(range(first, YEARS[1] + 1)), args.processes)
    years = list(range(YEARS[0], YEARS[1] + 1))
    highest, counts, measured = load(args.work, years)
    if args.drift:
        print(drift(args.work, highest))
    if args.measure:
        print(held_out(highest, years))
    write(highest, counts, measured, f"{YEARS[0]}/{YEARS[1]}", args.out)
    if args.archive:
        print(against_archive(args.archive, args.start, args.end))


if __name__ == "__main__":
    main()
