---
name: dpc-radar
description: Read the rain over Italy from the open radar data of the Italian Civil Protection (DPC) in Python with the dpc-radar library - latest frames, rain at a point or over a box, frame quality checks, the radar-gauge «Merging», storms, nowcast, return periods, the official criticality bulletin, event reports. Use when code or an answer needs rainfall, radar or weather-alert data for Italy, or mentions DPC, Protezione Civile, radar-dpc, SRI, SRT1, CUM24 or dpc-radar.
---

# dpc-radar

`dpc-radar` is a typed, async Python client for the radar platform of the
Italian Civil Protection (`https://radar-api.protezionecivile.it`). Use it
rather than raw HTTP: the API is small, undocumented and full of traps (a
frame that is gone answers 500, two products live on two different grids,
published frames can be corrupted). The library already handles them.

Full documentation: https://agentengineering.it/dpc-radar/ ·
everything as plain text: https://agentengineering.it/dpc-radar/llms-full.txt

## Setup

```bash
pip install dpc-radar                      # the client, grids, quality, climatology
pip install "dpc-radar[storms,nowcast,bulletins]"  # + storms, the next two hours, the alert
pip install "dpc-radar[mcp]"               # + the MCP server:  dpc-radar mcp
pip install "dpc-radar[report]"            # + event reports:   dpc-radar report
```

Python 3.12 or newer. No account and no key: the data are open
(CC BY-SA 4.0, credit "Dipartimento della Protezione Civile").

## The data, in one paragraph

- **`SRI`**: rain intensity now, mm/h, every 5 minutes. **`SRT1`**: the rain
  of the last hour, mm, radar alone. Both on a **1 km grid in a Transverse
  Mercator projection**.
- **`CUM3`, `CUM6`, `CUM12`, `CUM24`**: the rain of the last 3 to 24 hours,
  mm, every 30 minutes: the **«Merging»**, radar corrected with the rain
  gauges. On a **0.01° grid in EPSG:4326**. There is no `CUM1`.
- A frame is a `RainGrid`: `product`, `time` (UTC, when the window ends),
  `data` (float array, **NaN = not measured**), `transform`, `crs`.
- The DPC keeps frames for about **five months**. The radar-only history
  since 2010 is another dataset (IT-DPC-SRI, `dpc_radar.history`).

## Recipes

```python
import asyncio
from datetime import timedelta

from dpc_radar import ABSENT, RadarClient, radar_total, reliability
from dpc_radar.climatology import rarity


async def main() -> None:
    async with RadarClient() as radar:
        # The rain of the last 24 hours at a place: lon, lat in degrees.
        now = await radar.latest("CUM24")
        before = await radar.at("CUM24", now.time - now.product.period)
        q = now.quality(None if before is ABSENT else before)  # never trust one frame alone
        rain = q.grid  # None if the frame is rejected: then use `before`
        print(rain.sample([(15.78, 37.92)]), now.product.name, now.time)  # [mm or None]

        # Over a box (west, south, east, north): highest value, pixels over a threshold.
        print(rain.peak((15.5, 37.85, 16.2, 38.3), threshold=50))

        # The radar alone over three hours: the sum of the hourly frames.
        hours = [await radar.at("SRT1", now.time - timedelta(hours=h)) for h in range(3)]
        if not any(h is ABSENT for h in hours):
            total = radar_total(hours)

    print(reliability(15.78, 37.92).score)  # 0 to 1: how far to trust the radar there
    print(rarity(24, 15.78, 37.92, 100.0).return_period)  # years, None outside the map


asyncio.run(main())
```

- In Jupyter a loop is already running: write `await ...` at the top level
  instead of `asyncio.run`.
- A frame at a given time: `await radar.at("CUM3", time)` gives a `RainGrid`
  or `ABSENT` (the DPC no longer keeps it). Times must be aware datetimes.
- The facts with their sources, ready to print or to hand to a model:
  `from dpc_radar.facts import Facts`, then `await Facts(radar).rain_at(lon,
  lat, hours=3)`, `area_summary`, `storm_now`, `rain_ahead`,
  `official_alert`. Every answer is a dict with the product and the time of
  its frame.
- Storms as objects: `from dpc_radar import storms, track`. The next two
  hours: `from dpc_radar import nowcast, rain_ahead, chance, motion` on the
  `SRI` frames of the last half hour, the oldest first.
- The official alert: `from dpc_radar import bulletins`, then
  `(await bulletins.latest()).zone_at(lon, lat)`. Levels: 0 none, 1 yellow,
  2 orange, 3 red.
- Hourly «Merging» rain from frames you saved: `dpc_radar.archive`
  (`archive(folder, radar)` every night, `MergingArchive(folder).hourly(...)`).
  `await init()` (or `dpc-radar init`) fills the library's folder,
  `$DATA_ROOT/dpc-radar` or `~/.cache/dpc-radar`, and `MergingArchive()` with
  no folder reads it.
- The report of a day over a box, from a shell:
  `dpc-radar report --bbox W,S,E,N --day YYYY-MM-DD --out folder` writes
  `facts.json`, `report.md` and two images. If you rewrite the text, run
  `dpc-radar check report.md facts.json`: a number that is not in the facts
  is refused.

## Rules (each one is a real failure)

1. **There is no `CUM1`.** Asking for it gives an empty list, not an error.
   The shortest «Merging» is `CUM3`; for one hour use `SRT1` (radar alone).
2. **NaN and `None` mean "not measured", not "dry".** Never write that it
   did not rain because a value is missing. Check `reliability(lon, lat)`:
   where the radar is blind (behind mountains, far from every radar), a zero
   is not evidence of a dry sky either.
3. **Never trust one frame alone.** Published frames have carried up to
   20,584 mm of false rain for days. Use `frame.quality(previous)` (or
   `check`) and read places with `sample`, the median of 3×3 pixels, never
   one pixel.
4. **Two grids.** `SRI`/`SRT1` are 1 km Transverse Mercator; `CUM*` are
   0.01° EPSG:4326. Never add, subtract or index one array with the other:
   use `sample`, `peak`, `crop`, which take degrees on both.
5. **A frame that is gone answers HTTP 500** with S3's `403 Forbidden` in
   the body, not 404. `radar.at` returns `ABSENT`: do not retry it.
6. **The radar alone is not the truth.** In 2026 it read 20 % more than the
   «Merging» on average, up to 1.7 times in places. Prefer `CUM*`; for
   `SRT1`/`SRI` of 2026, `correct(frame)`. Always say which one a number is.
7. **A gauge can be wrong too**: stuck gauges have put 126.6 mm every three
   hours into the «Merging» on a dry night. `frame.quality(radar=...)` with
   the radar total of the same window masks them.
8. **Times are UTC**, the time of a frame is the end of its window, and the
   API speaks epoch milliseconds (`to_epoch_ms` / `from_epoch_ms`). The
   bulletin is in Italian time.
9. **Return periods are in radar millimetres**, from eight years
   (2018–2025), capped at 50 years: "50 years or more" is not a number, and
   these are not the levels of the official rainfall curves.
10. **A nowcast is a forecast.** `nowcast`, `rain_ahead` and `chance` return
    a `Forecast`, never a `RainGrid`: do not present them as measured rain.
    They are fair on where rain will be in 30–60 minutes and poor on peaks.
11. **Errors:** `DpcRadarError` is an answer the library cannot read or a
    bad argument (a point outside the grid, a window that does not exist).
    Its message says what to change: show it rather than retrying blindly.

## Without writing code

If the user only needs answers (not code), the MCP server may already be
connected: tools `latest`, `rain_at`, `area_summary`, `storm_now`,
`rain_ahead`, `official_alert`, `rarity` and `radar_status` from `dpc-radar mcp` (extra
`mcp`). Prefer them to downloading frames by hand. To add it to Claude Code:

```bash
claude mcp add dpc-radar -- uvx --from "dpc-radar[mcp]" dpc-radar mcp
```

## When reporting results

State the product, the time of the frame (UTC) and whether the number is
radar alone or «Merging». Do not compute numbers the tools did not give
you. Radar rain is evidence, not an alert: the official alert is the DPC
criticality bulletin and the messages of the regional civil protection. For
danger to people, point to them and to 112, never to a radar reading.

Data: Dipartimento della Protezione Civile, CC BY-SA 4.0.
