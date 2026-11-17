"""Measure the nowcast against what the radar saw next, on storms of the IT-DPC-SRI history.

    uv run --extra history python -m tools.measure_nowcast --history <italian-radar-dpc-sri.zarr> \
        [--cases 120] [--seed 2026] [--since 2021]

It draws moments at random from the years with a frame every five minutes
(2021 on), one per day at most, and keeps those with heavy rain somewhere
(300 km² at 10 mm/h or more), the half hour before and the two hours after
complete and no corrupted frame. On each it runs :func:`dpc_radar.nowcast.nowcast`
on the frames of the last half hour and compares it, pixel by pixel, with the
frames that came after; "persistence" is the last frame left where it is, the
forecast that knows nothing of motion.

* CSI: of the pixels where rain over a threshold was forecast or seen, the share
  where it was both (1 is perfect). Bias: pixels forecast over pixels seen.
* The rain of the next hour, forecast and seen, the same way.
* :func:`dpc_radar.nowcast.chance`: the Brier skill against saying everywhere
  the share of pixels that got the rain (0 is no better than that, 1 is
  perfect), and how often it rained where a chance was given.
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.crs import CRS

from dpc_radar import SRI, RainGrid
from dpc_radar.history import EPOCH, SriHistory, suspect

# The module, not the function of the same name that the package exports.
_nowcast = importlib.import_module("dpc_radar.nowcast")

BACK = 6  # frames of five minutes: the half hour the motion is measured on
AHEAD = 24  # two hours
HEAVY, MIN_HEAVY = 10.0, 300
LEADS = (30, 60, 120)
RATES = (1.0, 5.0, 10.0)  # mm/h
SUMS = (5.0, 10.0, 20.0)  # mm
BINS = (0.0, 0.05, 0.2, 0.4, 0.6, 0.8, 1.0001)


class Score:
    """Hits, false alarms and misses, summed over the cases."""

    def __init__(self) -> None:
        self.hits = self.false = self.missed = 0

    def add(self, forecast: np.ndarray, seen: np.ndarray, threshold: float) -> None:
        both = np.isfinite(forecast) & np.isfinite(seen)
        f, o = (forecast >= threshold) & both, (seen >= threshold) & both
        self.hits += int((f & o).sum())
        self.false += int((f & ~o).sum())
        self.missed += int((~f & o).sum())

    def __str__(self) -> str:
        csi = self.hits / max(self.hits + self.false + self.missed, 1)
        bias = (self.hits + self.false) / max(self.hits + self.missed, 1)
        return f"{csi:.2f} ({bias:.2f})"


def cases(history: SriHistory, count: int, seed: int, since: int) -> list[int]:
    """The index of the frame of "now" of each case."""
    minutes = history._minutes
    start = int(np.searchsorted(minutes, (datetime(since, 1, 1, tzinfo=UTC) - EPOCH).days * 1440))
    draw = random.Random(seed)
    days: set[int] = set()
    found: list[int] = []
    while len(found) < count:
        i = draw.randrange(start + BACK, len(minutes) - AHEAD)
        day = int(minutes[i]) // 1440
        if day in days or np.any(np.diff(minutes[i - BACK : i + AHEAD + 1]) != 5):
            continue
        now = history.frame(i)
        if now is None or (np.nan_to_num(now) >= HEAVY).sum() < MIN_HEAVY:
            continue
        frames = [history.frame(k) for k in range(i - BACK, i + AHEAD + 1)]
        if any(f is None or suspect(f) for f in frames):
            continue
        days.add(day)
        found.append(i)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=120)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--since", type=int, default=2021)
    args = parser.parse_args()

    history = SriHistory(args.history)
    transform = Affine(history._dx, 0, history._x0, 0, history._dy, history._y0)
    crs = CRS.from_wkt(history._crs_wkt)
    km = abs(history._dx) / 1000.0

    def frame(i: int) -> np.ndarray:
        data = history.frame(i)
        assert data is not None
        return data.astype(np.float32)

    rate: dict[tuple[float, int, str], Score] = defaultdict(Score)
    total: dict[tuple[float, str], Score] = defaultdict(Score)
    brier: dict[tuple[float, int, str], float] = defaultdict(float)
    pixels: dict[tuple[float, int], int] = defaultdict(int)
    rained: dict[tuple[float, int], int] = defaultdict(int)
    given: dict[tuple[float, int], list[int]] = defaultdict(lambda: [0, 0])
    speeds: list[float] = []
    still = 0
    chosen = cases(history, args.cases, args.seed, args.since)
    for n, i in enumerate(chosen, start=1):
        grids = [
            RainGrid(SRI, history.time(k), frame(k), transform, crs) for k in range(i - BACK, i + 1)
        ]
        now = grids[-1].data
        wind = _nowcast.motion(grids)
        still += not wind.found
        wet = np.nan_to_num(now) >= 1.0
        speeds.append(float(np.median(np.hypot(wind.east, wind.north)[wet])))
        forecasts = [f.data for f in _nowcast.nowcast(grids, minutes=5 * AHEAD)]
        seen = [frame(k) for k in range(i + 1, i + AHEAD + 1)]
        for lead in LEADS:
            for threshold in RATES:
                rate[threshold, lead, "nowcast"].add(
                    forecasts[lead // 5 - 1], seen[lead // 5 - 1], threshold
                )
                rate[threshold, lead, "persistence"].add(now, seen[lead // 5 - 1], threshold)
        for lead in LEADS:
            steps = lead // 5
            ahead = np.sum(forecasts[:steps], axis=0) / 12.0
            fell = np.sum(seen[:steps], axis=0) / 12.0
            if lead == 60:
                for mm in SUMS:
                    total[mm, "nowcast"].add(ahead, fell, mm)
                    total[mm, "persistence"].add(now * (lead / 60.0), fell, mm)
            both = np.isfinite(ahead) & np.isfinite(fell)
            reach = round(_nowcast.SPREAD_KM_PER_MINUTE * lead / km)
            for mm in SUMS:
                chance = _nowcast._share(ahead, mm, reach)[both]
                yes = fell[both] >= mm
                flat = ahead[both] >= mm
                brier[mm, lead, "chance"] += float(((chance - yes) ** 2).sum())
                brier[mm, lead, "yes or no"] += float((flat != yes).sum())
                pixels[mm, lead] += int(both.sum())
                rained[mm, lead] += int(yes.sum())
                if lead == 60:
                    for b, (low, high) in enumerate(itertools.pairwise(BINS)):
                        inside = (chance >= low) & (chance < high)
                        given[mm, b][0] += int(inside.sum())
                        given[mm, b][1] += int(yes[inside].sum())
        print(f"{n}/{len(chosen)} {history.time(i):%Y-%m-%d %H:%M}", flush=True)

    first, last = history.time(min(chosen)), history.time(max(chosen))
    print(f"\n{len(chosen)} cases from {first:%Y-%m-%d} to {last:%Y-%m-%d}, seed {args.seed}")
    print(
        f"speed of the rain, median over the wet pixels: median {np.median(speeds):.0f} km/h, "
        f"from {np.min(speeds):.0f} to {np.max(speeds):.0f}; no motion found in {still} cases"
    )
    print("\nIntensity: CSI (bias)\n| mm/h | Minutes | Nowcast | Persistence |\n|---|---|---|---|")
    for threshold in RATES:
        for lead in LEADS:
            print(
                f"| {threshold:g} | {lead} | {rate[threshold, lead, 'nowcast']} "
                f"| {rate[threshold, lead, 'persistence']} |"
            )
    print("\nRain of the next hour: CSI (bias)\n| mm | Nowcast | Persistence |\n|---|---|---|")
    for mm in SUMS:
        print(f"| {mm:g} | {total[mm, 'nowcast']} | {total[mm, 'persistence']} |")
    print(
        "\nChance: Brier skill\n| mm | Minutes | Pixels that got it | chance() | Yes or no |"
        "\n|---|---|---|---|---|"
    )
    for mm in SUMS:
        for lead in LEADS:
            base = rained[mm, lead] / pixels[mm, lead]
            reference = base * (1 - base) * pixels[mm, lead]
            print(
                f"| {mm:g} | {lead} | {100 * base:.2f} % "
                f"| {1 - brier[mm, lead, 'chance'] / reference:+.2f} "
                f"| {1 - brier[mm, lead, 'yes or no'] / reference:+.2f} |"
            )
    print(
        "\nChance of the next hour: how often it rained\n| Chance given | "
        + " | ".join(f"{mm:g} mm" for mm in SUMS)
        + " |\n|---|"
        + "---|" * len(SUMS)
    )
    for b, (low, high) in enumerate(itertools.pairwise(BINS)):
        cells = []
        for mm in SUMS:
            count, yes = given[mm, b]
            cells.append(f"{100 * yes / count:.2f} % of {count}" if count else "-")
        print(f"| {100 * low:.0f}-{min(100 * high, 100):.0f} % | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
