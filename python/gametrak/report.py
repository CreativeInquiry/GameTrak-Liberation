"""Decode GameTrak HID input reports.

The first six 16-bit little-endian fields are descriptor-derived but still
awaiting live movement validation for physical left/right semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from gametrak.constants import AXIS_LOGICAL_MAX, AXIS_LOGICAL_MIN, MIN_INPUT_REPORT_SIZE

AXIS_NAMES = ("x", "y", "z", "rx", "ry", "rz")


@dataclass(frozen=True)
class GameTrakRawReport:
    x: int
    y: int
    z: int
    rx: int
    ry: int
    rz: int
    hat: int | None
    buttons: int | None
    unknown_tail: bytes
    raw: bytes

    @property
    def axes(self) -> tuple[int, int, int, int, int, int]:
        return (self.x, self.y, self.z, self.rx, self.ry, self.rz)

    @property
    def axis_dict(self) -> dict[str, int]:
        return dict(zip(AXIS_NAMES, self.axes, strict=True))


def decode_report(report: bytes | bytearray | memoryview) -> GameTrakRawReport:
    """Decode a raw HID input report using the current protocol hypothesis."""

    raw = bytes(report)
    if len(raw) < MIN_INPUT_REPORT_SIZE:
        raise ValueError(
            f"GameTrak report must contain at least {MIN_INPUT_REPORT_SIZE} bytes; "
            f"got {len(raw)}"
        )

    x, y, z, rx, ry, rz = struct.unpack_from("<6H", raw, 0)

    hat = None
    buttons = None
    unknown_tail = raw[MIN_INPUT_REPORT_SIZE:]
    if len(raw) >= 14:
        tail_bits = int.from_bytes(raw[12:14], "little")
        hat = tail_bits & 0x0F
        buttons = (tail_bits >> 4) & 0x0FFF
        unknown_tail = raw[14:]

    return GameTrakRawReport(
        x=x,
        y=y,
        z=z,
        rx=rx,
        ry=ry,
        rz=rz,
        hat=hat,
        buttons=buttons,
        unknown_tail=unknown_tail,
        raw=raw,
    )


def values_are_in_descriptor_range(report: GameTrakRawReport) -> bool:
    """Return True when all six current axis values fit the HID descriptor range."""

    return all(AXIS_LOGICAL_MIN <= value <= AXIS_LOGICAL_MAX for value in report.axes)
