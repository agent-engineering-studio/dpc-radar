"""The ``dpc-radar`` command.

* ``dpc-radar mcp``: the MCP server, on standard input and output
  (:mod:`dpc_radar.mcp_server`).
* ``dpc-radar report --bbox W,S,E,N --day YYYY-MM-DD --out folder``: the
  report of a day of rain over a box (:mod:`dpc_radar.report`). The frames
  come from the API, or from a local archive with ``--archive folder``.
* ``dpc-radar archive [folder] --days 2``: save the «Merging» frames of the
  last days that are not in the folder yet (:mod:`dpc_radar.archive`). Safe to
  run again, and to schedule: it downloads only what is missing. Without a
  folder, ``merging/`` in the library's folder (:mod:`dpc_radar.local`).
* ``dpc-radar init [directory] --days 2 [--history]``: download what the
  library needs to work offline into its folder (:func:`dpc_radar.local.init`);
  ``--history`` adds the IT-DPC-SRI store, about 51 GB.
* ``dpc-radar sites``: which radars the DPC reports on and off, now or at
  ``--time``; ``--out sites.geojson`` saves them as GeoJSON
  (:mod:`dpc_radar.status`).
* ``dpc-radar check report.md facts.json``: are all the numbers of a text in
  the facts? For a text written, or rewritten, by someone else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import BBox


def _bbox(text: str) -> BBox:
    try:
        west, south, east, north = (float(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"a box is W,S,E,N in degrees: {text!r}") from None
    if not (west < east and south < north):
        raise argparse.ArgumentTypeError(f"a box is W,S,E,N with W < E and S < N: {text!r}")
    return (west, south, east, north)


def _days(text: str) -> int:
    try:
        days = int(text)
    except ValueError:
        days = 0
    if days < 1:
        raise argparse.ArgumentTypeError(f"a number of days, 1 or more: {text!r}")
    return days


def _time(text: str) -> datetime:
    try:
        time = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"a time is YYYY-MM-DDTHH:MM, UTC: {text!r}") from None
    return time if time.tzinfo else time.replace(tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpc-radar",
        description="The open radar data of the Italian Civil Protection (DPC).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp", help="serve the MCP tools on standard input and output")

    report = commands.add_parser("report", help="the report of a day of rain over a box")
    report.add_argument("--bbox", type=_bbox, required=True, metavar="W,S,E,N")
    report.add_argument("--day", required=True, metavar="YYYY-MM-DD", help="a UTC day")
    report.add_argument("--out", type=Path, required=True, help="the folder to write into")
    report.add_argument(
        "--archive", type=Path, help="read the frames from a local archive, not from the API"
    )
    report.add_argument(
        "--no-bulletin", action="store_true", help="do not look the official alert up"
    )
    report.add_argument(
        "--text", type=Path, help="a text of your own to save, if its numbers are in the facts"
    )
    report.add_argument("--no-images", action="store_true", help="write no image")

    archive = commands.add_parser("archive", help="save the «Merging» frames of the last days")
    archive.add_argument(
        "folder", type=Path, nargs="?", help="where the archive lives (default: the library's)"
    )
    archive.add_argument("--days", type=_days, default=2, help="how far back to look (default 2)")
    archive.add_argument(
        "--limit", type=int, default=200, help="most requests in one run (default 200)"
    )

    init = commands.add_parser("init", help="download what the library needs to work offline")
    init.add_argument(
        "directory", type=Path, nargs="?", help="the library's folder (default: see the docs)"
    )
    init.add_argument("--days", type=_days, default=2, help="days of «Merging» (default 2)")
    init.add_argument(
        "--history", action="store_true", help="also the IT-DPC-SRI store, 2010-2025 (~51 GB)"
    )
    init.add_argument(
        "--concurrency", type=int, default=32, help="IT-DPC-SRI files at a time (default 32)"
    )
    init.add_argument(
        "--retries", type=int, default=3, help="retries of a failed request (default 3)"
    )

    sites = commands.add_parser("sites", help="which radars the DPC reports on and off")
    sites.add_argument("--time", type=_time, metavar="YYYY-MM-DDTHH:MM", help="a UTC time")
    sites.add_argument("--out", type=Path, help="save the status as GeoJSON in this file")

    check = commands.add_parser(
        "check", help="are the numbers of a text in the facts, each of its own?"
    )
    check.add_argument("text", type=Path)
    check.add_argument("facts", type=Path)
    return parser


def _mcp(_: argparse.Namespace) -> int:
    from dpc_radar.mcp_server import run

    run()
    return 0


async def _build(args: argparse.Namespace) -> list[Path]:
    from dpc_radar import RadarClient
    from dpc_radar.archive import MergingArchive
    from dpc_radar.report import Read, build, bulletins_for, day_of, download

    day = day_of(args.day)
    if args.archive is not None:
        read: Read = MergingArchive(args.archive).read
    else:
        async with RadarClient() as radar:
            read = await download(radar, day)
    found = [] if args.no_bulletin else await bulletins_for(day)
    report = build(read, args.bbox, day, bulletins=found)
    text = None if args.text is None else args.text.read_text()
    written: list[Path] = report.save(args.out, text=text, images=not args.no_images)
    return written


def _report(args: argparse.Namespace) -> int:
    for path in asyncio.run(_build(args)):
        print(path)
    return 0


async def _save(args: argparse.Namespace) -> str:
    from dpc_radar import RadarClient
    from dpc_radar.archive import archive
    from dpc_radar.local import merging_directory

    folder = args.folder if args.folder is not None else merging_directory()
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    async with RadarClient() as radar:
        done = await archive(folder, radar, start=start, end=end, limit=args.limit)
    return (
        f"{done.downloaded} frames saved ({done.bytes / 1e6:.0f} MB), "
        f"{done.present} already there, {done.lost} gone at the DPC, "
        f"{done.failed} to retry next time"
    )


def _archive(args: argparse.Namespace) -> int:
    # Why a frame failed goes to the log; httpx would add a line for every request.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    print(asyncio.run(_save(args)))
    return 0


async def _download(args: argparse.Namespace) -> str:
    from dpc_radar.local import default_directory, init

    directory = args.directory if args.directory is not None else default_directory()
    done = await init(
        directory,
        days=args.days,
        history=args.history,
        retries=args.retries,
        concurrency=args.concurrency,
    )
    return (
        f"{directory}: {done.downloaded} files saved ({done.bytes / 1e6:.0f} MB), "
        f"{done.present} already there, {done.lost} gone at the source, "
        f"{done.failed} to retry next time"
    )


def _init(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    print(asyncio.run(_download(args)))
    return 0


async def _status(time: datetime | None) -> object:
    from dpc_radar import RadarClient

    async with RadarClient() as radar:
        return await radar.radar_status(time)


def _sites(args: argparse.Namespace) -> int:
    from dpc_radar.status import STATUS_CAVEAT, NetworkStatus

    status = asyncio.run(_status(args.time))
    if not isinstance(status, NetworkStatus):
        raise DpcRadarError("the DPC has no radar status for that time: not known")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(status.geojson(), ensure_ascii=False, indent=1) + "\n")
        print(args.out)
        return 0
    print(f"radar status of {status.time:%Y-%m-%dT%H:%MZ}")
    print(f"({STATUS_CAVEAT})")
    for r in status.radars:
        name = "(not in the mosaic)" if r.site is None else r.site.name
        print(f"{'on ' if r.on else 'OFF'}  {r.code:<13}{name:<23}{r.province}")
    return 0


def _check(args: argparse.Namespace) -> int:
    from dpc_radar.report import check_claims, check_text

    text, facts = args.text.read_text(), json.loads(args.facts.read_text())
    strangers = check_text(text, facts)
    if strangers:
        print("numbers that are not in the facts: " + ", ".join(strangers), file=sys.stderr)
        return 1
    misquotes = check_claims(text, facts)
    if misquotes:
        print("numbers said of the wrong fact:", file=sys.stderr)
        for m in misquotes:
            print(f"  {m}", file=sys.stderr)
        return 1
    print("every number of the text is in the facts, and none is said of the wrong one")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; the exit status (0: done)."""
    args = _parser().parse_args(argv)
    run = {
        "mcp": _mcp,
        "report": _report,
        "archive": _archive,
        "init": _init,
        "sites": _sites,
        "check": _check,
    }[args.command]
    try:
        return run(args)
    except ImportError as exc:
        extra = "mcp" if args.command == "mcp" else "report"
        print(f"{exc}: pip install 'dpc-radar[{extra}]'", file=sys.stderr)
    except (DpcRadarError, OSError, httpx.HTTPError, ValueError) as exc:
        print(f"dpc-radar: {exc}", file=sys.stderr)
    return 1


__all__ = ["main"]
