from __future__ import annotations

from gametrak.report import decode_report
from gametrak.values import SEMANTIC_AXIS_NAMES, normalized_values, raw_tuple, raw_values


def test_raw_values_map_hid_axes_to_semantic_order() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    assert raw_values(report) == {
        "left_x": 2048,
        "left_y": 2048,
        "left_r": 4095,
        "right_x": 4095,
        "right_y": 0,
        "right_r": 0,
    }
    assert raw_tuple(report) == (2048, 2048, 4095, 4095, 0, 0)
    assert SEMANTIC_AXIS_NAMES == (
        "left_x",
        "left_y",
        "left_r",
        "right_x",
        "right_y",
        "right_r",
    )


def test_normalized_values_are_descriptor_scale_not_calibration() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    values = normalized_values(report)

    assert round(values["left_x"], 3) == 0.0
    assert round(values["left_y"], 3) == 0.0
    assert values["left_r"] == 0.0
    assert values["right_x"] == 1.0
    assert values["right_y"] == -1.0
    assert values["right_r"] == 1.0
