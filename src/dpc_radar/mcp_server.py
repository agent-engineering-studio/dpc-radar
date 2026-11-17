"""The MCP server: the rain over Italy as tools an assistant can call.

``dpc-radar mcp`` speaks the Model Context Protocol on standard input and
output. It needs the ``mcp`` extra (``pip install "dpc-radar[mcp]"``) and no
key: the data are open. To add it to Claude Code::

    claude mcp add dpc-radar -- uvx --from "dpc-radar[mcp]" dpc-radar mcp

The tools are the methods of :class:`dpc_radar.facts.Facts`:

* ``latest``: the newest frame of every product.
* ``rain_at``: the rain of the last 1, 3, 6, 12 or 24 hours at a point.
* ``area_summary``: the same over a box: maximum, mean, km² over thresholds.
* ``storm_now``: the storms on the newest intensity frame, and where they go.
* ``rain_ahead``: the nowcast at a point, labelled as a forecast.
* ``official_alert``: the level of the DPC criticality bulletin.
* ``rarity``: how rare an amount of rain is at a place.

Every answer names the product and the time of the frame it was read from: the
model words the answer, it does not compute it. What the library refuses
(a box outside Italy, a window the DPC does not publish) reaches the model as
an error it can read and act on.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from dpc_radar.bulletins import Bulletin
from dpc_radar.client import RadarClient
from dpc_radar.errors import DpcRadarError
from dpc_radar.facts import Facts, Source

INSTRUCTIONS = """\
Rain over Italy from the open radar data of the Italian Civil Protection (DPC).

Rules for whoever answers with these tools:

- Every number you give must come from a tool result. Do not estimate, round
  further, add up or extrapolate numbers yourself.
- Say which product and which frame time (UTC) a number comes from: each
  result has them under "frame".
- null means not measured there (no data, or masked by the quality check). It
  is not zero: never say it did not rain because a value is null.
- rain_at reads the median of the 3x3 pixels around the point, about 3 km: it
  is the rain around a place, not at a house number. A maximum in
  area_summary is one pixel.
- "reliability" says how far the radar can be trusted at that place (beam
  blocked by mountains, far from every radar). If "reliable" is false, say so.
- rain_ahead is a forecast by extrapolation, good for the next hour or two:
  present it as a forecast, never as a measurement, and quote its limits.
- rarity compares with the radar's own history (2018-2025), capped at 50
  years: "at_least" true means "50 years or more", not a number.
- Nothing here is an alert. The official alert is the DPC criticality
  bulletin (official_alert) and the messages of the regional civil
  protection. For danger to people, point to them and to 112.
- Times are UTC unless a field says otherwise; bulletins are in Italian time.

Data: Dipartimento della Protezione Civile, CC BY-SA 4.0.
"""


def create_server(
    radar: Callable[[], Source] | None = None,
    *,
    bulletin: Callable[[], Awaitable[Bulletin]] | None = None,
) -> MCPServer[Any]:
    """The MCP server, its tools bound to one :class:`dpc_radar.facts.Facts`.

    ``radar`` builds the client on the first call (default: a
    :class:`dpc_radar.RadarClient`); ``bulletin`` fetches the newest bulletin
    (default: :func:`dpc_radar.bulletins.latest`).
    """
    from dpc_radar import __version__

    state: dict[str, Any] = {}

    def facts() -> Facts:
        if "facts" not in state:
            state["radar"] = (radar or RadarClient)()
            state["facts"] = Facts(state["radar"], bulletin=bulletin)
        found: Facts = state["facts"]
        return found

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            close = getattr(state.pop("radar", None), "aclose", None)
            if close is not None:
                await close()

    server: MCPServer[Any] = MCPServer(
        name="dpc-radar",
        title="Rain over Italy (DPC radar)",
        instructions=INSTRUCTIONS,
        website_url="https://agentengineering.it/dpc-radar/",
        version=__version__,
        lifespan=lifespan,
    )

    @server.tool()
    async def latest() -> dict[str, Any]:
        """The newest frame of every product: its time (UTC) and how often a new one comes.

        Start here to know how fresh the data are.
        """
        return await _guard(facts().latest())

    @server.tool()
    async def rain_at(lon: float, lat: float, hours: int = 1) -> dict[str, Any]:
        """The rain of the last hours at a point, in mm, with the frame it was read from.

        ``lon`` and ``lat`` in degrees (WGS84); ``hours`` is 1, 3, 6, 12 or 24.
        The value is the median of the 3x3 pixels around the point. 1 hour is
        the radar alone; 3 hours and more are radar corrected with rain gauges.
        ``mm`` null means not measured, not dry.
        """
        return await _guard(facts().rain_at(lon, lat, hours))

    @server.tool()
    async def area_summary(
        west: float, south: float, east: float, north: float, hours: int = 1
    ) -> dict[str, Any]:
        """The rain of the last hours over a box: maximum and where, mean, km² over thresholds.

        The box is in degrees (WGS84); ``hours`` is 1, 3, 6, 12 or 24.
        ``km2_over`` is the area that got at least 1, 5, 10, 20, 50 and 100 mm.
        """
        return await _guard(facts().area_summary(west, south, east, north, hours))

    @server.tool()
    async def storm_now(
        west: float | None = None,
        south: float | None = None,
        east: float | None = None,
        north: float | None = None,
    ) -> dict[str, Any]:
        """The storms raining now (patches at 10 mm/h or more), the heaviest first.

        With a box in degrees, only the storms that touch it; without, all of
        Italy (the 20 heaviest are listed, ``count`` is of them all). For each:
        centre, area, peak intensity in mm/h, and its motion over the last
        half hour (km/h, and degrees from north of where it goes).
        """
        return await _guard(facts().storm_now(west, south, east, north))

    @server.tool()
    async def rain_ahead(
        lon: float, lat: float, minutes: int = 60, mm: float = 20.0
    ) -> dict[str, Any]:
        """A forecast: the rain of the next minutes (5 to 120) at a point.

        The rain seen now, moved along its motion of the last half hour:
        ``forecast_mm``, and the chance of at least ``mm``. Good for an hour
        or two, poor on the peaks; always present it as a forecast.
        """
        return await _guard(facts().rain_ahead(lon, lat, minutes, mm))

    @server.tool()
    async def official_alert(
        lon: float | None = None, lat: float | None = None, place: str | None = None
    ) -> dict[str, Any]:
        """The official alert level of the DPC criticality bulletin, today and tomorrow.

        With ``lon`` and ``lat``: the alert zone of that point. With
        ``place``: the zones that hold a municipality of that name (in
        Italian, such as "Reggio di Calabria"), or whose own name contains
        it. The bulletin does not name regions: for a region, ask with
        neither and read the zone names, or ask by point. With neither:
        every zone on yellow, orange or red.
        """
        return await _guard(facts().official_alert(lon, lat, place))

    @server.tool()
    async def rarity(lon: float, lat: float, hours: int, mm: float) -> dict[str, Any]:
        """How rare ``mm`` of rain in ``hours`` (1, 3, 6 or 24) are at a point.

        The return period in years against the radar's history (2018-2025),
        with its interval, capped at 50 years. The amount must be radar rain
        (rain_at with hours=1, or a sum of those), not a rain gauge reading.
        """

        async def answer() -> dict[str, Any]:
            return facts().rarity(lon, lat, hours, mm)

        return await _guard(answer())

    return server


def run() -> None:
    """Serve on standard input and output, until the client hangs up."""
    # httpx logs every request, signed address included: too much for a log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    create_server().run("stdio")


async def _guard[T](call: Awaitable[T]) -> T:
    """What the library refuses, as an error the agent can read and act on."""
    try:
        return await call
    except (DpcRadarError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"the DPC did not answer ({exc!r}): try again in a minute") from exc
    except ImportError as exc:
        raise ToolError(f"{exc}: pip install 'dpc-radar[mcp]'") from exc


__all__ = ["INSTRUCTIONS", "create_server", "run"]
