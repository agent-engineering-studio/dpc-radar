from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from dpc_radar import bulletins as b
from dpc_radar import mcp_server as m
from dpc_radar.errors import DpcRadarError
from tests.conftest import Radar
from tests.test_facts import POINT, bulletin, flat
from tests.test_nowcast import CENTRE, moving


def radar() -> Radar:
    return Radar([flat(4.0, "CUM3"), flat(4.0, "CUM3", -30), flat(2.0, "SRT1"), *moving(frames=7)])


async def newest() -> b.Bulletin:
    return bulletin()


async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    found: dict[str, Any] = result.structured_content
    return found


def words(result: Any) -> str:
    (content,) = result.content
    assert isinstance(content, TextContent)
    return content.text


async def test_the_tools_and_the_rules() -> None:
    async with Client(m.create_server(radar, bulletin=newest)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert sorted(tools) == [
            "area_summary",
            "latest",
            "official_alert",
            "radar_status",
            "rain_ahead",
            "rain_at",
            "rarity",
            "storm_now",
        ]
        assert all(tool.description for tool in tools.values())
        assert tools["rain_at"].input_schema["required"] == ["lon", "lat"]
        assert client.instructions == m.INSTRUCTIONS
        assert "null means not measured" in m.INSTRUCTIONS


async def test_every_tool_answers_with_its_frame() -> None:
    lon, lat = POINT
    async with Client(m.create_server(radar, bulletin=newest)) as client:
        products = (await call(client, "latest"))["products"]
        assert [p["product"] for p in products][:3] == ["SRI", "SRT1", "CUM3"]

        rain = await call(client, "rain_at", lon=lon, lat=lat, hours=3)
        assert rain["mm"] == 4.0 and rain["frame"]["product"] == "CUM3"
        assert rain["frame"]["time"] == "2026-10-10T06:00Z"
        assert (await call(client, "rain_at", lon=lon, lat=lat))["frame"]["product"] == "SRT1"

        area = await call(client, "area_summary", west=15.02, south=37.82, east=15.18, north=37.98)
        assert area["max_mm"] == 2.0 and area["frame"]["product"] == "SRT1"

        storms = await call(client, "storm_now")
        assert storms["count"] == 1 and storms["frame"]["product"] == "SRI"

        ahead = await call(client, "rain_ahead", lon=CENTRE[0], lat=CENTRE[1], minutes=30)
        assert ahead["kind"] == "forecast" and ahead["until"] == "2026-10-10T06:30Z"

        alert = await call(client, "official_alert", lon=15.65, lat=38.1)
        assert alert["bulletin"] == "20261010_1417" and alert["zones"][0]["colour"] == "orange"
        assert len((await call(client, "official_alert", place="bagaladi"))["zones"]) >= 1

        rare = await call(client, "rarity", lon=15.785, lat=37.925, hours=3, mm=50.0)
        assert rare["hours"] == 3 and rare["mm"] == 50.0 and "return_period_years" in rare

        radars = await call(client, "radar_status", lon=lon, lat=lat)
        assert radars["known"] is False and radars["radars"][0]["on"] is None


async def test_what_the_library_refuses_is_an_error_the_agent_can_read() -> None:
    lon, lat = POINT
    async with Client(m.create_server(radar, bulletin=newest)) as client:
        result = await client.call_tool("rain_at", {"lon": lon, "lat": lat, "hours": 2})
        assert result.is_error
        assert "Error executing tool rain_at" in words(result)
        assert "1, 3, 6, 12" in words(result)
        result = await client.call_tool("rarity", {"lon": lon, "lat": lat, "hours": 2, "mm": 9.0})
        assert result.is_error and "no climatology for 2 h" in words(result)


async def test_guard() -> None:
    async def fails(exc: Exception) -> None:
        raise exc

    with pytest.raises(ToolError, match="outside the grid"):
        await m._guard(fails(DpcRadarError("outside the grid")))
    with pytest.raises(ToolError, match="not a number"):
        await m._guard(fails(ValueError("not a number")))
    with pytest.raises(ToolError, match=r"did not answer .*try again in a minute"):
        await m._guard(fails(httpx.ConnectError("no route")))
    with pytest.raises(ToolError, match=r"No module named 'scipy': pip install 'dpc-radar\[mcp\]'"):
        await m._guard(fails(ImportError("No module named 'scipy'")))


async def test_the_radar_is_built_once_and_closed_at_the_end() -> None:
    built: list[Radar] = []

    def build() -> Radar:
        built.append(radar())
        return built[-1]

    async with Client(m.create_server(build, bulletin=newest)) as client:
        assert built == []  # nothing is opened before the first question
        await call(client, "latest")
        await call(client, "latest")
        assert len(built) == 1 and not built[0].closed
    assert built[0].closed

    async with Client(m.create_server(build)):
        pass  # no question, nothing to close
    assert len(built) == 1


def test_run_serves_on_standard_input_and_output(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[str] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport: served.append(transport))
    m.run()
    assert served == ["stdio"]
