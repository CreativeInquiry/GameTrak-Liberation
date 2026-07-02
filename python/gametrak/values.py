"""Semantic GameTrak value helpers.

This module is deliberately not a calibration layer. It only captures two
driver-level facts:

- the empirically confirmed semantic channel order;
- descriptor-scale normalization for callers that explicitly ask for it.

Application-specific calibration, deadband, smoothing, range fitting, gesture
interpretation, and alternate coordinate systems should happen downstream from
the Python HID bridge.
"""

from __future__ import annotations

from typing import Literal

from gametrak.constants import AXIS_LOGICAL_MAX, AXIS_LOGICAL_MIN
from gametrak.report import GameTrakRawReport

SemanticAxisName = Literal["left_x", "left_y", "left_r", "right_x", "right_y", "right_r"]

SEMANTIC_AXIS_NAMES: tuple[SemanticAxisName, ...] = (
    "left_x",
    "left_y",
    "left_r",
    "right_x",
    "right_y",
    "right_r",
)


def raw_values(report: GameTrakRawReport) -> dict[SemanticAxisName, int]:
    """Return raw HID values in semantic left/right order."""

    return {
        "left_x": report.x,
        "left_y": report.y,
        "left_r": report.z,
        "right_x": report.rx,
        "right_y": report.ry,
        "right_r": report.rz,
    }


def raw_tuple(report: GameTrakRawReport) -> tuple[int, int, int, int, int, int]:
    """Return raw HID values in the public six-channel order."""

    values = raw_values(report)
    return tuple(values[name] for name in SEMANTIC_AXIS_NAMES)


def normalized_values(report: GameTrakRawReport) -> dict[SemanticAxisName, float]:
    """Return descriptor-normalized values in semantic left/right order.

    Joystick X/Y values use the descriptor midpoint to map ``0..4095`` to
    roughly ``-1..1``. Tether R values are expressed as simple extension:
    ``0`` is retracted/short and ``1`` is extended. These are convenience
    transforms, not per-device calibration.
    """

    return {
        "left_x": _normalize_centered(report.x),
        "left_y": _normalize_centered(report.y),
        "left_r": _normalize_tether_extension(report.z),
        "right_x": _normalize_centered(report.rx),
        "right_y": _normalize_centered(report.ry),
        "right_r": _normalize_tether_extension(report.rz),
    }


def normalized_tuple(report: GameTrakRawReport) -> tuple[float, float, float, float, float, float]:
    """Return descriptor-normalized values in the public six-channel order."""

    values = normalized_values(report)
    return tuple(values[name] for name in SEMANTIC_AXIS_NAMES)


def _normalize_centered(raw_value: int) -> float:
    """Map descriptor-range joystick values to ``-1..1`` with 2048 as zero."""

    center = 2048
    if raw_value >= center:
        return _clamp((raw_value - center) / (AXIS_LOGICAL_MAX - center), -1.0, 1.0)
    return _clamp((raw_value - center) / (center - AXIS_LOGICAL_MIN), -1.0, 1.0)


def _normalize_tether_extension(raw_value: int) -> float:
    """Map descriptor-range tether values to extension, not sensor voltage."""

    span = AXIS_LOGICAL_MAX - AXIS_LOGICAL_MIN
    sensor_fraction = (raw_value - AXIS_LOGICAL_MIN) / span
    return _clamp(1.0 - sensor_fraction, 0.0, 1.0)


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp a float into an inclusive range."""

    return min(max(value, low), high)
