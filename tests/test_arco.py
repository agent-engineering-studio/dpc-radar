from __future__ import annotations

import base64
import math
import struct
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from numcodecs import Blosc, Zstd

from dpc_radar import DpcRadarError
from dpc_radar.arco import (
    DATASETS,
    KEY_ENV,
    USERNAME_ENV,
    ArcoClient,
    chunk_range,
    dataset,
    decode_frame,
    index_range,
    parse_store,
)
from dpc_radar.products import SRI
from tests.conftest import TMERC

SHAPE = (20, 20)
#: A small store: 4 frames in an object, 5 time steps in a piece of the axis.
SHARD, PIECE = 4, 5
T0 = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
#: Ten steps on the axis, the first seven written.
TIMES = [T0 + k * STEP for k in range(10)]
LAST = TIMES[6]
WKT = __import__("rasterio").crs.CRS.from_string(TMERC).to_wkt()
CRS_ATTRS = {"GeoTransform": "-10000.0 1000.0 0.0 10000.0 0.0 -1000.0", "crs_wkt": WKT}
CENTRE = [(12.5, 42.0)]
AUTH = "Basic " + base64.b64encode(b"ada:secret").decode()


def v3(
    variable: str = "vmi", *, last: str | None = "2026-10-10T03:30", **codec: Any
) -> dict[str, Any]:
    """The ``zarr.json`` of a sharded store, as the hub writes it."""
    sharding = {
        "chunk_shape": [1, *SHAPE],
        "codecs": [{"name": "bytes"}, {"name": "zstd"}],
        "index_codecs": [{"name": "bytes"}, {"name": "crc32c"}],
        "index_location": "end",
        **codec,
    }
    return {
        "attributes": {} if last is None else {"last_valid": last},
        "consolidated_metadata": {
            "metadata": {
                variable: {
                    "shape": [len(TIMES), *SHAPE],
                    "data_type": "float32",
                    "chunk_grid": {"configuration": {"chunk_shape": [SHARD, *SHAPE]}},
                    "codecs": [{"name": "sharding_indexed", "configuration": sharding}],
                },
                "time": {"chunk_grid": {"configuration": {"chunk_shape": [PIECE]}}},
                "crs": {"attributes": CRS_ATTRS},
            }
        },
    }


def v2(*, chunks: int = 1) -> dict[str, Any]:
    """The ``.zmetadata`` of the SRI store: one object per frame."""
    return {
        "metadata": {
            ".zattrs": {"last_valid": "2026-10-10T03:30"},
            "RR/.zarray": {
                "shape": [len(TIMES), *SHAPE],
                "chunks": [chunks, *SHAPE],
                "dtype": "<f4",
                "compressor": {"id": "blosc"},
            },
            "time/.zarray": {"chunks": [len(TIMES)]},
            "crs/.zattrs": CRS_ATTRS,
        }
    }


def values(value: float) -> np.ndarray:
    """A frame of one value, with no data on the left and no echo on the right."""
    data = np.full(SHAPE, value, dtype="<f4")
    data[:, :5] = -9999.0
    data[:, 15:] = -9998.0
    return data


def shard(frames: dict[int, np.ndarray]) -> bytes:
    """A sharded object: the compressed frames, then where each one is."""
    body, index = b"", b""
    for inner in range(SHARD):
        if inner in frames:
            raw = Zstd().encode(frames[inner].tobytes())
            index += struct.pack("<QQ", len(body), len(raw))
            body += raw
        else:
            index += struct.pack("<QQ", 2**64 - 1, 2**64 - 1)
    return body + index + b"\0\0\0\0"


def axis(times: list[datetime], piece: int) -> list[bytes]:
    """The pieces of a time axis: the last one padded to the length of the others."""
    ns = [int(t.timestamp()) * 1_000_000_000 for t in times]
    ns += [-(2**63)] * (-len(ns) % piece)
    return [
        bytes(Blosc(cname="zstd").encode(np.array(ns[k : k + piece], dtype="<i8").tobytes()))
        for k in range(0, len(ns), piece)
    ]


def sharded_hub(variable: str = "vmi", name: str = "italian-radar-dpc-vmi") -> dict[str, bytes]:
    """The objects of a sharded store: frames 0-2 and 5-6 written, 3 lost, shard 2 absent."""
    root = f"/S3/{name}.zarr"
    objects = {f"{root}/time/c/{k}": piece for k, piece in enumerate(axis(TIMES, PIECE))}
    objects[f"{root}/{variable}/c/0/0/0"] = shard({0: values(10), 1: values(20), 2: values(30)})
    objects[f"{root}/{variable}/c/1/0/0"] = shard({1: values(40), 2: values(50)})
    return objects


def hub(
    objects: dict[str, bytes],
    meta: dict[str, Any],
    *,
    fail_first: int = 0,
    ignore_range: bool = False,
) -> tuple[httpx.MockTransport, Counter[str]]:
    """A fake hub: JSON for the metadata, bytes for the objects, ranges honoured."""
    calls: Counter[str] = Counter()

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == AUTH
        assert "secret" not in str(request.url)
        path = request.url.path
        wanted = request.headers.get("range")
        calls[path + (" index" if wanted and wanted.startswith("bytes=-") else "")] += 1
        if sum(calls.values()) <= fail_first:
            return httpx.Response(503)
        file = path.rsplit("/", 1)[-1]
        if file in meta:
            return httpx.Response(200, json=meta[file])
        if path not in objects:
            return httpx.Response(404)
        body = objects[path]
        if wanted is None or ignore_range:
            return httpx.Response(200, content=body)
        first, _, last = wanted.removeprefix("bytes=").partition("-")
        part = body[-int(last) :] if first == "" else body[int(first) : int(last) + 1]
        return httpx.Response(206, content=part)

    return httpx.MockTransport(handle), calls


def client(transport: httpx.MockTransport, **kwargs: Any) -> ArcoClient:
    async def no_sleep(_: float) -> None:
        return None

    kwargs = {"username": "ada", "key": "secret", "sleep": no_sleep, **kwargs}
    return ArcoClient(httpx.AsyncClient(transport=transport), **kwargs)


def test_the_datasets_by_name() -> None:
    assert dataset("vmi") is DATASETS["VMI"] and dataset(DATASETS["ETM"]).variable == "etm"
    assert DATASETS["SRI"].product is SRI and DATASETS["CAPPI8"].store.endswith("cappi8")
    assert DATASETS["OPERA"].no_echo == -math.inf and DATASETS["VIL"].no_echo == 0
    with pytest.raises(DpcRadarError, match="unknown ARCO dataset 'CUM3'"):
        dataset("CUM3")


def test_a_sharded_store_is_described() -> None:
    store = parse_store(v3(), "VMI")
    assert store.sharded and store.shard == SHARD and store.shape == SHAPE
    assert store.length == 10 and store.last_valid == LAST
    assert store.frame_key(6) == "vmi/c/1/0/0" and store.time_keys() == ["time/c/0", "time/c/1"]
    assert store.transform.a == 1000 and store.transform.f == 10_000
    assert index_range(store) == "bytes=-68"


def test_a_store_with_one_object_per_frame_is_described() -> None:
    store = parse_store(v2(), "SRI")
    assert not store.sharded and store.frame_key(6) == "RR/6.0.0"
    assert store.time_keys() == ["time/0"] and store.last_valid == LAST


@pytest.mark.parametrize("last", [None, "yesterday", 7])
def test_a_store_that_does_not_say_its_last_frame(last: Any) -> None:
    payload = v3()
    payload["attributes"] = {"last_valid": last}
    assert parse_store(payload, "VMI").last_valid is None


@pytest.mark.parametrize(
    "payload",
    [
        v3(index_location="start"),
        v3(codecs=[{"name": "bytes"}, {"name": "blosc"}]),
        v2(chunks=2),
        {},
    ],
)
def test_a_layout_it_does_not_read_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(DpcRadarError, match="a store layout this package does not read"):
        parse_store(payload, "SRI" if "metadata" in payload else "VMI")


def test_where_a_frame_is_inside_its_object() -> None:
    store = parse_store(v3(), "VMI")
    index = shard({1: values(1)})[-68:]
    assert chunk_range(store, index, 5) is not None and chunk_range(store, index, 4) is None
    with pytest.raises(DpcRadarError, match="a shard index of 3 bytes"):
        chunk_range(store, b"abc", 0)


def test_what_the_values_of_a_frame_mean() -> None:
    raw = Zstd().encode(values(30).tobytes())
    echo = decode_frame(parse_store(v3(), "VMI"), raw)
    assert np.isnan(echo[0, 0]) and echo[0, 10] == 30 and echo[0, 19] == -math.inf
    top = decode_frame(parse_store(v3("etm"), "ETM"), raw)
    assert np.isnan(top[0, 0]) and top[0, 19] == 0  # no echo top: nothing there
    with pytest.raises(DpcRadarError, match="not a readable frame"):
        decode_frame(parse_store(v3(), "VMI"), b"not zstd")


def test_no_account_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(USERNAME_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(DpcRadarError, match="needs an account"):
        ArcoClient()


async def test_the_account_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "ada")
    monkeypatch.setenv(KEY_ENV, "secret")
    transport, _ = hub(sharded_hub(), {"zarr.json": v3()})
    async with ArcoClient(httpx.AsyncClient(transport=transport)) as arco:
        assert await arco.last("VMI") == LAST


async def test_a_frame_of_a_sharded_store() -> None:
    transport, calls = hub(sharded_hub(), {"zarr.json": v3()})
    arco = client(transport)
    grid = await arco.frame("VMI", TIMES[1])
    assert grid is not None and grid.product.name == "VMI" and grid.time == TIMES[1]
    assert grid.sample(CENTRE) == [20.0] and grid.crs.to_wkt() == WKT
    assert await arco.frame("VMI", TIMES[3]) is None  # lost inside its object
    assert await arco.frame("VMI", TIMES[9]) is None  # its object is not there yet
    assert await arco.frame("VMI", T0 + timedelta(minutes=2)) is None  # not on the axis
    assert await arco.frame("VMI", TIMES[-1] + STEP) is None  # after the axis
    assert calls["/S3/italian-radar-dpc-vmi.zarr/zarr.json"] == 1  # the description is kept
    with pytest.raises(DpcRadarError, match="naive datetime"):
        await arco.frame("VMI", datetime(2026, 10, 10, 3))


async def test_the_frames_of_a_span() -> None:
    transport, calls = hub(sharded_hub(), {"zarr.json": v3()})
    arco = client(transport)
    grids = await arco.frames("VMI", T0, TIMES[9])
    assert [g.time for g in grids] == [TIMES[k] for k in (0, 1, 2, 5, 6)]  # 3 and 4 are lost
    assert [g.sample(CENTRE)[0] for g in grids] == [10, 20, 30, 40, 50]
    # the index of an object is read once for all its frames
    assert calls["/S3/italian-radar-dpc-vmi.zarr/vmi/c/0/0/0 index"] == 1
    assert await arco.times("VMI", T0, TIMES[9]) == TIMES[:7]  # nothing after the last frame
    assert await arco.times("VMI", TIMES[8], TIMES[9]) == []
    with pytest.raises(DpcRadarError, match=r"7 frames of VMI .* over the limit of 3"):
        await arco.frames("VMI", T0, TIMES[9], limit=3)


async def test_the_last_frame_is_asked_again_every_time() -> None:
    meta = {"zarr.json": v3()}
    transport, calls = hub(sharded_hub(), meta)
    arco = client(transport)
    assert await arco.last("VMI") == LAST
    meta["zarr.json"] = v3(last="2026-10-10T03:45")
    assert await arco.last("VMI") == TIMES[9]
    assert calls["/S3/italian-radar-dpc-vmi.zarr/zarr.json"] == 2
    assert await arco.times("VMI", TIMES[8], TIMES[9]) == TIMES[8:]


async def test_a_store_without_a_last_frame() -> None:
    transport, _ = hub(sharded_hub(), {"zarr.json": v3(last=None)})
    arco = client(transport)
    assert await arco.times("VMI", T0, TIMES[9]) == TIMES  # the whole axis: nothing to stop at
    with pytest.raises(DpcRadarError, match="does not say which its last frame is"):
        await arco.last("VMI")


async def test_a_frame_of_the_sri_store() -> None:
    root = "/S3/italian-radar-dpc-sri.zarr"
    objects = {f"{root}/time/0": axis(TIMES, len(TIMES))[0]}
    objects[f"{root}/RR/2.0.0"] = bytes(Blosc(cname="zstd").encode(values(7.5).tobytes()))
    transport, calls = hub(objects, {".zmetadata": v2()})
    arco = client(transport)
    grid = await arco.frame("SRI", TIMES[2])
    assert grid is not None and grid.product is SRI and grid.sample(CENTRE) == [7.5]
    assert await arco.frame("SRI", TIMES[3]) is None
    assert calls[f"{root}/zarr.json"] == 1  # asked first: this store is not Zarr v3


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, b"", "rejected the user name or the access key"),
        (403, b"", "no access to italian-radar-dpc-vmi: subscribe"),
        (200, b"<html>", "zarr.json is not JSON"),
        (200, b"[1]", "zarr.json is not a JSON object"),
        (404, b"", "no consolidated metadata"),
    ],
)
async def test_a_store_that_cannot_be_described(status: int, body: bytes, message: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, content=body))
    with pytest.raises(DpcRadarError, match=message):
        await client(transport).store("VMI")


async def test_a_failure_of_the_hub_is_raised() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client(transport, retries=0).store("VMI")
    objects = sharded_hub()
    transport, _ = hub(objects, {"zarr.json": v3()})

    def broken(request: httpx.Request) -> httpx.Response:
        if "/vmi/" in request.url.path or "/RR/" in request.url.path:
            return httpx.Response(500)
        return transport.handle_request(request)

    with pytest.raises(httpx.HTTPStatusError):
        await client(httpx.MockTransport(broken), retries=0).frame("VMI", TIMES[0])


async def test_a_failure_on_a_frame_of_the_sri_store_is_raised() -> None:
    root = "/S3/italian-radar-dpc-sri.zarr"
    transport, _ = hub({f"{root}/time/0": axis(TIMES, len(TIMES))[0]}, {".zmetadata": v2()})

    def broken(request: httpx.Request) -> httpx.Response:
        if "/RR/" in request.url.path:
            return httpx.Response(500)
        return transport.handle_request(request)

    with pytest.raises(httpx.HTTPStatusError):
        await client(httpx.MockTransport(broken), retries=0).frame("SRI", TIMES[0])


async def test_a_hub_that_ignores_the_range_is_refused() -> None:
    transport, _ = hub(sharded_hub(), {"zarr.json": v3()}, ignore_range=True)
    with pytest.raises(DpcRadarError, match="answered a range request with the whole object"):
        await client(transport).frame("VMI", TIMES[0])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"/S3/italian-radar-dpc-vmi.zarr/time/c/1": None}, "time/c/1 is missing"),
        ({"/S3/italian-radar-dpc-vmi.zarr/time/c/1": b"noise"}, "not a readable time axis"),
        (
            {"/S3/italian-radar-dpc-vmi.zarr/time/c/1": axis(TIMES[:2], 2)[0]},
            "a time axis of 7 steps, not 10",
        ),
    ],
)
async def test_a_time_axis_that_cannot_be_read(
    change: dict[str, bytes | None], message: str
) -> None:
    objects = sharded_hub()
    for path, body in change.items():
        if body is None:
            del objects[path]
        else:
            objects[path] = body
    transport, _ = hub(objects, {"zarr.json": v3()})
    with pytest.raises(DpcRadarError, match=message):
        await client(transport).frame("VMI", TIMES[0])


async def test_what_is_on_disk_is_not_asked_again(tmp_path: Path) -> None:
    transport, calls = hub(sharded_hub(), {"zarr.json": v3()})
    first = await client(transport, cache=tmp_path).frame("VMI", TIMES[1])
    folder = tmp_path / "italian-radar-dpc-vmi"
    assert (folder / "time-10").exists() and (folder / "vmi" / "1").exists()
    assert not list(tmp_path.rglob("*.part"))
    before = sum(calls.values())
    again = await client(transport, cache=tmp_path).frame("VMI", TIMES[1])
    assert (
        first is not None
        and again is not None
        and np.array_equal(first.data, again.data, equal_nan=True)
    )
    assert sum(calls.values()) == before + 1  # the description only: axis and frame come from disk
    # a lost frame is not written down: it is asked again, in case it was filled in the meantime
    assert await client(transport, cache=tmp_path).frame("VMI", TIMES[3]) is None
    assert not (folder / "vmi" / "3").exists()


async def test_a_busy_hub_is_asked_again() -> None:
    transport, calls = hub(sharded_hub(), {"zarr.json": v3()}, fail_first=2)
    assert await client(transport).last("VMI") == LAST
    assert calls["/S3/italian-radar-dpc-vmi.zarr/zarr.json"] == 3
    transport, _ = hub(sharded_hub(), {"zarr.json": v3()}, fail_first=2)
    with pytest.raises(httpx.HTTPStatusError):
        await client(transport, retries=1).last("VMI")


async def test_a_network_error_is_retried_then_raised() -> None:
    tries = 0

    def down(_: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await client(httpx.MockTransport(down), retries=2).store("VMI")
    assert tries == 3


async def test_your_own_http_layer() -> None:
    transport, _ = hub(sharded_hub(), {"zarr.json": v3()})
    seen: list[str] = []

    async def send(method: str, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(f"{method} {url}")
        assert kwargs["auth"] == ("ada", "secret") and kwargs["timeout"] == 60.0
        async with httpx.AsyncClient(transport=transport) as http:
            return await http.request(method, url, **kwargs)

    async with ArcoClient(username="ada", key="secret", send=send) as arco:
        grid = await arco.frame("VMI", TIMES[0])
    assert grid is not None and grid.sample(CENTRE) == [10.0]
    assert seen[0] == "GET https://api.arcodatahub.com/S3/italian-radar-dpc-vmi.zarr/zarr.json"


async def test_it_closes_only_the_client_it_opened() -> None:
    async with ArcoClient(username="ada", key="secret") as own:
        opened = own._client
    assert opened is not None and opened.is_closed
    transport, _ = hub(sharded_hub(), {"zarr.json": v3()})
    yours = httpx.AsyncClient(transport=transport)
    async with ArcoClient(yours, username="ada", key="secret"):
        pass
    assert not yours.is_closed
    await yours.aclose()
