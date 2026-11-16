"""A small rain dashboard for any place in Italy, in one file.

    uv run --group examples streamlit run examples/app.py

Pick a town: the page shows how hard it is raining now, how much fell in the
last 3 to 24 hours, the official alert of the zone, a map, and whether the
frame passed the consistency check. Everything comes from open data of the
Dipartimento della Protezione Civile; nothing is stored.

It is a demo of the library, not an alert service.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.figure import Figure
from rasterio.transform import array_bounds

from dpc_radar import ABSENT, DpcRadarError, RadarClient, RainGrid, bulletins

PLACES = {
    "Roma": (12.50, 41.90),
    "Milano": (9.19, 45.46),
    "Napoli": (14.27, 40.85),
    "Torino": (7.69, 45.07),
    "Palermo": (13.36, 38.12),
    "Genova": (8.95, 44.41),
    "Bologna": (11.34, 44.49),
    "Firenze": (11.26, 43.77),
    "Bari": (16.87, 41.12),
    "Cagliari": (9.12, 39.22),
    "Reggio Calabria": (15.65, 38.11),
    "Another place (type the coordinates)": None,
}
TOTALS = ("CUM3", "CUM6", "CUM12", "CUM24")
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
LEVELS = [1, 5, 10, 25, 50, 100, 150]
ALERT = {"none": "🟢 none", "yellow": "🟡 yellow", "orange": "🟠 orange", "red": "🔴 red"}
VERDICTS = {
    "consistent": "✅ The frame agrees with the one before it.",
    "pixels_masked": "🟡 A few pixels jumped and were set to «not measured».",
    "previous_wrong": "✅ The frame is fine: it was the one before it that was faulty.",
    "rejected": "⛔ The frame is faulty (it adds rain that cannot be): do not use it.",
    "not_comparable": "⚪ The frame before it has a different grid: not checked.",
}
MAX_STEP_MM = 150.0  # no real storm adds this much in half an hour
BORDERS_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson"
)
BORDERS_FILE = Path(__file__).parent / ".cache" / "countries.geojson"


@st.cache_resource(ttl=300, show_spinner="Asking the DPC for the latest frames…")
def frames() -> dict[str, RainGrid]:
    """The newest frame of each product, and the CUM24 before it, kept five minutes."""

    async def fetch() -> dict[str, RainGrid]:
        async with RadarClient() as radar:
            found = {name: await radar.latest(name) for name in ("SRI", *TOTALS)}
            day = found["CUM24"]
            before = await radar.at("CUM24", day.time - day.product.period)
            if before is not ABSENT:
                found["before"] = before
        return found

    return asyncio.run(fetch())


@st.cache_resource(ttl=1800, show_spinner="Reading the official bulletin…")
def bulletin() -> bulletins.Bulletin | None:
    """Today's bulletin, or None if GitHub does not answer (60 unsigned calls an hour)."""
    try:
        return asyncio.run(bulletins.latest())
    except (DpcRadarError, httpx.HTTPError):
        return None


@st.cache_data(show_spinner=False)
def borders() -> list[np.ndarray]:
    """Country outlines from Natural Earth (public domain), downloaded once."""
    if not BORDERS_FILE.exists():
        BORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        answer = httpx.get(BORDERS_URL, timeout=120, follow_redirects=True)
        answer.raise_for_status()
        BORDERS_FILE.write_bytes(answer.content)
    rings = []
    for feature in json.loads(BORDERS_FILE.read_text())["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        rings += [np.array(polygon[0]) for polygon in polygons]
    return rings


def rain_map(grid: RainGrid, lon: float, lat: float, half: float) -> Figure:
    """The frame around a point: blue is rain, grey is «not measured»."""
    west, south, east, north = array_bounds(*grid.data.shape, grid.transform)
    cmap = ListedColormap(BLUE)
    cmap.set_under("none")
    cmap.set_bad("#bdbcb6")
    fig, ax = plt.subplots(figsize=(7, 6))
    for ring in borders():
        ax.fill(ring[:, 0], ring[:, 1], color="#ecebe6", zorder=0)
        ax.plot(ring[:, 0], ring[:, 1], color="#6f6e69", linewidth=0.6, zorder=3)
    image = ax.imshow(
        grid.data,
        extent=(west, east, south, north),
        cmap=cmap,
        norm=BoundaryNorm(LEVELS, cmap.N, extend="max"),
        interpolation="nearest",
        zorder=2,
    )
    ax.plot(lon, lat, marker="o", ms=9, mfc="none", mec="#0b0b0b", mew=1.5, zorder=5)
    ax.set_xlim(lon - half * 1.3, lon + half * 1.3)
    ax.set_ylim(lat - half, lat + half)
    ax.set_aspect(1.3)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.colorbar(image, ax=ax, shrink=0.7, pad=0.02, ticks=LEVELS, label="mm")
    fig.tight_layout()
    return fig


def show(value: float | None, unit: str) -> str:
    return "not measured" if value is None else f"{value:.1f} {unit}"


st.set_page_config(page_title="Rain in Italy · dpc-radar", page_icon="🌧️", layout="wide")
st.title("🌧️ Rain in Italy, from open radar data")
st.caption(
    "A demo of [dpc-radar](https://github.com/agent-engineering-studio/dpc-radar). "
    "It shows measurements, it is **not** an alert service: the alerts that count are "
    "issued by the Civil Protection."
)

with st.sidebar:
    st.header("Where")
    choice = st.selectbox("Place", list(PLACES))
    if PLACES[choice] is None:
        lon = st.number_input("Longitude (east)", 6.0, 19.0, 15.78, format="%.3f")
        lat = st.number_input("Latitude (north)", 36.0, 47.5, 37.92, format="%.3f")
        name = f"{lon:.3f}, {lat:.3f}"
    else:
        (lon, lat), name = PLACES[choice], choice
    st.header("Map")
    product = st.radio("Rain of the last", TOTALS, index=3, format_func=lambda p: f"{p[3:]} hours")
    half = st.slider("Zoom: degrees around the place", 0.5, 5.0, 1.5, 0.5)

try:
    latest = frames()
except (DpcRadarError, httpx.HTTPError) as exc:
    st.error(f"The DPC radar API did not answer: {exc}. Try again in a minute.")
    st.stop()

st.subheader(f"{name} · frames to {latest['CUM24'].time:%d %B %Y, %H:%M} UTC")
columns = st.columns(5)
[rate] = latest["SRI"].sample([(lon, lat)])
columns[0].metric("Raining now", show(rate, "mm/h"))
for column, total in zip(columns[1:], TOTALS, strict=True):
    [mm] = latest[total].sample([(lon, lat)])
    column.metric(f"Last {total[3:]} hours", show(mm, "mm"))
if rate is None:
    st.info("«Not measured» means the radars do not reach this point. It does not mean dry.")

left, right = st.columns([3, 2])
with left:
    st.pyplot(rain_map(latest[product], lon, lat, half))
    st.caption(
        "Data © Dipartimento della Protezione Civile, CC BY-SA 4.0 · borders: Natural Earth. "
        "Grey: not measured."
    )
with right:
    st.markdown("#### Official alert")
    official = bulletin()
    if official is None:
        st.write("The bulletin is not reachable right now.")
    else:
        for day in official.days():
            zone = official.zone_at(lon, lat, day)
            if zone is None:
                st.write(f"**{day:%d %B}** · this point is in no alert zone")
            else:
                st.write(f"**{day:%d %B}** · {ALERT[zone.colour]} · zone «{zone.name}»")
        st.caption(f"Bulletin of {official.issued:%d %B %Y, %H:%M}, DPC, CC BY 4.0.")

    st.markdown("#### Can this frame be trusted?")
    if "before" in latest:
        result = latest["CUM24"].check(latest["before"], max_step=MAX_STEP_MM)
        st.write(VERDICTS[result.verdict])
        st.caption(
            f"CUM24 against the frame 30 minutes earlier: {result.jumps} pixels changed by "
            f"more than {MAX_STEP_MM:.0f} mm."
        )
    else:
        st.write("⚪ The frame before this one is not available: not checked.")

    st.markdown("#### In the area on the map")
    box = (lon - half * 1.3, lat - half, lon + half * 1.3, lat + half)
    top, pixels = latest[product].peak(box, threshold=50.0)
    st.write(f"Highest value: **{top:.0f} mm** · 50 mm or more on about **{pixels:,} km²**")
