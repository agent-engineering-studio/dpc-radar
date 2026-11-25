"""Where the library keeps its data on disk, and :func:`init`, which fills it.

The folder is, in this order:

1. the one the caller passes;
2. ``$DATA_ROOT/dpc-radar``: ``DATA_ROOT`` is the root shared with Limen and
   the other libraries of agent-engineering-studio, each in a folder of its own;
3. ``$XDG_CACHE_HOME/dpc-radar``, or ``~/.cache/dpc-radar``.

Inside it:

* ``merging/``: the «Merging» frames, as :func:`dpc_radar.archive.archive`
  writes them and :class:`~dpc_radar.archive.MergingArchive` reads them;
* ``it_dpc_sri/``: the IT-DPC-SRI Zarr store, as
  :func:`dpc_radar.history.mirror` writes it and
  :class:`~dpc_radar.history.SriHistory` reads it. About 51 GB: :func:`init`
  downloads it only when asked.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from dpc_radar.archive import ArchiveReport

PACKAGE: Final = "dpc-radar"
MERGING: Final = "merging"
IT_DPC_SRI: Final = "it_dpc_sri"


def default_directory() -> Path:
    """The library's folder when the caller names none: see the module."""
    root = os.environ.get("DATA_ROOT")
    if root:
        return Path(root).expanduser() / PACKAGE
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / PACKAGE


def merging_directory(directory: Path | None = None) -> Path:
    """Where the «Merging» frames live, under ``directory`` or the default folder."""
    return (directory if directory is not None else default_directory()) / MERGING


def history_directory(directory: Path | None = None) -> Path:
    """Where the IT-DPC-SRI store lives, under ``directory`` or the default folder."""
    return (directory if directory is not None else default_directory()) / IT_DPC_SRI


@dataclass
class InitReport:
    """What one :func:`init` run did: the «Merging», and the history if it was asked."""

    merging: ArchiveReport
    history: ArchiveReport | None = None

    def _parts(self) -> list[ArchiveReport]:
        return [self.merging] if self.history is None else [self.merging, self.history]

    @property
    def downloaded(self) -> int:
        return sum(p.downloaded for p in self._parts())

    @property
    def present(self) -> int:
        return sum(p.present for p in self._parts())

    @property
    def lost(self) -> int:
        return sum(p.lost for p in self._parts())

    @property
    def failed(self) -> int:
        return sum(p.failed for p in self._parts())

    @property
    def bytes(self) -> int:
        return sum(p.bytes for p in self._parts())


async def init(
    directory: Path | None = None,
    *,
    days: int = 2,
    history: bool = False,
    client: httpx.AsyncClient | None = None,
    retries: int = 3,
    concurrency: int = 32,
    now: datetime | None = None,
) -> InitReport:
    """Download what the library needs to work offline into ``directory``.

    The «Merging» frames of the last ``days`` (about 35 MB a day) into
    ``merging/``; with ``history``, the IT-DPC-SRI store (2010-2025, about
    51 GB) into ``it_dpc_sri/``, ``concurrency`` files at a time.

    Safe to run again: a file on disk is not downloaded twice, and one the
    source no longer has is written down and not asked again. Files are
    written as ``.part`` and renamed. A file that fails is counted in the
    report and retried on the next run; a disk that cannot be written raises.

    ``client`` is used for every request and left open; ``retries=0`` leaves
    the retrying to the caller.
    """
    from dpc_radar.archive import archive
    from dpc_radar.client import RadarClient
    from dpc_radar.history import mirror

    end = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    async with RadarClient(client, retries=retries) as radar:
        merging = await archive(
            merging_directory(directory),
            radar,
            start=end - timedelta(days=days),
            end=end,
            limit=sys.maxsize,
            now=now,
        )
    if not history:
        return InitReport(merging)
    sri = await mirror(
        history_directory(directory), client, retries=retries, concurrency=concurrency
    )
    return InitReport(merging, sri)


__all__ = [
    "IT_DPC_SRI",
    "MERGING",
    "PACKAGE",
    "InitReport",
    "default_directory",
    "history_directory",
    "init",
    "merging_directory",
]
