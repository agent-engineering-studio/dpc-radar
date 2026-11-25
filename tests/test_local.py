from __future__ import annotations

import json
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest

from dpc_radar import InitReport, default_directory, init
from dpc_radar.archive import ArchiveReport, MergingArchive, path
from dpc_radar.history import SRI_STORE, SriHistory, mirror
from dpc_radar.local import history_directory, merging_directory
from tests.conftest import tiff
from tests.test_history import _frame, store

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
STORE = httpx.URL(SRI_STORE)


def test_default_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "root"))
    assert default_directory() == tmp_path / "root" / "dpc-radar"
    assert merging_directory() == tmp_path / "root" / "dpc-radar" / "merging"
    assert history_directory(tmp_path) == tmp_path / "it_dpc_sri"
    monkeypatch.setenv("DATA_ROOT", "")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_directory() == tmp_path / "xdg" / "dpc-radar"
    monkeypatch.delenv("DATA_ROOT")
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_directory() == tmp_path / "home" / ".cache" / "dpc-radar"


def test_the_readers_default_to_the_library_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert MergingArchive().folder == tmp_path / "dpc-radar" / "merging"
    assert MergingArchive(tmp_path).folder == tmp_path
    (tmp_path / "dpc-radar").mkdir()
    store(tmp_path / "x", [0], {}).rename(tmp_path / "dpc-radar" / "it_dpc_sri")
    assert SriHistory().path == tmp_path / "dpc-radar" / "it_dpc_sri"


def remote(tmp_path: Path) -> Path:
    """An IT-DPC-SRI store of three frames, as the object store holds it."""
    root = store(tmp_path / "remote", [0, 5, 10], {0: _frame(4), 1: _frame(8), 2: _frame(8)})
    meta = {
        "RR/.zarray": json.loads((root / "RR" / ".zarray").read_text()),
        "time/.zarray": {"shape": [3]},
        "crs/.zattrs": json.loads((root / "crs" / ".zattrs").read_text()),
    }
    (root / ".zmetadata").write_text(json.dumps({"metadata": meta}))
    (root / ".zgroup").write_text('{"zarr_format": 2}')
    return root


def network(
    root: Path | None, *, gone: Collection[str] = (), broken: Collection[str] = ()
) -> tuple[httpx.AsyncClient, list[str]]:
    """The DPC API and the object store: every frame, except the ``gone`` and ``broken`` keys."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == STORE.host:
            key = request.url.path.removeprefix(STORE.path + "/")
            asked.append(key)
            if key in broken:
                return httpx.Response(503)
            file = root / key if root is not None else None
            if key in gone or file is None or not file.is_file():
                return httpx.Response(403)
            return httpx.Response(200, content=file.read_bytes())
        if request.url.path == "/downloadProduct":
            asked.append("dpc")
            return httpx.Response(200, json={"url": "https://s3.test/frame.tif"})
        return httpx.Response(200, content=tiff(np.ones((5, 5))))

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), asked


async def test_init_downloads_the_merging_of_the_last_days(tmp_path: Path) -> None:
    client, asked = network(None)
    done = await init(tmp_path, days=1, client=client, retries=0, now=NOW)
    assert done.history is None and done.failed == 0
    assert done.downloaded == 9 + 25  # CUM3 every three hours, SRT1 every hour, both ends
    assert done.bytes > 0 and asked.count("dpc") == 34
    assert path(tmp_path / "merging", "SRT1", NOW).exists()
    assert not list(tmp_path.rglob("*.part"))
    again = await init(tmp_path, days=1, client=client, retries=0, now=NOW)
    assert again.present == 34 and again.downloaded == 0
    assert not client.is_closed


async def test_init_with_the_history(tmp_path: Path) -> None:
    root = remote(tmp_path)
    client, asked = network(root, gone={"RR/1.0.0"}, broken={"RR/2.0.0"})
    local = tmp_path / "local"
    done = await init(local, days=1, client=client, retries=0, history=True, now=NOW)
    assert done.history is not None
    assert (done.history.downloaded, done.history.lost, done.history.failed) == (4, 2, 1)
    assert done.failed == 1 and done.lost == 2  # .zattrs is not in this store: gone too
    sri = SriHistory(local / "it_dpc_sri")
    assert len(sri) == 3 and sri.frame(0) is not None and sri.frame(1) is None
    assert (local / "it_dpc_sri" / "missing.txt").read_text() == "1\n"

    client, asked = network(root)
    again = await init(local, days=1, client=client, retries=0, history=True, now=NOW)
    assert again.history is not None and again.history.downloaded == 1  # RR/2
    assert "RR/1.0.0" not in asked and "RR/0.0.0" not in asked
    assert SriHistory(local / "it_dpc_sri").frame(2) is not None


async def test_mirror_retries_and_gives_up_without_raising(tmp_path: Path) -> None:
    root = remote(tmp_path)
    calls = {"n": 0}
    slept: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        key = request.url.path.removeprefix(STORE.path + "/")
        if key == "RR/0.0.0":
            return httpx.Response(503)
        file = root / key
        if not file.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=file.read_bytes())

    async def sleep(s: float) -> None:
        slept.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    done = await mirror(tmp_path / "m", client, retries=2, concurrency=2, sleep=sleep)
    assert done.failed == 1 and done.downloaded == 5  # .zmetadata after one retry
    assert slept[0] == 2.0 and slept.count(4.0) == 1
    assert not (tmp_path / "m" / "RR" / "0.0.0").exists()


async def test_mirror_without_metadata_stops(tmp_path: Path) -> None:
    client, _ = network(None, broken={".zmetadata"})
    done = await mirror(tmp_path, client, retries=0)
    assert done == ArchiveReport(failed=1)
    client, _ = network(None)
    assert await mirror(tmp_path, client, retries=0) == ArchiveReport(lost=1)


async def test_a_disk_that_cannot_be_written_raises(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    client, _ = network(None)
    with pytest.raises(OSError):
        await init(tmp_path / "file", days=1, client=client, retries=0, now=NOW)


def test_init_report_adds_up() -> None:
    a = ArchiveReport(downloaded=1, present=2, lost=3, failed=4, bytes=5)
    assert InitReport(a).bytes == 5
    both = InitReport(a, ArchiveReport(downloaded=10, bytes=50))
    assert (both.downloaded, both.present, both.lost, both.failed, both.bytes) == (
        11,
        2,
        3,
        4,
        55,
    )
