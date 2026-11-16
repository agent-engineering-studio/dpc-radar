"""How much did it rain at a place? One command, one answer.

    uv run python examples/rain_at.py 15.78 37.92
    uv run python examples/rain_at.py 12.50 41.90 --name Roma

Prints the rain rate now, the rain of the last 3, 6, 12 and 24 hours, and the
official alert of the zone (the last line needs ``dpc-radar[bulletins]``).

Radar data and bulletins © Dipartimento della Protezione Civile (CC BY-SA 4.0
and CC BY 4.0). This is not an alert service.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from dpc_radar import DpcRadarError, RadarClient, bulletins

PRODUCTS = ("SRI", "CUM3", "CUM6", "CUM12", "CUM24")


def line(label: str, value: float | None, unit: str) -> str:
    """One row of the answer. ``None`` is "not measured", never "dry"."""
    if value is None:
        return f"  {label:<18} not measured (outside the radar's reach)"
    return f"  {label:<18} {value:6.1f} {unit}  {'#' * min(60, round(value / 2))}"


async def alert(lon: float, lat: float) -> str:
    """The official alert of the zone, today and tomorrow."""
    try:
        bulletin = await bulletins.latest()
    except (DpcRadarError, httpx.HTTPError) as exc:
        return f"  official alert      not available ({exc})"
    rows = []
    for day in bulletin.days():
        zone = bulletin.zone_at(lon, lat, day)
        if zone is None:
            return "  official alert      the point is in no alert zone"
        rows.append(f"  alert {day:%d %b}        {zone.colour:<7} zone «{zone.name}»")
    return "\n".join(rows)


async def main(lon: float, lat: float, name: str) -> None:
    async with RadarClient() as radar:
        grids = [await radar.latest(p) for p in PRODUCTS]
    print(f"{name} ({lon:.3f}, {lat:.3f}), frames to {grids[-1].time:%d %B %Y %H:%M} UTC")
    for grid in grids:
        [value] = grid.sample([(lon, lat)])
        hours = grid.product.hours
        label = "raining now" if hours is None else f"last {hours} hours"
        print(line(label, value, "mm/h" if hours is None else "mm"))
    print(await alert(lon, lat))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rain at a place, from the open DPC radar data.")
    parser.add_argument("lon", type=float, help="longitude, degrees east (Roma: 12.50)")
    parser.add_argument("lat", type=float, help="latitude, degrees north (Roma: 41.90)")
    parser.add_argument("--name", default="Here", help="a name for the place")
    args = parser.parse_args()
    asyncio.run(main(args.lon, args.lat, args.name))
