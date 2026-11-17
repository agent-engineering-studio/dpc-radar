"""Draw docs/images/catalogue.png: the rare rain of a season, on the map and month by month.

uv run --group examples python docs/make_catalogue_image.py docs/data/rare-rain-2026.csv

The file is the one written by ``tools/build_catalogue.py``.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar import reliability_map

OUT = Path(__file__).parent / "images" / "catalogue.png"
CEILING = 50.0
WINDOWS = {"1": "#f4a73f", "3": "#e2572b", "6": "#b12a5b", "24": "#4b2a7b"}
MONTHS = ["Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
KNOWN = ("Melito di Porto Salvo, 10 October", 15.78, 37.92)


def main() -> None:
    with Path(sys.argv[1]).open(newline="") as source:
        rows = [r for r in csv.DictReader(source) if float(r["return_period_y"]) >= CEILING]
    m = reliability_map()
    inv = ~m.transform
    xs, ys = warp_points(
        "EPSG:4326", m.crs, [float(r["lon"]) for r in rows], [float(r["lat"]) for r in rows]
    )
    cols = inv.a * np.array(xs) + inv.c
    lines = inv.e * np.array(ys) + inv.f
    size = np.array([12 * float(r["times_level"]) ** 2 for r in rows])
    seen = np.array([r["confirmed"] == "true" for r in rows])

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(14, 7.4), constrained_layout=True, width_ratios=[1.25, 1]
    )
    # Where the radar can be compared with the gauges: the land, more or less.
    left.imshow(np.isfinite(m.agreement), cmap="Greys", vmin=0, vmax=5, interpolation="nearest")
    left.scatter(cols[~seen], lines[~seen], s=size[~seen], fc="none", ec="#6b7785", lw=0.8)
    left.scatter(cols[seen], lines[seen], s=size[seen], fc="#e2572b", ec="white", lw=0.4, alpha=0.8)
    kx, ky = warp_points("EPSG:4326", m.crs, [KNOWN[1]], [KNOWN[2]])
    at = (inv.a * kx[0] + inv.c, inv.e * ky[0] + inv.f)
    left.annotate(
        KNOWN[0],
        at,
        xytext=(at[0] - 330, at[1] + 70),
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": "black", "lw": 0.8},
    )
    left.set_title(
        f"Rain over the 50-year level of its place, June-November 2026: {len(rows)} events\n"
        f"full: the «Merging» saw it too ({int(seen.sum())}); empty: only the radar. "
        "The larger, the further over the level",
        fontsize=10,
        loc="left",
    )
    left.set_xticks([])
    left.set_yticks([])

    by = Counter((int(r["start"][5:7]), r["window_h"]) for r in rows)
    bottom = np.zeros(len(MONTHS))
    for window, colour in WINDOWS.items():
        count = np.array([by[(month, window)] for month in range(6, 12)])
        right.bar(MONTHS, count, bottom=bottom, color=colour, label=f"{window} h")
        bottom += count
    right.set_title("The same events by month and by the window they were rarest in", fontsize=10)
    right.set_ylabel("events")
    right.legend(frameon=False)
    right.spines[["top", "right"]].set_visible(False)
    fig.savefig(OUT, dpi=100, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
