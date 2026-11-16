"""Draw the explanatory diagram of the README: why a radar can miss rain.

    uv run --group examples python docs/make_diagrams.py

No data is downloaded. The beam is drawn to scale with the usual "4/3 Earth"
rule: a beam sent at an angle ``a`` is, at a distance ``r``, at the height
``r·sin(a) + r² / (2 · 4/3 · R)`` above the radar, with ``R`` the Earth's
radius. With the lowest beam at 0.5° that is about 1.5 km at 100 km and 4 km
at 200 km. The mountain, the cloud and the melting layer are an example.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Ellipse

OUT = Path(__file__).parent / "images"
EARTH_KM = 6371.0
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, LAND, INK, MUTED = "#fcfcfb", "#d9d6cc", "#0b0b0b", "#7a7974"
plt.rcParams.update({"font.family": "sans-serif", "font.size": 11, "text.color": INK})


def beam_km(distance: np.ndarray, angle_deg: float) -> np.ndarray:
    """Height of a radar beam above the radar, in km (4/3 Earth)."""
    return distance * np.sin(np.radians(angle_deg)) + distance**2 / (2 * 4 / 3 * EARTH_KM)


def note(ax: plt.Axes, n: int, x: float, y: float, text: str) -> None:
    ax.text(x, y, str(n), ha="center", va="center", fontsize=10, color="white", weight="bold",
            bbox={"boxstyle": "circle,pad=0.25", "fc": INK, "ec": "none"}, zorder=9)  # fmt: skip
    ax.text(x + 4, y, text, ha="left", va="center", fontsize=9.5, zorder=9)


def main() -> None:
    r = np.linspace(0, 220, 400)
    fig, ax = plt.subplots(figsize=(12, 5.2), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    # The melting layer: snow that melts looks like very heavy rain to a radar.
    ax.axhspan(2.9, 3.4, color="#f3d9a4", alpha=0.7, zorder=1)

    # The beam: one degree wide, lowest edge at 0°, centre at 0.5°.
    low, mid, high = beam_km(r, 0.0), beam_km(r, 0.5), beam_km(r, 1.0)
    ax.fill_between(r, low, high, color=BLUE[1], alpha=0.55, zorder=2, linewidth=0)
    ax.plot(r, mid, color=BLUE[4], linewidth=1.2, zorder=3)

    # A mountain close to the radar hides the part of the beam that leaves under its top.
    peak, top = 60.0, 0.8
    blocked = np.degrees(np.arcsin((top - beam_km(np.array(peak), 0.0)) / peak))
    behind = r > peak
    ax.fill_between(r[behind], low[behind], beam_km(r[behind], float(blocked)), color=SURFACE,
                    alpha=0.85, zorder=3, linewidth=0)  # fmt: skip
    ax.fill([peak - 14, peak, peak + 14], [0, top, 0], color=LAND, zorder=4)

    # A low rain cloud far away: the rain falls under the beam.
    for cx, cy in ((146, 1.45), (158, 1.6), (170, 1.45)):
        ax.add_patch(Ellipse((cx, cy), 22, 0.75, color="#b9b8b2", zorder=5))
    for x in np.arange(141, 177, 3.5):
        ax.plot([x, x - 1.5], [1.1, 0.1], color=BLUE[4], linewidth=1.3, zorder=5)
    ax.plot([158, 158], [0, 0.4], color=INK, linewidth=2, zorder=6)
    ax.plot([155.5, 160.5], [0.4, 0.4], color=INK, linewidth=2, zorder=6)

    # The radar.
    ax.plot([0, 0], [0, 0.35], color=INK, linewidth=3, zorder=6)
    ax.add_patch(Ellipse((0, 0.45), 6, 0.36, color=INK, zorder=6))

    ax.axhline(0, color=INK, linewidth=1.5, zorder=6)
    ax.set_xlim(-6, 222)
    ax.set_ylim(0, 6.6)
    ax.set_xlabel("distance from the radar (km)")
    ax.set_ylabel("height above the radar (km)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.text(-3, 0.8, "radar", fontsize=9.5)
    ax.text(179, 0.25, "rain gauge", fontsize=9, ha="left")

    note(
        ax,
        1,
        100,
        float(beam_km(np.array(100.0), 0.5)) + 0.75,
        "the beam climbs: 1.5 km up at 100 km",
    )
    note(ax, 2, 160, 5.0, "and 4 km up at 200 km")
    note(ax, 3, 78, 0.3, "behind a mountain:\nhalf the beam is gone")
    note(ax, 4, 20, 3.15, "melting snow shines like a downpour")
    note(ax, 5, 183, 1.3, "rain that forms low\nfalls under the beam")
    ax.set_title("Why a radar can miss rain, or see too much", loc="left", fontsize=13, pad=12)
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "how-radar-sees.png", dpi=160, facecolor=SURFACE)
    print("written", OUT / "how-radar-sees.png")


if __name__ == "__main__":
    main()
