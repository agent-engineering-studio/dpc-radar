"""Watch an area and say when a violent shower enters it.

    uv run python examples/storm_watch.py                    # the whole of Italy
    uv run python examples/storm_watch.py --box 15.25 37.75 17.25 39.45 --rate 20
    uv run python examples/storm_watch.py --once             # one look, then stop

Every five minutes the DPC publishes a new SRI frame: how hard it is raining,
in mm/h. This script asks for it, looks inside the box and prints one line. A
frame that has not changed is not downloaded again.

Replace ``notify`` with an e-mail, a chat message or a webhook. Radar only:
it shows where it is raining hard now, it does not forecast and it is not an
official alert.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from dpc_radar import DpcRadarError, RadarClient, RainGrid
from dpc_radar.grid import BBox

ITALY: BBox = (6.0, 36.3, 19.0, 47.3)


def notify(message: str) -> None:
    """What to do when the threshold is passed: here, a line on the screen."""
    print(f"!! {message}")


def look(frame: RainGrid, box: BBox, rate: float) -> None:
    top, pixels = frame.peak(box, threshold=rate)
    print(f"{frame.time:%H:%M} UTC  strongest rain {top:5.1f} mm/h")
    if pixels:
        notify(f"{rate:.0f} mm/h or more on about {pixels} km² (peak {top:.0f} mm/h)")


async def watch(box: BBox, rate: float, every: float, once: bool) -> None:
    frame: RainGrid | None = None
    async with RadarClient() as radar:
        while True:
            try:
                newest = await radar.latest("SRI", cached=frame)
            except (DpcRadarError, httpx.HTTPError) as exc:
                print(f"no frame this time: {exc}")  # the next round tries again
            else:
                if newest is not frame:
                    frame = newest
                    look(frame, box, rate)
            if once:
                return
            await asyncio.sleep(every)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tell me when it rains hard in a box.")
    parser.add_argument(
        "--box",
        type=float,
        nargs=4,
        default=ITALY,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="the area to watch, in degrees (default: Italy)",
    )
    parser.add_argument("--rate", type=float, default=30.0, help="mm/h that count as violent")
    parser.add_argument("--every", type=float, default=300.0, help="seconds between two looks")
    parser.add_argument("--once", action="store_true", help="look once and stop")
    args = parser.parse_args()
    try:
        asyncio.run(watch(tuple(args.box), args.rate, args.every, args.once))
    except KeyboardInterrupt:
        print("stopped")
