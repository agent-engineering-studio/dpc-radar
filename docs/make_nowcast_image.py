"""Draw docs/images/nowcast.png: a nowcast of the night of Melito di Porto Salvo.

uv run --group examples --extra nowcast python docs/make_nowcast_image.py <folder>

The folder holds the SRI frames of 10 October 2026 from 00:00 to 02:30 UTC,
as ``SRI_YYYYMMDD_HHMM.tif`` (``RadarClient.download("SRI", time)``).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from make_images import BORDER, CREDIT, borders
from matplotlib.colors import BoundaryNorm, ListedColormap
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_points

from dpc_radar import RainGrid
from dpc_radar.nowcast import chance, motion, rain_ahead

OUT = Path(__file__).parent / "images" / "nowcast.png"
NOW = datetime(2026, 10, 10, 0, 30, tzinfo=UTC)
MINUTES = 120
BOX = (15.0, 37.45, 16.9, 38.75)  # the Strait of Messina
MELITO = (15.78, 37.92)
LEVELS = [1, 5, 10, 20, 30, 50, 75, 100]
COLOURS = ["#cfe8f7", "#8cc6ec", "#4a9bd9", "#f4d03f", "#f39c12", "#e2572b", "#8e2a5b"]


def main() -> None:
    folder = Path(sys.argv[1])

    def frame(time: datetime) -> RainGrid:
        raw = (folder / f"SRI_{time:%Y%m%d_%H%M}.tif").read_bytes()
        return RainGrid.from_geotiff(raw, "SRI", time)

    five = timedelta(minutes=5)
    grids = [frame(NOW - five * k) for k in range(6, -1, -1)]
    now = grids[-1]
    ahead = rain_ahead(grids, minutes=MINUTES)
    fell = np.sum([frame(NOW + five * k).data for k in range(1, MINUTES // 5 + 1)], axis=0) / 12
    likely = chance(grids, mm=20, minutes=MINUTES)
    wind = motion(grids).at(*MELITO)
    assert wind is not None

    xs, ys = warp_points("EPSG:4326", now.crs, [BOX[0], BOX[2]], [BOX[1], BOX[3]])
    (bottom, top), (left, right) = rowcol(now.transform, xs, ys)
    part = (slice(top, bottom), slice(left, right))
    coast = []
    for ring in borders()[0]:
        near = (np.abs(ring[:, 0] - 16.0) < 2.5) & (np.abs(ring[:, 1] - 38.1) < 2.0)
        if near.any():
            rx, ry = warp_points("EPSG:4326", now.crs, list(ring[:, 0]), list(ring[:, 1]))
            rr, cc = rowcol(now.transform, rx, ry)
            coast.append((np.array(cc) - left, np.array(rr) - top))
    mx, my = warp_points("EPSG:4326", now.crs, [MELITO[0]], [MELITO[1]])
    row, col = rowcol(now.transform, mx[0], my[0])
    at = (col - left, row - top)

    cmap, norm = ListedColormap(COLOURS), BoundaryNorm(LEVELS, len(COLOURS))
    fig, axes = plt.subplots(1, 4, figsize=(17, 4.6), constrained_layout=True)
    panels = [
        (now.data, f"The radar at {NOW:%H:%M} UTC (mm/h)", cmap, norm),
        (ahead.data, f"Forecast at {NOW:%H:%M}: rain to {ahead.time:%H:%M} (mm)", cmap, norm),
        (fell, f"What the radar saw to {ahead.time:%H:%M} (mm)", cmap, norm),
        (likely.data, "Forecast: chance of 20 mm or more", "Purples", None),
    ]
    for ax, (data, title, colours, scale) in zip(axes, panels, strict=True):
        shown = np.where(data[part] >= (0.05 if scale is None else LEVELS[0]), data[part], np.nan)
        image = ax.imshow(
            shown,
            cmap=colours,
            norm=scale,
            interpolation="nearest",
            **({} if scale else {"vmin": 0, "vmax": 1}),
        )
        for cc, rr in coast:
            ax.plot(cc, rr, color=BORDER, lw=0.8)
        ax.set_xlim(0, right - left)
        ax.set_ylim(bottom - top, 0)
        ax.plot(*at, marker="o", ms=7, mfc="none", mec="black", mew=1.2)
        ax.annotate("Melito", at, xytext=(at[0] + 6, at[1] + 16), fontsize=9)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(image, ax=ax, shrink=0.75, pad=0.02)
    seen = RainGrid(now.product, NOW, fell.astype(np.float32), now.transform, now.crs)
    print(
        f"motion at Melito {wind[0]:.0f} km/h to {wind[1]:.0f}°; forecast "
        f"{ahead.sample([MELITO])[0]:.1f} mm, seen {seen.sample([MELITO])[0]:.1f} mm, "
        f"chance of 20 mm {likely.sample([MELITO])[0]:.2f}"
    )
    fig.text(0.005, 0.01, CREDIT, fontsize=7, color="#6b7785")
    fig.savefig(OUT, dpi=100, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
