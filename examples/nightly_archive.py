"""Keep your own archive of the «Merging», one run a night.

    uv run python examples/nightly_archive.py ~/radar-archive
    uv run python examples/nightly_archive.py ~/radar-archive --days 30 --limit 400

The DPC deletes each frame after about five months, and nobody keeps a public
archive of the «Merging». This script saves the CUM3 and SRT1 frames of the
last days that are not on disk yet. Run it again and it downloads only what is
new: it is safe to schedule, for example with cron at 03:10 every night:

    10 3 * * *  cd /path/to/dpc-radar && uv run python examples/nightly_archive.py ~/radar-archive

A day is about 34 frames and 35 MB. Read the archive back with
``MergingArchive(folder).hourly(points, start, end)``: see notebook 4.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dpc_radar import RadarClient
from dpc_radar.archive import archive


async def main(folder: Path, days: int, limit: int) -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    async with RadarClient() as radar:
        report = await archive(
            folder, radar, start=end - timedelta(days=days), end=end, limit=limit
        )
    print(
        f"{report.downloaded} frames saved ({report.bytes / 1e6:.0f} MB), "
        f"{report.present} already there, {report.lost} gone at the DPC, "
        f"{report.failed} to retry next time"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Save the DPC radar frames before they expire.")
    parser.add_argument("folder", type=Path, help="where the archive lives")
    parser.add_argument("--days", type=int, default=3, help="how far back to look (default 3)")
    parser.add_argument("--limit", type=int, default=200, help="most requests in one run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")  # why a frame failed
    asyncio.run(main(args.folder.expanduser(), args.days, args.limit))
