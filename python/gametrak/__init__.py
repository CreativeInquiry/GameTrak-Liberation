"""GameTrak HID/OSC support."""

from gametrak.constants import GAMETRAK_PID, GAMETRAK_VID
from gametrak.report import AXIS_NAMES, GameTrakRawReport, decode_report
from gametrak.values import SEMANTIC_AXIS_NAMES, normalized_tuple, raw_tuple

__all__ = [
    "AXIS_NAMES",
    "GAMETRAK_PID",
    "GAMETRAK_VID",
    "GameTrakRawReport",
    "SEMANTIC_AXIS_NAMES",
    "decode_report",
    "normalized_tuple",
    "raw_tuple",
]
