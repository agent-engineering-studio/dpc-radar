"""Build a catalogue of rare rain from a local archive of frames, as a CSV.

    uv run python -m tools.build_catalogue --archive <folder of archive()> --out events.csv \
        [--start 2026-06-09] [--end 2026-11-16] [--years 10] [--sea]

It needs a local archive of SRT1 and CUM3 frames (:func:`dpc_radar.archive.archive`).
Every hourly SRT1 frame goes through :meth:`dpc_radar.RainGrid.quality` against the
one before it (a rejected frame is an hour with no data), then
:func:`dpc_radar.catalogue.rare_events` finds the rain over the level of its place
and :func:`dpc_radar.catalogue.with_merging` asks the «Merging» about each event.

The land is the alert zones of the latest DPC criticality bulletin
(:mod:`dpc_radar.bulletins`, one call to GitHub): rain over the sea or over the
border is left out, and every event carries the name of its zone. ``--sea``
skips the call and keeps everything, without names.

The events come the rarest first: by return period, then by how many times the
level the rain was (the return period stops at 50 years).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.warp import transform as warp_points
from rasterio.warp import transform_geom

from dpc_radar import RainGrid, bulletins
from dpc_radar.archive import MergingArchive, times
from dpc_radar.catalogue import YEARS, Event, rare_events, with_merging

FIELDS = (
    "rank",
    "start",
    "end",
    "window_h",
    "zone",
    "lon",
    "lat",
    "radar_window_mm",
    "level_mm",
    "times_level",
    "return_period_y",
    "first_hour_over",
    "last_hour_over",
    "area_km2",
    "radar_blocks_mm",
    "merging_blocks_mm",
    "confirmed",
)


def frames(
    archive: MergingArchive, start: datetime, end: datetime, seen: Counter[str]
) -> Iterator[RainGrid]:
    """The hourly frames of the archive that can be used, with what was wrong masked."""
    previous: RainGrid | None = None
    for time in times("SRT1", start, end):
        frame = archive.read("SRT1", time)
        if frame is None:
            seen["missing"] += 1
            previous = None
            continue
        answer = frame.quality(previous)
        previous = frame
        if answer.grid is None:
            seen["rejected"] += 1
            continue
        seen["read"] += 1
        yield answer.grid


def land(frame: RainGrid) -> tuple[np.ndarray, list[str]]:
    """The number of the alert zone of every pixel of ``frame`` (0: none), and the names."""
    bulletin = asyncio.run(bulletins.latest())
    day = bulletin.days()[0]
    zones = [z for z in bulletin.zones if z.valid == day]
    shapes: list[tuple[Any, int]] = [
        (transform_geom("EPSG:4326", frame.crs, z.geometry.__geo_interface__), k)
        for k, z in enumerate(zones, start=1)
    ]
    number = rasterize(shapes, out_shape=frame.data.shape, transform=frame.transform, fill=0)
    return np.asarray(number), [z.name for z in zones]


def row(rank: int, e: Event, zone: str) -> dict[str, object]:
    return {
        "rank": rank,
        "start": f"{e.start:%Y-%m-%dT%H:%MZ}",
        "end": f"{e.time:%Y-%m-%dT%H:%MZ}",
        "window_h": e.window,
        "zone": zone,
        "lon": e.lon,
        "lat": e.lat,
        "radar_window_mm": e.mm,
        "level_mm": e.level_mm,
        "times_level": round(e.times_level, 2),
        "return_period_y": e.return_period,
        "first_hour_over": f"{e.first:%Y-%m-%dT%H:%MZ}",
        "last_hour_over": f"{e.last:%Y-%m-%dT%H:%MZ}",
        "area_km2": e.area_km2,
        "radar_blocks_mm": "" if e.radar_mm is None else e.radar_mm,
        "merging_blocks_mm": "" if e.merging_mm is None else e.merging_mm,
        "confirmed": "" if e.confirmed is None else str(e.confirmed).lower(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start", type=datetime.fromisoformat, default=datetime(2026, 6, 9))
    parser.add_argument("--end", type=datetime.fromisoformat, default=datetime(2026, 11, 16))
    parser.add_argument("--years", type=float, default=YEARS)
    parser.add_argument("--sea", action="store_true", help="keep the sea: no alert zones")
    args = parser.parse_args()
    start, end = args.start.replace(tzinfo=UTC), args.end.replace(tzinfo=UTC)

    archive = MergingArchive(args.archive, cache_frames=4)
    first = next((f for t in times("SRT1", start, end) if (f := archive.read("SRT1", t))), None)
    if first is None:
        raise SystemExit(f"no SRT1 frame in {args.archive} between {start} and {end}")
    number, names = (np.zeros(first.data.shape, int), []) if args.sea else land(first)
    seen: Counter[str] = Counter()
    events = rare_events(
        frames(archive, start, end, seen), years=args.years, inside=number > 0 if names else None
    )
    events = [with_merging(e, archive) for e in events]
    inv = ~first.transform
    with args.out.open("w", newline="") as out:
        writer = csv.DictWriter(out, FIELDS)
        writer.writeheader()
        for rank, e in enumerate(events, start=1):
            xs, ys = warp_points("EPSG:4326", first.crs, [e.lon], [e.lat])
            zone = number[int(inv.e * ys[0] + inv.f), int(inv.a * xs[0] + inv.c)]
            writer.writerow(row(rank, e, names[zone - 1] if zone else ""))

    days = (end - start).days
    print(f"{seen['read']} hours read, {seen['missing']} missing, {seen['rejected']} rejected")
    print(f"{len(events)} events over the {args.years:g}-year level in {days} days → {args.out}")
    for cut in (10.0, 20.0, 50.0):
        some = [e for e in events if e.return_period >= cut]
        yes = sum(1 for e in some if e.confirmed)
        no = sum(1 for e in some if e.confirmed is False)
        print(
            f"  {cut:g} years or more: {len(some)} ({30 * len(some) / days:.0f} a month), "
            f"{yes} confirmed by the «Merging», {no} not, {len(some) - yes - no} unknown"
        )


if __name__ == "__main__":
    main()
