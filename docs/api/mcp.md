# MCP server: the rain as tools

`dpc-radar mcp` gives an assistant (Claude, or any client of the
[Model Context Protocol](https://modelcontextprotocol.io/)) seven tools on
the rain over Italy. Someone asks "how much has it rained on Messina in the
last three hours?" and the answer is computed from a frame, with the name of
the product and the time of the frame: the model words it, it does not
compute it.

```bash
pip install "dpc-radar[mcp]"
dpc-radar mcp   # speaks MCP on standard input and output

# Claude Code, with nothing to install first:
claude mcp add dpc-radar -- uvx --from "dpc-radar[mcp]" dpc-radar mcp
```

For Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dpc-radar": { "command": "uvx", "args": ["--from", "dpc-radar[mcp]", "dpc-radar", "mcp"] }
  }
}
```

No key: the data are open.

| Tool | What it answers |
|---|---|
| `latest()` | the newest frame of every product, and how often a new one comes |
| `rain_at(lon, lat, hours=1)` | the rain of the last 1, 3, 6, 12 or 24 hours at a point: the median of 3×3 pixels, with the reliability of the radar there |
| `area_summary(west, south, east, north, hours=1)` | over a box: maximum and where, mean, median, 95th percentile, km² over 1, 5, 10, 20, 50 and 100 mm |
| `storm_now(west, south, east, north)` | the storms on the newest intensity frame (patches at 10 mm/h or more), with their motion; all of Italy without a box |
| `rain_ahead(lon, lat, minutes=60, mm=20)` | the [nowcast](nowcast.md) at a point, labelled as a forecast, with the chance of at least `mm` |
| `official_alert(lon, lat, place)` | the level of the [criticality bulletin](bulletins.md): the zone of a point, the zones of a municipality or with a name, or every zone on alert |
| `rarity(lon, lat, hours, mm)` | the [return period](climatology.md) of an amount of radar rain at a place |

This is the answer of `rain_at(15.78, 37.92, hours=3)`:

```json
{
  "lon": 15.78, "lat": 37.92, "hours": 3,
  "mm": 0.0, "measured": true,
  "method": "median of the 3x3 pixels around the point",
  "frame": {
    "product": "CUM3", "time": "2026-11-17T10:30Z", "unit": "mm", "hours": 3,
    "source": "radar and rain gauges («Merging»)"
  },
  "quality": {
    "verdict": "consistent", "trusted_share": 1.0, "masked_pixels": 0,
    "reasons": {"jump": 0, "beyond_record": 0, "outlier": 0, "gauge_only": 0},
    "newest_frame_rejected": null
  },
  "reliability": {
    "score": 0.9, "reliable": true, "beam_height_m": 1100.0,
    "nearest_radar": "Catania Fontanarossa", "distance_km": 81.7
  },
  "note": "radar data, not an alert: the official alert is the DPC bulletin"
}
```

**What keeps the numbers honest.**

- Every answer carries its `frame`: product and time. One hour is the radar
  alone (`SRT1`), three hours and more are the «Merging»; the answer says
  which.
- Each frame goes through [`quality`](quality.md) against the frame before
  it. If the newest frame is rejected, the answer is read from the one
  before and says so (`newest_frame_rejected`). Pixels that fail a rule are
  masked.
- Not measured is `null`, with a note, never zero.
- `rain_ahead` answers `"kind": "forecast"`, with its limits in words.
- What the library refuses (a window that does not exist, half a box)
  reaches the model as an error it can read: `the DPC publishes the rain of
  [1, 3, 6, 12, 24] hours, not of 2`.
- The server's instructions tell the model not to add up or estimate
  numbers itself, and that nothing here is an alert.

A model can still word a number badly: the server gives it the facts and
the rules, it cannot read its answer. For a text that must be right, see
the [event reports](report.md), where the text is checked.

**`official_alert` and regions.** The bulletin names alert zones ("Versante
Jonico Meridionale") and their municipalities, not regions. `place` matches
a municipality (in Italian, the whole name) or part of a zone name; for a
region, ask by point or read the list of zones on alert.

The tools are the methods of `Facts`, which you can use from Python without
the server:

```python
from dpc_radar import RadarClient
from dpc_radar.facts import Facts

async with RadarClient() as radar:
    facts = Facts(radar)
    print(await facts.rain_at(15.78, 37.92, hours=3))
    print(await facts.area_summary(15.5, 37.85, 16.2, 38.3, hours=24))
```

::: dpc_radar.mcp_server

::: dpc_radar.facts
