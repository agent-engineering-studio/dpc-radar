"""Draw docs/images/bias.png from the map shipped with the package.

uv run --group examples python docs/make_bias_image.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm
from rasterio.warp import transform as warp_points

from dpc_radar import bias_map

OUT = Path(__file__).parent / "images" / "bias.png"
PLACES = {
    "Milan": (9.19, 45.46),
    "Genoa": (8.93, 44.41),
    "Rome": (12.5, 41.9),
    "Foggia, a faulty gauge": (15.39, 41.25),
    "Palermo": (13.36, 38.12),
    "Melito": (15.78, 37.92),
}


def main() -> None:
    m = bias_map()
    fig, ax = plt.subplots(figsize=(8.2, 8.6), constrained_layout=True)
    shown = np.where(m.factor == 1.0, np.nan, m.factor)
    ax.imshow(np.isfinite(m.factor), cmap="Greys", vmin=0, vmax=6, interpolation="nearest")
    im = ax.imshow(shown, norm=LogNorm(0.4, 2.5), cmap="RdBu", interpolation="nearest")
    for name, (lon, lat) in PLACES.items():
        xs, ys = warp_points("EPSG:4326", m.crs, [lon], [lat])
        c, r = (xs[0] + 600000) / 1000, (650000 - ys[0]) / 1000
        ax.plot(c, r, "o", mfc="none", mec="black", ms=7)
        ax.annotate(f"{name} {m.at(lon, lat):.2f}", (c, r), xytext=(c + 14, r + 5), fontsize=9)
    ax.set_title(
        "What to multiply the radar alone by to bring it to the «Merging»\n"
        f"red: the radar reads too much · blue: too little · grey: no correction ({m.built_on})",
        fontsize=10,
        loc="left",
    )
    ax.set_xticks([])
    ax.set_yticks([])
    bar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.01, ticks=[0.4, 0.5, 0.67, 1, 1.5, 2, 2.5])
    bar.ax.minorticks_off()
    bar.ax.set_yticklabels(["0.4", "0.5", "0.67", "1", "1.5", "2", "2.5"])
    fig.savefig(OUT, dpi=100, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
