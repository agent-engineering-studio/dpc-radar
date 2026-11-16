"""Draw docs/images/reliability.png from the map shipped with the package.

uv run --group examples python docs/make_reliability_image.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from rasterio.warp import transform as warp_points

from dpc_radar import SITES, reliability_map

OUT = Path(__file__).parent / "images" / "reliability.png"
TITLES = {
    "score": "Score: the lowest of the parts",
    "visibility": "Visibility: how low the beam passes",
    "coverage": "Coverage: how often there is data",
    "agreement": "Agreement: radar alone against the «Merging»",
}


def main() -> None:
    m = reliability_map()
    fig, axes = plt.subplots(1, 4, figsize=(17, 5.6), constrained_layout=True)
    for ax, (name, title) in zip(axes, TITLES.items(), strict=True):
        im = ax.imshow(getattr(m, name), vmin=0, vmax=1, cmap="RdYlGn", interpolation="nearest")
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_facecolor("#e9edf2")
        ax.set_xticks([])
        ax.set_yticks([])
    xs, ys = warp_points("EPSG:4326", m.crs, [s.lon for s in SITES], [s.lat for s in SITES])
    cols = [(x + 600000) / 1000 for x in xs]
    rows = [(650000 - y) / 1000 for y in ys]
    axes[1].plot(cols, rows, "k^", ms=4, label="radar")
    axes[1].legend(loc="lower left", fontsize=9, frameon=False)
    lx, ly = warp_points("EPSG:4326", m.crs, [14.95], [38.47])
    c, r = (lx[0] + 600000) / 1000, (650000 - ly[0]) / 1000
    axes[0].plot(c, r, "o", mfc="none", mec="black", ms=9)
    axes[0].annotate("Lipari", (c, r), xytext=(c - 190, r - 60), fontsize=9)
    fig.colorbar(im, ax=axes, shrink=0.75, label="0 = do not trust · 1 = trust", pad=0.01)
    fig.savefig(OUT, dpi=100, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
