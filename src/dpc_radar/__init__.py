"""dpc-radar: the Italian Civil Protection (DPC) open radar data, from Python."""

import logging
from importlib.metadata import version

from dpc_radar.bias import BiasMap, bias_map, correct, radar_factor
from dpc_radar.catalogue import Event, rare_events, with_merging
from dpc_radar.client import ABSENT, Absent, RadarClient
from dpc_radar.climatology import (
    Climatology,
    Level,
    Rarity,
    climatology,
    rarity,
    return_level,
    return_period,
)
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import FrameCheck, RainGrid
from dpc_radar.nowcast import Forecast, Motion, chance, motion, nowcast, rain_ahead
from dpc_radar.products import (
    API_BASE,
    CUM3,
    CUM6,
    CUM12,
    CUM24,
    PRODUCTS,
    SRI,
    SRT1,
    LastProduct,
    Product,
    is_expired,
    parse_download,
    parse_last_product,
    product,
)
from dpc_radar.quality import Quality, radar_total
from dpc_radar.reliability import (
    SITES,
    RadarSite,
    Reliability,
    ReliabilityMap,
    reliability,
    reliability_map,
)
from dpc_radar.status import NetworkStatus, RadarStatus
from dpc_radar.storms import Storm, Track, storms, track

__version__ = version("dpc-radar")
logging.getLogger("dpc_radar").addHandler(logging.NullHandler())

__all__ = [
    "ABSENT",
    "API_BASE",
    "CUM3",
    "CUM6",
    "CUM12",
    "CUM24",
    "PRODUCTS",
    "SITES",
    "SRI",
    "SRT1",
    "Absent",
    "BiasMap",
    "Climatology",
    "DpcRadarError",
    "Event",
    "Forecast",
    "FrameCheck",
    "LastProduct",
    "Level",
    "Motion",
    "NetworkStatus",
    "Product",
    "Quality",
    "RadarClient",
    "RadarSite",
    "RadarStatus",
    "RainGrid",
    "Rarity",
    "Reliability",
    "ReliabilityMap",
    "Storm",
    "Track",
    "__version__",
    "bias_map",
    "chance",
    "climatology",
    "correct",
    "is_expired",
    "motion",
    "nowcast",
    "parse_download",
    "parse_last_product",
    "product",
    "radar_factor",
    "radar_total",
    "rain_ahead",
    "rare_events",
    "rarity",
    "reliability",
    "reliability_map",
    "return_level",
    "return_period",
    "storms",
    "track",
    "with_merging",
]
