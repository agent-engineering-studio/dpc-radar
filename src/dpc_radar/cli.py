"""The ``dpc-radar`` command.

* ``dpc-radar mcp``: the MCP server, on standard input and output
  (:mod:`dpc_radar.mcp_server`).
* ``dpc-radar report --bbox W,S,E,N --day YYYY-MM-DD --out folder``: the
  report of a day of rain over a box (:mod:`dpc_radar.report`). The frames
  come from the API, or from a local archive with ``--archive folder``.
* ``dpc-radar check report.md facts.json``: are all the numbers of a text in
  the facts? For a text written, or rewritten, by someone else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
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

    check = commands.add_parser("check", help="are the numbers of a text all in the facts?")
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


def _check(args: argparse.Namespace) -> int:
    from dpc_radar.report import check_text

    strangers = check_text(args.text.read_text(), json.loads(args.facts.read_text()))
    if strangers:
        print("numbers that are not in the facts: " + ", ".join(strangers), file=sys.stderr)
        return 1
    print("every number of the text is in the facts")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; the exit status (0: done)."""
    args = _parser().parse_args(argv)
    run = {"mcp": _mcp, "report": _report, "check": _check}[args.command]
    try:
        return run(args)
    except ImportError as exc:
        extra = "mcp" if args.command == "mcp" else "report"
        print(f"{exc}: pip install 'dpc-radar[{extra}]'", file=sys.stderr)
    except (DpcRadarError, OSError, httpx.HTTPError, ValueError) as exc:
        print(f"dpc-radar: {exc}", file=sys.stderr)
    return 1


__all__ = ["main"]
