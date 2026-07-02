from __future__ import annotations

import argparse

from gametrak.record import (
    DEFAULT_SECONDS,
    SCHEMA_NAME,
    malformed_report_record,
    metadata_record,
    parse_args,
    report_record,
    summary_record,
)


def test_parse_args_defaults_to_ten_second_jsonl_capture() -> None:
    args = parse_args([])

    assert args.seconds == DEFAULT_SECONDS
    assert args.out is None
    assert args.open_mode == "vidpid"
    assert args.no_ps2_init is False


def test_metadata_record_documents_schema_and_axis_order() -> None:
    args = argparse.Namespace(
        seconds=10.0,
        label="official",
        note=["short published sample"],
        open_mode="vidpid",
        read_size=16,
        read_timeout_ms=50,
        no_ps2_init=False,
    )

    row = metadata_record(selected=None, args=args, wall_time_ns=123, monotonic_start_ns=456)

    assert row["type"] == "metadata"
    assert row["schema"] == SCHEMA_NAME
    assert row["tool"] == "gametrak-record"
    assert row["label"] == "official"
    assert row["notes"] == ["short published sample"]
    assert row["device"] is None
    assert row["hid_axis_order"] == ["x", "y", "z", "rx", "ry", "rz"]
    assert row["semantic_axis_order"] == ["left_x", "left_y", "left_r", "right_x", "right_y", "right_r"]


def test_report_record_preserves_raw_bytes_and_semantic_values() -> None:
    report = bytes.fromhex("1a00380cff0fc708a900ff0f0201aabb")

    row = report_record(index=7, monotonic_start_ns=1_000, monotonic_now_ns=1_250, report=report)

    assert row["type"] == "report"
    assert row["index"] == 7
    assert row["elapsed_ns"] == 250
    assert row["length"] == 16
    assert row["raw_bytes_hex"] == report.hex()
    assert row["raw"] == [26, 3128, 4095, 2247, 169, 4095]
    assert row["hid_axes"] == {"x": 26, "y": 3128, "z": 4095, "rx": 2247, "ry": 169, "rz": 4095}
    assert row["semantic_raw"] == {
        "left_x": 26,
        "left_y": 3128,
        "left_r": 4095,
        "right_x": 2247,
        "right_y": 169,
        "right_r": 4095,
    }
    assert row["hat"] == 2
    assert row["buttons"] == 16
    assert row["unknown_tail_hex"] == "aabb"
    assert row["descriptor_range_ok"] is True


def test_malformed_report_record_keeps_bytes_and_error() -> None:
    row = malformed_report_record(
        index=3,
        monotonic_start_ns=100,
        monotonic_now_ns=130,
        report=b"\x01\x02",
        error=ValueError("too short"),
    )

    assert row == {
        "type": "malformed_report",
        "index": 3,
        "time_ns": 130,
        "elapsed_ns": 30,
        "length": 2,
        "raw_bytes_hex": "0102",
        "error": "ValueError: too short",
    }


def test_summary_record_reports_rate() -> None:
    row = summary_record(
        reports=30,
        valid_reports=29,
        malformed_reports=1,
        monotonic_start_ns=1_000_000_000,
        monotonic_end_ns=2_000_000_000,
    )

    assert row["type"] == "summary"
    assert row["reports"] == 30
    assert row["valid_reports"] == 29
    assert row["malformed_reports"] == 1
    assert row["elapsed_ns"] == 1_000_000_000
    assert row["mean_report_rate_hz"] == 30.0
