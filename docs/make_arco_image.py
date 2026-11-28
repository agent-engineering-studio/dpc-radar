"""The figure of the ARCO Data Hub: the night of Melito, read four ways.

    set -a; . ./.env; set +a
    uv run --extra arco --group examples python docs/make_arco_image.py

Needs an account of the ARCO Data Hub (ARCODATAHUB_USERNAME,
ARCODATAHUB_ACCESS_KEY) subscribed to SRT1, VMI, ETM and VIL. Writes
docs/images/arco-four-readings.png.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from make_images import BLUE, BORDER, FONT, INK, INK2, LAND, SURFACE, borders
from matplotlib import patheffects
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.ticker import FuncFormatter
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_points

from dpc_radar.arco import ArcoClient

OUT = Path(__file__).parent / "images" / "arco-four-readings.png"
WHEN = datetime(2026, 10, 10, 2, tzinfo=UTC)  # the hour of 63 mm at Melito
MELITO = (15.78, 37.92)
BOX = (15.25, 16.75, 37.65, 38.75)  # west, east, south, north
#: (product, title, unit, the lower edge of each blue step, a scale on the values)
PANELS = [
    ("SRT1", "Rain of the hour to 02:00", "mm", [1, 2, 5, 10, 20, 40, 60], 1.0),
    ("VMI", "Strongest echo in the column", "dBZ", [15, 20, 30, 40, 45, 50, 55], 1.0),
    ("ETM", "Top of the echo", "km", [2, 4, 6, 8, 10, 12, 14], 0.001),
    ("VIL", "Liquid water in the column", "mm", [0.5, 1, 2, 5, 10, 20, 40], 1.0),
]
CREDIT = (
    "Data: ARCO Data Hub, FBK in cooperation with Dipartimento Protezione Civile, "
    "CC BY-SA 4.0 · borders: Natural Earth"
)


async def frames() -> list[tuple[str, np.ndarray, object, object]]:
    async with ArcoClient() as arco:
        out = []
        for name, *_ in PANELS:
            grid = await arco.frame(name, WHEN)
            assert grid is not None, f"{name} has no frame at {WHEN}"
            out.append((name, grid.data, grid.transform, grid.crs))
        return out


def main() -> None:
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": FONT.split(", ")})
    got = asyncio.run(frames())
    _, _, transform, crs = got[0]
    xs, ys = warp_points("EPSG:4326", crs, [BOX[0], BOX[1]], [BOX[2], BOX[3]])
    (bottom, top), (left, right) = rowcol(transform, xs, ys)
    part = (slice(top, bottom), slice(left, right))
    coast = []
    for ring in borders()[0]:
        near = (ring[:, 0] > BOX[0] - 1) & (ring[:, 0] < BOX[1] + 1)
        near &= (ring[:, 1] > BOX[2] - 1) & (ring[:, 1] < BOX[3] + 1)
        if near.any():
            rx, ry = warp_points("EPSG:4326", crs, list(ring[:, 0]), list(ring[:, 1]))
            rr, cc = rowcol(transform, rx, ry)
            coast.append((np.array(cc) - left, np.array(rr) - top))
    mx, my = warp_points("EPSG:4326", crs, [MELITO[0]], [MELITO[1]])
    row, col = rowcol(transform, mx[0], my[0])
    at = (col - left, row - top)

    fig, axes = plt.subplots(2, 2, figsize=(11, 10.4), facecolor=SURFACE)
    for ax, (name, title, unit, levels, scale), (_, data, _, _) in zip(
        axes.flat, PANELS, got, strict=True
    ):
        values = data[part].astype(float) * scale
        nothing = np.isneginf(values) | (np.isfinite(values) & (values < levels[0]))
        shown = np.where(np.isfinite(values) & ~nothing, values, np.nan)
        ax.set_facecolor(SURFACE)
        ax.imshow(np.where(nothing, 1.0, np.nan), cmap=ListedColormap([LAND]), vmin=0, vmax=1)
        cmap = ListedColormap(BLUE)
        ceiling = levels[-1] + (levels[-1] - levels[-2])  # the last step is open: "and above"
        norm = BoundaryNorm([*levels, ceiling], len(BLUE))
        image = ax.imshow(
            np.minimum(shown, ceiling * 0.999), cmap=cmap, norm=norm, interpolation="nearest"
        )
        for cc, rr in coast:
            ax.plot(cc, rr, color=BORDER, lw=0.8)
        ax.plot(*at, marker="o", ms=8, mfc="none", mec=INK, mew=1.4)
        label = ax.annotate(
            "Melito di Porto Salvo", at, xytext=(at[0] + 5, at[1] + 12), fontsize=9, color=INK
        )
        label.set_path_effects([patheffects.withStroke(linewidth=3, foreground=SURFACE)])
        ax.set_xlim(0, right - left)
        ax.set_ylim(bottom - top, 0)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(BORDER)
            spine.set_linewidth(0.6)
        ax.set_title(f"{name} · {title}", fontsize=11, loc="left", color=INK)
        bar = fig.colorbar(image, ax=ax, shrink=0.78, pad=0.02, ticks=levels, extend="max")
        bar.set_label(unit, color=INK2, fontsize=9)
        bar.ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        bar.ax.tick_params(labelsize=8, colors=INK2)
        bar.outline.set_visible(False)
        at_point = data[row, col] * scale
        print(f"{name}: at Melito {at_point:.2f} {unit}")
    fig.suptitle(
        "10 October 2026, 02:00 UTC: the same storm, four readings of the radar",
        x=0.02,
        ha="left",
        fontsize=13,
        color=INK,
    )
    fig.text(
        0.02,
        0.012,
        f"Grey: measured, and nothing above the first step. {CREDIT}",
        fontsize=7.5,
        color=INK2,
    )
    fig.tight_layout(rect=(0, 0.025, 1, 0.97))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150, facecolor=SURFACE)
    print(OUT)


if __name__ == "__main__":
    main()
