"""The DPC radar archive of the ARCO Data Hub: any frame since 2010, and the last one.

The `ARCO Data Hub <https://arcodatahub.com/>`_ (Fondazione Bruno Kessler on
DPC data, for the IT4LIA AI Factory) keeps the national radar mosaics as Zarr
stores that grow every five minutes: the history the DPC API does not give
(it keeps about five months) and products it does not publish as open data.
:class:`ArcoClient` reads one frame at a time, as a
:class:`~dpc_radar.grid.RainGrid`:

================  ==========================================  ============
name              what it is                                  since
================  ==========================================  ============
``SRI``           rain intensity, mm/h                        2010
``SRT1``          rain of the last hour, mm                   July 2018
``VMI``           highest reflectivity in the column, dBZ     2018
``CAPPI2``-``8``  reflectivity at 2000 to 8000 m, dBZ         2018 to 2022
``VIL``           liquid water in the column, mm (kg/m²)      July 2018
``ETM``           height of the echo top, m                   July 2018
``OPERA``         ``VMI`` of Europe at 2 km, with the DPC's   July 2024
================  ==========================================  ============

It is **not open like the DPC API**: it needs a free account and the
subscription to each dataset, and it asks for fair use. The user name and the
access key come from ``ARCODATAHUB_USERNAME`` and ``ARCODATAHUB_ACCESS_KEY``,
or from the arguments. The data are CC BY-SA 4.0: credit «FBK in cooperation
with Dipartimento Protezione Civile Nazionale» (and OPERA for ``OPERA``).

It is read here without zarr or xarray: one request for a frame of ``SRI``
(a Zarr v2 store, one object per frame), two for the others (Zarr v3, 288
frames in one object: the index at its end, then the bytes of the frame). The
codecs come with the extra ``arco`` (``pip install "dpc-radar[arco]"``).

**What a value means.** Three states, as in the rest of the library:

* a number: measured;
* ``NaN``: not measured (outside the radars' range, or a frame with a hole),
  ``-9999`` in the store;
* *no echo*: measured, and nothing there (``-9998`` in the store). It becomes
  ``-inf`` in a reflectivity (0 dBZ is a weak echo, not an absence) and ``0``
  in a height or a quantity of water.

**The traps.**

* The time axis is not regular: ``SRI`` has a frame every 15 minutes in 2010,
  every 5 today; ``SRT1`` is hourly until July 2020. Ask :meth:`ArcoClient.times`
  which frames exist, never compute them from a step.
* Before 30 April 2026 (and not in all of 2025) *no echo* in a reflectivity is
  written as 0 dBZ, which cannot be told from a weak echo: it stays 0.
* ``VIL`` between August 2019 and October 2025 has no 0.5 bin in some frames:
  it was merged into 0 when two encodings were made one.
* ``SRT1`` here is the same frame the DPC API serves as ``SRT1``, pixel by
  pixel (checked on 30 hours of 2026).
* ``OPERA`` is on another grid (2 km, ETRS89-LAEA) and is reflectivity, not
  rain: it does not compare with ``SRI`` as it is.
* The frame of a time not yet published, or lost, is ``None``: not an error.

The client keeps the description of a store and its time axis in memory. With
``cache`` it also keeps them, and every frame it reads, in a folder: a frame on
disk is not asked again.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final

import httpx
import numpy as np
from affine import Affine
from rasterio.crs import CRS

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import RainGrid
from dpc_radar.products import NODATA, SRI, SRT1, Product

ARCO_BASE: Final = "https://api.arcodatahub.com/S3"
#: The environment variables of the account, as the hub's own guide names them.
USERNAME_ENV: Final = "ARCODATAHUB_USERNAME"
KEY_ENV: Final = "ARCODATAHUB_ACCESS_KEY"

#: In the store: measured, and no echo. Not measured is ``-9999``,
#: :data:`~dpc_radar.products.NODATA`.
NO_ECHO: Final = -9998.0
#: How many frames :meth:`ArcoClient.frames` reads in one call, unless told otherwise: a day.
MAX_FRAMES: Final = 288

Send = Callable[..., Awaitable[httpx.Response]]

_RETRY_STATUS: Final = frozenset({429, 500, 502, 503, 504})
_GONE: Final = frozenset({403, 404})  # an object the store does not have
#: A chunk a sharded object does not hold: offset and length are both this.
_EMPTY: Final = 2**64 - 1
#: How many pieces of a time axis are asked at once.
_AXIS_BATCH: Final = 8
_NS: Final = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ArcoDataset:
    """One store of the hub, and what its frames become."""

    name: str
    #: The name of the store on the hub, without ``.zarr``.
    store: str
    #: The array of the frames inside the store.
    variable: str
    #: What a :class:`~dpc_radar.grid.RainGrid` of it carries: the name and the unit.
    product: Product
    #: What *no echo* becomes: ``-inf`` in a reflectivity, 0 elsewhere.
    no_echo: float = 0.0


def _reflectivity(name: str, store: str, variable: str, what: str) -> ArcoDataset:
    return ArcoDataset(name, store, variable, Product(name, None, False, what), -math.inf)


_DPC: Final = "italian-radar-dpc"
_CAPPI: Final = tuple(
    _reflectivity(
        f"CAPPI{km}", f"{_DPC}-cappi{km}", f"cappi{km}", f"reflectivity at {km}000 m (dBZ)"
    )
    for km in range(2, 9)
)

#: Every store this module reads, by name.
DATASETS: Final = {
    d.name: d
    for d in (
        ArcoDataset("SRI", f"{_DPC}-sri", "RR", SRI),
        ArcoDataset("SRT1", f"{_DPC}-srt1-adj", "srt1_adj", SRT1),
        _reflectivity("VMI", f"{_DPC}-vmi", "vmi", "highest reflectivity in the column (dBZ)"),
        *_CAPPI,
        ArcoDataset(
            "VIL",
            f"{_DPC}-vil",
            "vil",
            Product("VIL", None, False, "liquid water in the column (mm)"),
        ),
        ArcoDataset(
            "ETM", f"{_DPC}-etm", "etm", Product("ETM", None, False, "height of the echo top (m)")
        ),
        _reflectivity(
            "OPERA",
            "opera-plus-it-vmi",
            "vmi",
            "highest reflectivity in the column, Europe at 2 km (dBZ)",
        ),
    )
}


def dataset(name: str | ArcoDataset) -> ArcoDataset:
    """The :class:`ArcoDataset` called ``name``."""
    if isinstance(name, ArcoDataset):
        return name
    try:
        return DATASETS[name.upper()]
    except KeyError:
        known = ", ".join(DATASETS)
        raise DpcRadarError(f"unknown ARCO dataset {name!r}; known: {known}") from None


@dataclass(frozen=True, slots=True)
class Store:
    """What the metadata of a store say: its grid, its layout, its last frame."""

    dataset: ArcoDataset
    #: How many time steps the axis has, the ones not yet written included.
    length: int
    #: Rows and columns of a frame.
    shape: tuple[int, int]
    #: How many frames one object holds: 1 in a Zarr v2 store, 288 in a sharded v3 one.
    shard: int
    #: How many time steps one object of the time axis holds.
    time_chunk: int
    transform: Affine
    crs: CRS
    #: The last frame written; ``None`` if the store does not say.
    last_valid: datetime | None

    @property
    def sharded(self) -> bool:
        return self.shard > 1

    def frame_key(self, i: int) -> str:
        """The object that holds frame ``i``, under the store."""
        variable = self.dataset.variable
        return f"{variable}/c/{i // self.shard}/0/0" if self.sharded else f"{variable}/{i}.0.0"

    def time_keys(self) -> list[str]:
        """The objects of the time axis, in order."""
        pieces = range(math.ceil(self.length / self.time_chunk))
        return [f"time/c/{k}" if self.sharded else f"time/{k}" for k in pieces]


def _when(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        return None


def _grid(attrs: dict[str, Any]) -> tuple[Affine, CRS]:
    """The affine transform and the projection, from the attributes of ``crs``."""
    x0, dx, _, y0, _, dy = (float(v) for v in str(attrs["GeoTransform"]).split())
    return Affine(dx, 0.0, x0, 0.0, dy, y0), CRS.from_wkt(str(attrs["crs_wkt"]))


def _v3(payload: dict[str, Any], of: ArcoDataset) -> Store:
    arrays = payload["consolidated_metadata"]["metadata"]
    frames, time = arrays[of.variable], arrays["time"]
    codec = frames["codecs"][0]
    inner = [c["name"] for c in codec["configuration"]["codecs"]]
    if (
        codec["name"] != "sharding_indexed"
        or codec["configuration"]["chunk_shape"][0] != 1
        or codec["configuration"]["index_location"] != "end"
        or inner != ["bytes", "zstd"]
        or frames["data_type"] != "float32"
    ):
        raise ValueError(f"codecs {json.dumps(frames['codecs'])[:200]}")
    length, rows, cols = (int(n) for n in frames["shape"])
    transform, crs = _grid(arrays["crs"]["attributes"])
    return Store(
        of,
        length,
        (rows, cols),
        int(frames["chunk_grid"]["configuration"]["chunk_shape"][0]),
        int(time["chunk_grid"]["configuration"]["chunk_shape"][0]),
        transform,
        crs,
        _when(payload.get("attributes", {}).get("last_valid")),
    )


def _v2(payload: dict[str, Any], of: ArcoDataset) -> Store:
    meta = payload["metadata"]
    frames, time = meta[f"{of.variable}/.zarray"], meta["time/.zarray"]
    if (
        frames["chunks"][0] != 1
        or frames["compressor"]["id"] != "blosc"
        or frames["dtype"] != "<f4"
    ):
        raise ValueError(f"layout {json.dumps(frames)[:200]}")
    length, rows, cols = (int(n) for n in frames["shape"])
    transform, crs = _grid(meta["crs/.zattrs"])
    return Store(
        of,
        length,
        (rows, cols),
        1,
        int(time["chunks"][0]),
        transform,
        crs,
        _when(meta.get(".zattrs", {}).get("last_valid")),
    )


def parse_store(payload: dict[str, Any], name: str | ArcoDataset) -> Store:
    """A :class:`Store` from the consolidated metadata of a store.

    ``payload`` is the ``zarr.json`` of a Zarr v3 store or the ``.zmetadata``
    of a v2 one. A layout this module does not read (another codec, frames
    grouped in another way) raises :class:`~dpc_radar.errors.DpcRadarError`:
    better than a frame of noise.
    """
    of = dataset(name)
    try:
        return _v3(payload, of) if "consolidated_metadata" in payload else _v2(payload, of)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DpcRadarError(
            f"{of.store}: a store layout this package does not read ({exc})"
        ) from exc


def index_range(store: Store) -> str:
    """The ``Range`` of the index at the end of a sharded object.

    Sixteen bytes per frame (where it starts, how long it is) and four of
    checksum.
    """
    return f"bytes=-{store.shard * 16 + 4}"


def chunk_range(store: Store, index: bytes, i: int) -> str | None:
    """The ``Range`` of frame ``i`` inside its object; ``None`` if the object does not hold it."""
    if len(index) != store.shard * 16 + 4:
        raise DpcRadarError(f"{store.dataset.store}: a shard index of {len(index)} bytes")
    offset, size = struct.unpack_from("<QQ", index, (i % store.shard) * 16)
    if offset == _EMPTY and size == _EMPTY:
        return None
    return f"bytes={offset}-{offset + size - 1}"


def _codec(name: str) -> Any:
    try:
        import numcodecs
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError(
            'reading the ARCO Data Hub needs the codecs: pip install "dpc-radar[arco]"'
        ) from exc
    return getattr(numcodecs, name)()


def decode_frame(store: Store, raw: bytes) -> np.ndarray:
    """The values of a frame from its compressed bytes: see the module for what they mean."""
    codec = _codec("Zstd" if store.sharded else "Blosc")
    try:
        data = np.frombuffer(codec.decode(raw), dtype="<f4").reshape(store.shape).copy()
    except Exception as exc:  # the codecs raise their own errors
        raise DpcRadarError(f"{store.dataset.store}: not a readable frame ({exc})") from exc
    empty = data == NO_ECHO
    data[data <= NODATA] = np.nan
    data[empty] = store.dataset.no_echo
    return data


def _ns(time: datetime) -> int:
    if time.tzinfo is None:
        raise DpcRadarError("naive datetime: pass an aware one (UTC)")
    return int(time.timestamp()) * _NS


def _write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.rename(dest)


class ArcoClient:
    """The radar stores of the ARCO Data Hub, a frame at a time.

    ``client``, ``send``, ``retries`` and ``backoff_s`` are those of
    :class:`~dpc_radar.client.RadarClient`: your own ``httpx.AsyncClient``, or
    your own HTTP layer, which then does the retrying. The credentials go with
    every request as HTTP Basic, never in the URL.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        username: str | None = None,
        key: str | None = None,
        send: Send | None = None,
        retries: int = 3,
        backoff_s: float = 2.0,
        timeout_s: float = 60.0,
        base: str = ARCO_BASE,
        cache: Path | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        username = username or os.environ.get(USERNAME_ENV)
        key = key or os.environ.get(KEY_ENV)
        if not username or not key:
            raise DpcRadarError(
                f"the ARCO Data Hub needs an account: set {USERNAME_ENV} and {KEY_ENV}, "
                "or pass username and key"
            )
        self._auth = (username, key)
        self._own = client is None and send is None
        self._client = client or (
            None if send is not None else httpx.AsyncClient(follow_redirects=True)
        )
        self._send_hook = send
        self._retries = retries
        self._backoff_s = backoff_s
        self._timeout_s = timeout_s
        self._base = base.rstrip("/")
        self._cache = cache
        self._sleep = sleep
        self._stores: dict[str, Store] = {}
        self._axes: dict[str, np.ndarray] = {}

    async def __aenter__(self) -> ArcoClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this instance opened it."""
        if self._own and self._client is not None:
            await self._client.aclose()

    async def store(self, name: str | ArcoDataset, *, refresh: bool = False) -> Store:
        """The description of a store; read once, and again with ``refresh``."""
        of = dataset(name)
        if refresh or of.name not in self._stores:
            self._stores[of.name] = await self._describe(of)
        return self._stores[of.name]

    async def last(self, name: str | ArcoDataset) -> datetime:
        """The time of the newest frame of a store, asked again at every call."""
        found = (await self.store(name, refresh=True)).last_valid
        if found is None:
            raise DpcRadarError(f"{dataset(name).store} does not say which its last frame is")
        return found

    async def times(
        self, name: str | ArcoDataset, start: datetime, end: datetime
    ) -> list[datetime]:
        """The times of the frames from ``start`` to ``end``, both included.

        Only frames already written: nothing after the last one the store knew
        of when this client read its description (:meth:`last` reads it again).
        """
        store = await self.store(name)
        axis = await self._axis(store)
        lo, hi = self._span(store, axis, start, end)
        return [datetime.fromtimestamp(int(ns) // _NS, tz=UTC) for ns in axis[lo:hi]]

    async def frame(self, name: str | ArcoDataset, time: datetime) -> RainGrid | None:
        """The frame of ``time`` (aware); ``None`` if the store has no frame at that time."""
        store = await self.store(name)
        axis = await self._axis(store)
        ns = _ns(time)
        i = int(np.searchsorted(axis, ns))
        if i == len(axis) or int(axis[i]) != ns:
            return None
        return await self._read(store, i, time, {})

    async def frames(
        self,
        name: str | ArcoDataset,
        start: datetime,
        end: datetime,
        *,
        limit: int = MAX_FRAMES,
    ) -> list[RainGrid]:
        """The frames from ``start`` to ``end``, both included, the oldest first.

        A frame the store lost is left out. More than ``limit`` frames raise
        before anything is downloaded: the hub asks to be told before a large
        pull, and a year asked by mistake is 100,000 frames.
        """
        store = await self.store(name)
        axis = await self._axis(store)
        lo, hi = self._span(store, axis, start, end)
        if hi - lo > limit:
            raise DpcRadarError(
                f"{hi - lo} frames of {store.dataset.name} from {start} to {end}: over the limit "
                f"of {limit}; raise it if you mean it (the hub asks for fair use)"
            )
        indexes: dict[int, bytes] = {}
        out: list[RainGrid] = []
        for i in range(lo, hi):
            when = datetime.fromtimestamp(int(axis[i]) // _NS, tz=UTC)
            grid = await self._read(store, i, when, indexes)
            if grid is not None:
                out.append(grid)
        return out

    def _span(
        self, store: Store, axis: np.ndarray, start: datetime, end: datetime
    ) -> tuple[int, int]:
        """The positions on the axis of the frames in ``[start, end]`` that are written."""
        last = _ns(end)
        if store.last_valid is not None:
            last = min(last, _ns(store.last_valid))
        lo = int(np.searchsorted(axis, _ns(start), side="left"))
        return lo, max(lo, int(np.searchsorted(axis, last, side="right")))

    async def _describe(self, of: ArcoDataset) -> Store:
        for file in ("zarr.json", ".zmetadata"):
            response = await self._get(f"{of.store}.zarr/{file}")
            if response.status_code == 404:
                continue
            if response.status_code == 401:
                raise DpcRadarError("the ARCO Data Hub rejected the user name or the access key")
            if response.status_code == 403:
                raise DpcRadarError(
                    f"no access to {of.store}: subscribe to the dataset on arcodatahub.com"
                )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise DpcRadarError(f"{of.store}: {file} is not JSON") from exc
            if not isinstance(payload, dict):
                raise DpcRadarError(f"{of.store}: {file} is not a JSON object")
            return parse_store(payload, of)
        raise DpcRadarError(f"{of.store}: no consolidated metadata on the hub")

    async def _axis(self, store: Store) -> np.ndarray:
        """The time axis of a store, in nanoseconds since 1970: memory, then disk, then the hub."""
        name = store.dataset.name
        if name in self._axes:
            return self._axes[name]
        file = self._file(store, f"time-{store.length}")
        if file is not None and file.exists():
            axis = np.frombuffer(file.read_bytes(), dtype="<i8")
        else:
            keys = store.time_keys()
            pieces: list[bytes] = []
            for k in range(0, len(keys), _AXIS_BATCH):
                pieces += await asyncio.gather(
                    *(self._object(store, key) for key in keys[k : k + _AXIS_BATCH])
                )
            codec = _codec("Blosc")
            try:
                raw = b"".join(bytes(codec.decode(p)) for p in pieces)
            except Exception as exc:  # the codecs raise their own errors
                raise DpcRadarError(f"{store.dataset.store}: not a readable time axis") from exc
            # The last piece is as long as the others: what is past the axis is padding.
            raw = raw[: store.length * 8]
            axis = np.frombuffer(raw, dtype="<i8")
            if len(axis) != store.length:
                raise DpcRadarError(
                    f"{store.dataset.store}: a time axis of {len(axis)} steps, not {store.length}"
                )
            if file is not None:
                _write(file, raw)
        self._axes[name] = axis
        return axis

    async def _object(self, store: Store, key: str) -> bytes:
        """One whole object of a store, which must be there."""
        response = await self._get(f"{store.dataset.store}.zarr/{key}")
        if response.status_code in _GONE:
            raise DpcRadarError(f"{store.dataset.store}: {key} is missing")
        response.raise_for_status()
        return response.content

    async def _read(
        self, store: Store, i: int, time: datetime, indexes: dict[int, bytes]
    ) -> RainGrid | None:
        raw = await self._raw(store, i, indexes)
        if raw is None:
            return None
        return RainGrid(
            store.dataset.product, time, decode_frame(store, raw), store.transform, store.crs
        )

    async def _raw(self, store: Store, i: int, indexes: dict[int, bytes]) -> bytes | None:
        """The compressed bytes of frame ``i``: from the cache, or from the hub."""
        file = self._file(store, f"{store.dataset.variable}/{i}")
        if file is not None and file.exists():
            return file.read_bytes()
        url = f"{store.dataset.store}.zarr/{store.frame_key(i)}"
        if store.sharded:
            shard = i // store.shard
            if shard not in indexes:
                found = await self._part(url, index_range(store))
                if found is None:
                    return None
                indexes[shard] = found
            where = chunk_range(store, indexes[shard], i)
            raw = None if where is None else await self._part(url, where)
        else:
            response = await self._get(url)
            if response.status_code in _GONE:
                return None
            response.raise_for_status()
            raw = response.content
        if raw is not None and file is not None:
            _write(file, raw)
        return raw

    async def _part(self, url: str, byte_range: str) -> bytes | None:
        """A range of an object; ``None`` if the object is not there."""
        response = await self._get(url, headers={"Range": byte_range})
        if response.status_code in _GONE:
            return None
        response.raise_for_status()
        if response.status_code != 206:
            raise DpcRadarError(
                "the hub answered a range request with the whole object "
                f"(HTTP {response.status_code})"
            )
        return response.content

    def _file(self, store: Store, name: str) -> Path | None:
        return None if self._cache is None else self._cache / store.dataset.store / name

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base}/{path}"
        kwargs = {"auth": self._auth, "timeout": self._timeout_s, **kwargs}
        if self._send_hook is not None:
            return await self._send_hook("GET", url, **kwargs)
        assert self._client is not None
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.request("GET", url, **kwargs)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            else:
                if response.status_code not in _RETRY_STATUS or attempt == self._retries:
                    return response
            await self._sleep(self._backoff_s * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "ARCO_BASE",
    "DATASETS",
    "KEY_ENV",
    "MAX_FRAMES",
    "NO_ECHO",
    "USERNAME_ENV",
    "ArcoClient",
    "ArcoDataset",
    "Store",
    "chunk_range",
    "dataset",
    "decode_frame",
    "index_range",
    "parse_store",
]
