"""dpc-radar: the Italian Civil Protection (DPC) open radar data, from Python."""

import logging
from importlib.metadata import version

from dpc_radar.bias import BiasMap, bias_map, correct, radar_factor
from dpc_radar.client import ABSENT, Absent, RadarClient
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import FrameCheck, RainGrid
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
    "DpcRadarError",
    "FrameCheck",
    "LastProduct",
    "Product",
    "Quality",
    "RadarClient",
    "RadarSite",
    "RainGrid",
    "Reliability",
    "ReliabilityMap",
    "__version__",
    "bias_map",
    "correct",
    "is_expired",
    "parse_download",
    "parse_last_product",
    "product",
    "radar_factor",
    "radar_total",
    "reliability",
    "reliability_map",
]
