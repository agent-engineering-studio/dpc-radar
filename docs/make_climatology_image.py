"""Draw docs/images/climatology.png: the map shipped with the package, and one day read with it.

uv run --group examples python docs/make_climatology_image.py <archive folder>

The archive is the one kept by :mod:`dpc_radar.archive`, with the frames of
1 and 2 November 2026.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from rasterio.warp import Resampling, reproject

from dpc_radar import RainGrid, climatology, radar_total, return_level, return_period
from dpc_radar.archive import MergingArchive

OUT = Path(__file__).parent / "images" / "climatology.png"
END = datetime(2026, 11, 2, 9, tzinfo=UTC)
PLACES = {"Genoa": (8.93, 44.41), "Rome": (12.5, 41.9), "Bari": (16.87, 41.12)}
STEPS = [1, 2, 5, 10, 20, 50]
COLOURS = ["#dfe7ee", "#f7e08a", "#f4a73f", "#e2572b", "#8e1b4f"]


def day(archive: MergingArchive) -> tuple[RainGrid, RainGrid]:
    """The 24 hours to ``END``: the radar alone, and the «Merging» on the same pixels."""
    hours = [archive.read("SRT1", END - timedelta(hours=h)) for h in range(24)]
    blocks = [archive.read("CUM3", END - timedelta(hours=3 * h)) for h in range(8)]
    radar = radar_total([f for f in hours if f is not None])
    kept = [f for f in blocks if f is not None]
    if len(kept) < 8 or any(f is None for f in hours):
        raise SystemExit("the archive lacks frames of the 24 hours")
    merging = np.full(radar.data.shape, np.nan, dtype="float32")
    reproject(
        np.sum([f.data for f in kept], axis=0, dtype="float32"),
        merging,
        src_transform=kept[0].transform,
        src_crs=kept[0].crs,
        src_nodata=np.nan,
        dst_transform=radar.transform,
        dst_crs=radar.crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return radar, replace(radar, data=merging)


def main() -> None:
    c = climatology()
    radar, merging = day(MergingArchive(Path(sys.argv[1])))
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.6), constrained_layout=True)

    p = c.parts[24]
    y = -np.log(1 - 1 / 10)
    level = p["loc"] + p["scale"] / p["shape"] * (y ** -p["shape"] - 1)
    im = axes[0].imshow(level, vmin=0, vmax=250, cmap="YlGnBu", interpolation="nearest")
    axes[0].set_title(
        f"The rain of 24 hours passed once in 10 years (radar mm, {c.built_on})",
        fontsize=10,
        loc="left",
    )
    for name, (lon, lat) in PLACES.items():
        at = return_level(24, lon, lat, 10)
        r, k = c._cell(lon, lat) or (0, 0)
        assert at is not None
        axes[0].plot(k, r, "o", mfc="none", mec="black", ms=7)
        axes[0].annotate(f"{name} {at.mm:.0f} mm", (k, r), xytext=(k + 4, r + 2), fontsize=9)
    fig.colorbar(im, ax=axes[0], shrink=0.7, pad=0.01, label="mm")

    norm = BoundaryNorm(STEPS, len(COLOURS))
    for ax, frame, name in ((axes[1], radar, "radar alone"), (axes[2], merging, "«Merging»")):
        years = return_period(frame, 24)
        rare = int((years >= 10).sum())
        ax.set_facecolor("#ffffff")
        im = ax.imshow(years, norm=norm, cmap=ListedColormap(COLOURS), interpolation="nearest")
        ax.set_title(
            f"24 hours to {END:%-d %B %Y %H:%M} UTC, {name}:\n"
            f"return period of the rain; 10 years or more on {rare:,} km²",
            fontsize=10,
            loc="left",
        )
    bar = fig.colorbar(im, ax=axes[1:], shrink=0.7, pad=0.01, ticks=STEPS, label="years")
    bar.ax.set_yticklabels(["every year", "2", "5", "10", "20", "50 or more"])
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    fig.savefig(OUT, dpi=100, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
