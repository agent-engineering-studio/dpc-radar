"""The demo of dpc-radar: what the library can tell about the rain over Italy.

    uv run --all-extras --group demo streamlit run demo/app.py

Pick a place. Each tab is one thing the library does, with the lines of Python
that do it and the data they return:

* the rain there now and over the last 24 hours, the official alert, the
  check of the frame;
* the storms raining on Italy right now, and where they are going;
* the rain of the next two hours, as a forecast;
* how rare a given rain is at that place;
* how far the radar can be trusted there;
* the report of a whole day, from the frames kept on disk.

Everything comes from open data of the Dipartimento della Protezione Civile.
It is a demo of the library, not an alert service.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import borders
import httpx
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
from matplotlib.axes import Axes
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.figure import Figure
from rasterio.warp import transform as warp_points

from dpc_radar import (
    SITES,
    Absent,
    DpcRadarError,
    LastProduct,
    NetworkStatus,
    Product,
    RadarClient,
    RainGrid,
    bulletins,
    radar_factor,
    rain_ahead,
    reliability,
    reliability_map,
    return_level,
)
from dpc_radar.archive import MergingArchive
from dpc_radar.facts import HOURS, Facts, rarity_of
from dpc_radar.report import build, bulletins_for

DATA = Path(os.environ.get("DPC_RADAR_DATA", Path(__file__).parent.parent / "data"))
ARCHIVE = DATA / "archive"

PLACES = {
    "Roma": (12.50, 41.90),
    "Milano": (9.19, 45.46),
    "Napoli": (14.27, 40.85),
    "Torino": (7.69, 45.07),
    "Palermo": (13.36, 38.12),
    "Genova": (8.95, 44.41),
    "Bologna": (11.34, 44.49),
    "Firenze": (11.26, 43.77),
    "Venezia": (12.33, 45.44),
    "Bari": (16.87, 41.12),
    "Catania": (15.09, 37.50),
    "Cagliari": (9.12, 39.22),
    "Reggio Calabria": (15.65, 38.11),
    "Another place (type the coordinates)": None,
}
ITALY = (6.0, 35.8, 19.0, 47.6)
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MM = [1.0, 5, 10, 25, 50, 100, 150]  # the steps of a total, in mm
MM_H = [0.5, 2, 5, 10, 25, 50, 100]  # the steps of an hour or of an intensity
ALERT = {"none": "🟢 none", "yellow": "🟡 yellow", "orange": "🟠 orange", "red": "🔴 red"}
VERDICTS = {
    "consistent": "✅ The frame agrees with the one before it.",
    "pixels_masked": "🟡 A few pixels could not be right and were set to «not measured».",
    "previous_wrong": "✅ The frame is fine: it was the one before it that was faulty.",
    "rejected": "⛔ The newest frame is faulty: the answer comes from the one before it.",
    "not_comparable": "⚪ The frame before it has a different grid: not checked.",
    "unchecked": "⚪ There was no earlier frame to check this one against.",
}
RETURN_YEARS = (2, 5, 10, 20, 50)
WINDOWS = (1, 3, 6, 24)

# ---------------------------------------------------------------- the data


class Held:
    """The API, keeping the frames it has read: a new look at the page downloads nothing twice."""

    def __init__(self, radar: RadarClient, frames: OrderedDict[tuple[str, datetime], RainGrid]):
        self._radar, self._frames = radar, frames

    async def last_product(self, name: str | Product) -> LastProduct | None:
        return await self._radar.last_product(name)

    async def radar_status(self) -> NetworkStatus | Absent:
        return await self._radar.radar_status()

    async def at(self, name: str | Product, time: datetime) -> RainGrid | Absent:
        key = (name if isinstance(name, str) else name.name, time)
        if key not in self._frames:
            frame = await self._radar.at(name, time)
            if isinstance(frame, Absent):
                return frame
            self._frames[key] = frame
            while len(self._frames) > 40:
                self._frames.popitem(last=False)
        return self._frames[key]


@st.cache_resource
def held_frames() -> OrderedDict[tuple[str, datetime], RainGrid]:
    return OrderedDict()


@st.cache_resource(ttl=1800, show_spinner="Reading the official bulletin…")
def bulletin() -> bulletins.Bulletin | None:
    """The newest bulletin, or None if GitHub does not answer (60 unsigned calls an hour)."""
    try:
        return asyncio.run(bulletins.latest())
    except (DpcRadarError, httpx.HTTPError):
        return None


def ask[T](job: Callable[[Held, Facts], Awaitable[T]]) -> T:
    """Run ``job`` with the API and with :class:`dpc_radar.facts.Facts` on top of it."""
    found = bulletin()

    async def newest() -> bulletins.Bulletin:
        if found is None:
            raise DpcRadarError("the bulletin is not reachable right now")
        return found

    async def run() -> T:
        async with RadarClient() as radar:
            held = Held(radar, held_frames())
            return await job(held, Facts(held, bulletin=newest))

    return asyncio.run(run())


@st.cache_data(ttl=300, show_spinner="Asking the DPC for the latest frames…")
def rain_here(lon: float, lat: float) -> dict[int, dict[str, Any]]:
    """The rain of the last 1, 3, 6, 12 and 24 hours at a point."""

    async def job(_: Held, facts: Facts) -> dict[int, dict[str, Any]]:
        return {hours: await facts.rain_at(lon, lat, hours) for hours in HOURS}

    return ask(job)


@st.cache_data(ttl=300, show_spinner=False)
def area(box: tuple[float, float, float, float], hours: int) -> dict[str, Any]:
    return ask(lambda _, facts: facts.area_summary(*box, hours))


@st.cache_data(ttl=300, show_spinner=False)
def radars(lon: float | None = None, lat: float | None = None) -> dict[str, Any]:
    """Which radars the DPC reports on and off; not known if the file cannot be read."""
    try:
        return ask(lambda _, facts: facts.radar_status(lon, lat))
    except (DpcRadarError, httpx.HTTPError):
        return {"known": False, "time": None, "radars": [], "off": None}


@st.cache_data(ttl=900, show_spinner=False)
def alert(lon: float, lat: float) -> dict[str, Any] | None:
    try:
        return ask(lambda _, facts: facts.official_alert(lon, lat))
    except DpcRadarError:
        return None


@st.cache_resource(ttl=300, show_spinner="Asking the DPC for the latest frames…")
def newest(product: str) -> RainGrid:
    async def job(held: Held, _: Facts) -> RainGrid:
        last = await held.last_product(product)
        frame = None if last is None else await held.at(product, last.time)
        if frame is None or isinstance(frame, Absent):
            raise DpcRadarError(f"the newest {product} frame is not available")
        return frame

    return ask(job)


@st.cache_data(ttl=300, show_spinner="Looking for storms…")
def storms_now() -> dict[str, Any]:
    return ask(lambda _, facts: facts.storm_now())


@st.cache_data(ttl=300, show_spinner="Moving the rain ahead…")
def ahead(lon: float, lat: float, mm: float) -> list[dict[str, Any]]:
    """The forecast at a point for the next 30, 60, 90 and 120 minutes."""

    async def job(_: Held, facts: Facts) -> list[dict[str, Any]]:
        return [await facts.rain_ahead(lon, lat, minutes, mm) for minutes in (30, 60, 90, 120)]

    return ask(job)


@st.cache_resource(ttl=300, show_spinner=False)
def ahead_field(minutes: int) -> Any:
    """The millimetres of the next ``minutes`` over Italy: a :class:`dpc_radar.Forecast`."""

    async def job(held: Held, _: Facts) -> Any:
        last = await held.last_product("SRI")
        if last is None:
            raise DpcRadarError("the DPC API lists no frame of SRI")
        times = [last.time - last.period * k for k in range(6, -1, -1)]
        frames = [f for f in [await held.at("SRI", t) for t in times] if not isinstance(f, Absent)]
        return rain_ahead(frames, minutes=minutes)

    return ask(job)


def days_kept() -> list[date]:
    """The UTC days the archive on disk holds an hourly frame of."""
    found = set()
    for folder in (ARCHIVE / "SRT1").glob("[0-9]*/[0-9]*/[0-9]*"):
        year, month, day = folder.parts[-3:]
        found.add(date(int(year), int(month), int(day)))
    return sorted(found)


@st.cache_data(show_spinner="Reading the frames of the day…")
def day_report(day: date, box: tuple[float, float, float, float]) -> dict[str, Any]:
    """Build the report of a day over a box, save it, and say where."""
    try:
        official = asyncio.run(bulletins_for(day))
    except (DpcRadarError, httpx.HTTPError):
        official = []
    report = build(MergingArchive(ARCHIVE).read, box, day, bulletins=official)
    folder = DATA / "reports" / f"{day}_{box[0]:.2f}_{box[1]:.2f}_{box[2]:.2f}_{box[3]:.2f}"
    written = report.save(folder)
    return {"facts": report.facts, "text": report.text(), "files": [str(p) for p in written]}


# ---------------------------------------------------------------- the maps


@st.cache_data(show_spinner=False)
def outlines() -> list[np.ndarray]:
    try:
        return borders.rings(DATA / "cache")
    except (httpx.HTTPError, OSError):
        return []  # a map without borders is still a map


def lonlat(transform: Any, crs: Any, shape: tuple[int, int], step: int = 1) -> tuple[Any, Any]:
    """The longitude and latitude of the pixel centres, one pixel every ``step``."""
    rows, cols = np.mgrid[0 : shape[0] : step, 0 : shape[1] : step]
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    if crs.is_geographic:
        return xs, ys
    lons, lats = warp_points(crs, "EPSG:4326", xs.ravel(), ys.ravel())
    return np.reshape(lons, xs.shape), np.reshape(lats, ys.shape)


def canvas(
    box: tuple[float, float, float, float], size: tuple[float, float]
) -> tuple[Figure, Axes]:
    """An empty map of ``box`` (west, south, east, north) with the borders on it."""
    fig, ax = plt.subplots(figsize=size)
    for ring in outlines():
        ax.fill(ring[:, 0], ring[:, 1], color="#ecebe6", zorder=0)
        ax.plot(ring[:, 0], ring[:, 1], color="#6f6e69", linewidth=0.6, zorder=3)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect(1 / np.cos(np.radians((box[1] + box[3]) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    return fig, ax


def paint(ax: Axes, lons: Any, lats: Any, data: Any, levels: list[float], label: str) -> None:
    """Rain on a map: blue by steps, nothing under the first step, grey where not measured."""
    cmap = ListedColormap(BLUE)
    cmap.set_under("none")
    cmap.set_bad("#bdbcb6")
    mesh = ax.pcolormesh(
        lons,
        lats,
        np.ma.masked_invalid(data),
        cmap=cmap,
        norm=BoundaryNorm(levels, cmap.N, extend="max"),
        shading="nearest",
        zorder=2,
    )
    ax.figure.colorbar(mesh, ax=ax, shrink=0.7, pad=0.02, ticks=levels, label=label)


def mark(ax: Axes, lon: float, lat: float) -> None:
    ax.plot(lon, lat, marker="o", ms=10, mfc="none", mec="#0b0b0b", mew=1.6, zorder=6)


def around(lon: float, lat: float, half: float) -> tuple[float, float, float, float]:
    """A box of ``half`` degrees of latitude around a point, as wide on the ground as tall."""
    wide = half / np.cos(np.radians(lat))
    return (round(lon - wide, 3), round(lat - half, 3), round(lon + wide, 3), round(lat + half, 3))


def rain_map(grid: RainGrid, lon: float, lat: float, half: float, levels: list[float]) -> Figure:
    box = around(lon, lat, half)
    part = grid.crop(box)
    fig, ax = canvas(box, (7, 6))
    paint(ax, *lonlat(part.transform, part.crs, part.data.shape), part.data, levels, "mm")
    mark(ax, lon, lat)
    fig.tight_layout()
    return fig


def show(value: float | None, unit: str) -> str:
    return "not measured" if value is None else f"{value:.1f} {unit}"


def behind(code: str, data: Any) -> None:
    """The lines of Python that gave what is on the page, and what they returned."""
    with st.expander("The code, and the data it returns"):
        st.code(code, language="python")
        st.json(data, expanded=False)


# ---------------------------------------------------------------- the page

st.set_page_config(page_title="Rain in Italy · dpc-radar", page_icon="🌧️", layout="wide")
st.title("🌧️ Rain in Italy, from open radar data")
st.caption(
    "A demo of [dpc-radar](https://github.com/agent-engineering-studio/dpc-radar), a Python "
    "library for the open radar data of the Italian Civil Protection. It shows measurements "
    "and estimates, it is **not** an alert service: the alerts that count are issued by the "
    "Civil Protection."
)

with st.sidebar:
    st.header("Where")
    choice = st.selectbox("Place", list(PLACES))
    spot = PLACES[choice]
    if spot is None:
        lon = st.number_input("Longitude (east)", 6.0, 19.0, 15.78, format="%.3f")
        lat = st.number_input("Latitude (north)", 36.0, 47.5, 37.92, format="%.3f")
        name = f"{lon:.3f}° E, {lat:.3f}° N"
    else:
        (lon, lat), name = spot, choice
    half = st.slider("Zoom of the maps: degrees around the place", 0.5, 4.0, 1.5, 0.5)
    st.divider()
    st.caption(
        "Data © Dipartimento della Protezione Civile, CC BY-SA 4.0 · bulletins CC BY 4.0 · "
        "borders: Natural Earth. Frames are asked for again every five minutes."
    )

try:
    rain = rain_here(lon, lat)
    sri = newest("SRI")
except (DpcRadarError, httpx.HTTPError) as exc:
    st.error(f"The DPC radar API did not answer: {exc}. Try again in a minute.")
    st.stop()

here, storm_tab, ahead_tab, rare_tab, trust_tab, day_tab, own_tab = st.tabs(
    [
        "Rain here",
        "Storms now",
        "Next two hours",
        "How rare",
        "Can it be trusted",
        "A day, as a report",
        "Use it yourself",
    ]
)

with here:
    st.subheader(f"{name} · frames to {rain[24]['frame']['time']} UTC")
    columns = st.columns(6)
    [rate] = sri.sample([(lon, lat)])
    columns[0].metric("Now, mm/h", "not measured" if rate is None else f"{rate:.1f}")
    for column, hours in zip(columns[1:], HOURS, strict=True):
        column.metric(
            f"Last {hours} h" if hours > 1 else "Last hour", show(rain[hours]["mm"], "mm")
        )
    if rain[1]["mm"] is None:
        st.info("«Not measured» means the radars do not reach this point. It does not mean dry.")

    hours = st.radio(
        "On the map: the rain of the last",
        list(HOURS),
        index=4,
        horizontal=True,
        format_func=lambda h: "hour (radar alone)" if h == 1 else f"{h} hours",
    )
    left, right = st.columns([3, 2])
    with left:
        st.pyplot(rain_map(newest(HOURS[hours]), lon, lat, half, MM_H if hours == 1 else MM))
        st.caption("Grey: not measured. The circle is the place.")
    with right:
        st.markdown("#### Official alert")
        official = alert(lon, lat)
        if official is None:
            st.write("The bulletin is not reachable right now.")
        elif not official["zones"]:
            st.write("This point is in no alert zone of the bulletin.")
        else:
            for zone in official["zones"]:
                st.write(f"**{zone['day']}** · {ALERT[zone['colour']]} · zone «{zone['zone']}»")
            st.caption(f"Bulletin issued {official['issued']}, DPC.")

        st.markdown("#### Can this frame be trusted?")
        quality = rain[hours]["quality"]
        st.write(VERDICTS.get(quality["verdict"], quality["verdict"]))
        st.caption(
            f"{rain[hours]['frame']['product']} of {rain[hours]['frame']['time']}: "
            f"{quality['masked_pixels']} pixels set to «not measured» by the check."
        )

        st.markdown("#### In the area on the map")
        try:
            summary = area(around(lon, lat, half), hours)
            st.write(
                f"Highest value: **{show(summary['max_mm'], 'mm')}** · "
                f"10 mm or more on **{summary['km2_over']['10']:,.0f} km²**, "
                f"50 mm or more on **{summary['km2_over']['50']:,.0f} km²**"
            )
        except DpcRadarError as exc:
            summary = {"error": str(exc)}
            st.write(f"Not available: {exc}")
    behind(
        f"""from dpc_radar import RadarClient
from dpc_radar.facts import Facts

async with RadarClient() as radar:
    facts = Facts(radar)
    rain = await facts.rain_at({lon}, {lat}, hours={hours})
    alert = await facts.official_alert({lon}, {lat})
    summary = await facts.area_summary(*{around(lon, lat, half)!r}, hours={hours})""",
        {"rain": rain[hours], "alert": official, "summary": summary},
    )

with storm_tab:
    st.subheader("The storms raining on Italy now")
    found: dict[str, Any] | None
    try:
        found = storms_now()
    except (DpcRadarError, httpx.HTTPError) as exc:
        st.error(f"Not available: {exc}")
        found = None
    if found is not None:
        st.write(
            f"**{found['count']}** patches at {found['threshold_mm_h']:.0f} mm/h or more on the "
            f"frame of {found['frame']['time']} UTC. The arrows say where each one went over "
            "the last half hour."
        )
        left, right = st.columns([3, 2])
        with left:
            fig, ax = canvas(ITALY, (7, 7.5))
            lons, lats = lonlat(sri.transform, sri.crs, sri.data.shape, 2)
            paint(ax, lons, lats, sri.data[::2, ::2], MM_H, "mm/h")
            for storm in found["storms"]:
                ax.plot(storm["lon"], storm["lat"], "o", ms=7, mfc="none", mec="#c0392b", zorder=5)
                going = storm["moving"]
                if going is not None and going["kmh"] >= 5:
                    reach = going["kmh"] / 111.0  # an hour of travel, in degrees of latitude
                    angle = np.radians(going["towards_degrees"])
                    ax.annotate(
                        "",
                        (
                            storm["lon"] + reach * np.sin(angle) / np.cos(np.radians(storm["lat"])),
                            storm["lat"] + reach * np.cos(angle),
                        ),
                        (storm["lon"], storm["lat"]),
                        arrowprops={"arrowstyle": "->", "color": "#c0392b", "lw": 1.3},
                        zorder=5,
                    )
            mark(ax, lon, lat)
            fig.tight_layout()
            st.pyplot(fig)
            st.caption("Red circles: storms. An arrow is an hour of travel at the speed measured.")
        with right:
            if found["storms"]:
                st.dataframe(
                    [
                        {
                            "lon": s["lon"],
                            "lat": s["lat"],
                            "km²": s["area_km2"],
                            "peak mm/h": s["peak_mm_h"],
                            "km/h": None if s["moving"] is None else s["moving"]["kmh"],
                            "towards °": None
                            if s["moving"] is None
                            else s["moving"]["towards_degrees"],
                        }
                        for s in found["storms"]
                    ],
                    hide_index=True,
                )
            else:
                st.write("No storm on this frame: a quiet moment.")
            st.caption(
                "An intensity at one instant, not the rain of an hour. The heaviest 20 are "
                "listed. Degrees are from north, clockwise: 90 is towards east."
            )
        behind(
            """from dpc_radar import RadarClient
from dpc_radar.facts import Facts

async with RadarClient() as radar:
    found = await Facts(radar).storm_now()

# or, on frames you already hold:
from dpc_radar import storms, track
patches = storms(frame, threshold=10.0)
tracks = track(frames, threshold=10.0)""",
            found,
        )

with ahead_tab:
    st.subheader(f"{name} · the rain of the next two hours")
    st.warning(
        "This is a **forecast**, not a measurement: the rain seen now, moved along the way it "
        "went over the last half hour. It does not grow or fade, and a storm born later is not "
        "in it. Good for an hour or two; trust the chance more than the millimetres."
    )
    threshold = st.select_slider(
        "Chance of at least", [1.0, 5.0, 10.0, 20.0], 5.0, format_func="{:.0f} mm".format
    )
    try:
        steps = ahead(lon, lat, threshold)
    except (DpcRadarError, httpx.HTTPError) as exc:
        st.error(f"Not available: {exc}")
        steps = []
    if steps:
        for column, step in zip(st.columns(4), steps, strict=True):
            chance = step["chance"]["probability"]
            column.metric(f"Next {step['minutes']} minutes", show(step["forecast_mm"], "mm"))
            if chance is not None:
                column.caption(f"{chance:.0%} chance of {threshold:.0f} mm or more")
        going = steps[0]["moving"]
        if going is not None:
            st.write(
                f"The rain around here moves at **{going['kmh']:.0f} km/h** towards "
                f"**{going['towards_degrees']}°** (from north, clockwise). "
                f"Issued {steps[0]['issued']} UTC."
            )
        minutes = st.radio(
            "On the map: the next",
            [30, 60, 90, 120],
            1,
            horizontal=True,
            format_func="{} minutes".format,
        )
        try:
            field = ahead_field(minutes)
            box = around(lon, lat, half)
            fig, ax = canvas(box, (7, 6))
            lons, lats = lonlat(field.transform, field.crs, field.data.shape, 2)
            paint(ax, lons, lats, field.data[::2, ::2], MM_H, f"mm in the next {minutes} minutes")
            mark(ax, lon, lat)
            fig.tight_layout()
            left, _ = st.columns([3, 2])
            left.pyplot(fig)
        except (DpcRadarError, httpx.HTTPError) as exc:
            st.write(f"The map is not available: {exc}")
    behind(
        f"""from dpc_radar import RadarClient, rain_ahead, chance
from dpc_radar.facts import Facts

async with RadarClient() as radar:
    step = await Facts(radar).rain_ahead({lon}, {lat}, minutes=60, mm={threshold})

# or, on the last frames of SRI (oldest first):
field = rain_ahead(frames, minutes=60)        # mm at every pixel
odds = chance(frames, mm={threshold}, minutes=60)   # 0 to 1 at every pixel""",
        steps,
    )

with rare_tab:
    st.subheader(f"{name} · how rare is a rain, here")
    st.write(
        "The same 50 mm are an ordinary autumn day in one place and a disaster in another. "
        "The library holds eight years of radar (2018–2025) and says, for a place, how often "
        "an amount of rain comes back. Eight years cannot speak of centuries: the answer "
        "stops at 50 years."
    )
    levels = {
        window: {years: return_level(window, lon, lat, years) for years in RETURN_YEARS}
        for window in WINDOWS
    }
    if all(level is None for row in levels.values() for level in row.values()):
        st.info("The radar history has no data at this point.")
    else:
        left, right = st.columns([3, 2])
        with left:
            st.markdown("#### The rain that comes back once in…")
            st.dataframe(
                [
                    {"in": f"{window} h" if window > 1 else "1 hour"}
                    | {
                        f"{years} years": "not known" if level is None else f"{level.mm:.0f} mm"
                        for years, level in row.items()
                    }
                    for window, row in levels.items()
                ],
                hide_index=True,
            )
            fig, ax = plt.subplots(figsize=(7, 3.6))
            for window, row in levels.items():
                known = [(years, level) for years, level in row.items() if level is not None]
                if known:
                    xs = [years for years, _ in known]
                    ax.plot(xs, [level.mm for _, level in known], marker="o", label=f"{window} h")
                    ax.fill_between(
                        xs, [lv.low for _, lv in known], [lv.high for _, lv in known], alpha=0.15
                    )
            ax.set_xscale("log")
            ax.set_xticks(RETURN_YEARS, [str(y) for y in RETURN_YEARS])
            ax.minorticks_off()
            ax.set_xlabel("once in … years")
            ax.set_ylabel("mm (radar)")
            ax.legend(frameon=False, ncols=4)
            fig.tight_layout()
            st.pyplot(fig)
            st.caption("The bands are the 90 % interval of each estimate.")
        with right:
            st.markdown("#### Try an amount")
            window = st.radio("In", WINDOWS, 1, horizontal=True, format_func="{} h".format)
            amount = st.slider("Millimetres", 5.0, 300.0, 60.0, 5.0)
            answer = rarity_of(lon, lat, window, amount)
            years = answer["return_period_years"]
            if years is None:
                st.write("Not known here.")
            elif answer["at_least"]:
                st.metric(f"{amount:.0f} mm in {window} h", "50 years or more")
            else:
                st.metric(f"{amount:.0f} mm in {window} h", f"once in {years:.1f} years")
                st.write(
                    f"Between {answer['low_years']:.1f} and {answer['high_years']:.1f} years, "
                    "nine times out of ten."
                )
            fallen = rain[1]["mm"]
            if fallen:
                last_hour = rarity_of(lon, lat, 1, fallen)
                st.write(
                    f"The last hour brought **{fallen:.1f} mm** here: "
                    + (
                        "an amount that comes every year."
                        if (last_hour["return_period_years"] or 0) <= 1
                        else f"once in **{last_hour['return_period_years']:.1f} years**."
                    )
                )
            st.caption(
                "The amount must be radar rain, the kind the history is made of: a rain gauge "
                "reads more, and would look rarer than it is."
            )
            behind(
                f"""from dpc_radar import rarity, return_level

rarity({window}, {lon}, {lat}, {amount})        # how rare an amount is
return_level({window}, {lon}, {lat}, 20)     # the rain of a 20-year return period""",
                answer,
            )

with trust_tab:
    st.subheader(f"{name} · how far the radar can be trusted here")
    trust = reliability(lon, lat)
    factor = radar_factor(lon, lat)
    if trust.score is None:
        st.info("This point is outside the map of the mosaic.")
    else:
        parts = st.columns(5)
        parts[0].metric("Score", f"{trust.score:.2f}")
        parts[0].caption("✅ reliable" if trust.reliable else "⚠️ not reliable")
        for column, (label, value) in zip(
            parts[1:4],
            [
                ("Visibility", trust.visibility),
                ("Coverage", trust.coverage),
                ("Agreement", trust.agreement),
            ],
            strict=True,
        ):
            column.metric(label, "not known" if value is None else f"{value:.2f}")
        parts[4].metric("Radar × factor", "not known" if factor is None else f"× {factor:.2f}")
        beam = "not known" if trust.beam_height is None else f"{trust.beam_height:,.0f} m"
        st.write(
            f"The nearest radar is **{trust.nearest_site}**, {trust.distance_km:.0f} km away; "
            f"the lowest beam that reaches this point passes **{beam}** above the ground. "
            "The score is the lowest of its three parts: how low the beam passes "
            "(visibility), how often there is data (coverage), and how well the radar alone "
            "agrees with the «Merging» (agreement). The factor is what the radar-only rain "
            "is multiplied by to bring it towards the «Merging»."
        )
    left, _ = st.columns([3, 2])
    with left:
        m = reliability_map()
        fig, ax = canvas(ITALY, (7, 7.5))
        lons, lats = lonlat(m.transform, m.crs, m.score.shape, 3)
        mesh = ax.pcolormesh(
            lons, lats, np.ma.masked_invalid(m.score[::3, ::3]), cmap="RdYlGn", vmin=0, vmax=1,
            shading="nearest", zorder=2,
        )  # fmt: skip
        network = radars()
        off = set(network["off"] or [])
        for site in SITES:
            colour = "white" if site.code in off else "black"
            ax.plot(site.lon, site.lat, "^", ms=6, mfc=colour, mec="black", zorder=5)
        mark(ax, lon, lat)
        fig.colorbar(mesh, ax=ax, shrink=0.7, pad=0.02, label="0 = do not trust · 1 = trust")
        fig.tight_layout()
        st.pyplot(fig)
        st.caption(
            f"Triangles: the {len(SITES)} radars of the mosaic. An estimate made from outside "
            f"(built on {m.built_on}): the DPC publishes no quality map."
        )
        if network["known"]:
            st.caption(
                f"White triangles: reported off by the DPC viewer at {network['time']} "
                f"({', '.join(sorted(off)) or 'none'}). A hint, from a file that is not "
                "documented open data: a radar reported on may still be missing from the mosaic."
            )
    around_here = radars(lon, lat)
    if around_here["radars"]:
        words = {True: "on", False: "**off**", None: "not known"}
        st.write(
            "Radars whose range holds this point, as the DPC viewer reports them now: "
            + "; ".join(
                f"{r['name']} ({r['distance_km']:.0f} km) {words[r['on']]}"
                for r in around_here["radars"]
            )
            + "."
        )
    else:
        st.write("No radar of the mosaic has this point in its range.")
    behind(
        f"""from dpc_radar import reliability, radar_factor, correct

reliability({lon}, {lat})      # score, its parts, beam height, nearest radar
radar_factor({lon}, {lat})     # «Merging» / radar at this place
correct(frame)                  # a radar-only frame brought towards the «Merging»

async with RadarClient() as radar:
    status = await radar.radar_status()   # which radars the DPC reports on and off
    [r.code for r in status.off]""",
        {
            "reliability": rain[1]["reliability"],
            "radar_factor": factor,
            "radar_status": around_here,
        },
    )

with day_tab:
    st.subheader("A whole day, as a report")
    st.write(
        "The DPC deletes each frame after about five months. `dpc-radar archive` keeps them "
        "on disk; `dpc-radar report` reads a day back and writes what happened: the totals of "
        "the radar alone and of the «Merging», the wettest place, how rare it was, the "
        "official alert. Every number of the text is checked against the data before saving."
    )
    kept = days_kept()
    if not kept:
        st.info(
            f"There is no archive in `{ARCHIVE}` yet. Fill it with\n\n"
            f"    dpc-radar archive {ARCHIVE} --days 2\n\n"
            "or run `scripts/init.sh` (with Docker: `docker compose run --rm init`), "
            "then reload the page."
        )
    else:
        whole = [d for d in kept if d + timedelta(days=1) in kept] or kept
        left, right = st.columns(2)
        day = left.selectbox(
            "Day (UTC)", kept, index=kept.index(whole[-1]), format_func="{:%d %B %Y}".format
        )
        size = right.slider("Box: degrees around the place", 0.25, 1.5, 0.5, 0.25)
        made: dict[str, Any] | None
        try:
            made = day_report(day, around(lon, lat, size))
        except DpcRadarError as exc:
            st.error(f"No report for this day: {exc}")
            made = None
        if made is not None:
            frames = made["facts"]["frames"]
            st.caption(
                f"Frames used: {frames['radar']['used']} of {frames['radar']['expected']} hours "
                f"of radar, {frames['merging']['used']} of {frames['merging']['expected']} blocks "
                f"of «Merging». Written to `{Path(made['files'][0]).parent}`."
            )
            left, right = st.columns([3, 2])
            with left:
                for file in made["files"][2:]:
                    st.image(file)
            with right:
                # The report opens with its own title: smaller here, under the title of the page.
                st.markdown(re.sub(r"^#", "###", made["text"], flags=re.MULTILINE))
            st.download_button("facts.json", Path(made["files"][0]).read_bytes(), "facts.json")
            box = around(lon, lat, size)
            behind(
                f"""# from the shell
dpc-radar report --bbox {",".join(str(v) for v in box)} --day {day} \\
    --archive {ARCHIVE} --out report

# from Python
from dpc_radar.archive import MergingArchive
from dpc_radar.report import build

report = build(MergingArchive("{ARCHIVE}").read, {box!r}, date({day.year}, {day.month}, {day.day}))
report.save(Path("report"))""",
                made["facts"],
            )

with own_tab:
    st.subheader("Use it yourself")
    st.markdown(
        """
**In Python**

```bash
pip install "dpc-radar[bulletins,storms,nowcast]"
```

```python
import asyncio
from dpc_radar import RadarClient

async def main():
    async with RadarClient() as radar:
        frame = await radar.latest("CUM24")          # the rain of the last 24 hours
        print(frame.time, frame.sample([(12.50, 41.90)]))   # at Rome, in mm

asyncio.run(main())
```

**From the shell**

```bash
dpc-radar archive data/archive --days 2      # keep the frames before the DPC deletes them
dpc-radar report --bbox 15.5,37.8,16.2,38.3 --day 2026-10-10 --archive data/archive --out melito
dpc-radar check melito/report.md melito/facts.json
```

**From an assistant** (the Model Context Protocol: the same data this page shows, as tools)

```bash
claude mcp add dpc-radar -- uvx --from "dpc-radar[mcp]" dpc-radar mcp
# or, with the image of this repository:
claude mcp add dpc-radar -- docker run -i --rm dpc-radar:local dpc-radar mcp
```

**Read more**: [the documentation](https://agentengineering.it/dpc-radar/) ·
[the notebooks](https://github.com/agent-engineering-studio/dpc-radar/tree/main/notebooks) ·
[`llms.txt`](https://agentengineering.it/dpc-radar/llms.txt) for coding assistants.
"""
    )
    with contextlib.suppress(DpcRadarError, httpx.HTTPError):
        behind(
            "from dpc_radar.facts import Facts\n\nawait Facts(radar).latest()",
            ask(lambda _, facts: facts.latest()),
        )
