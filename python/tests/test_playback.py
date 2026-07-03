from __future__ import annotations

from io import StringIO
import json

import pytest

from gametrak.playback import load_samples, parse_args, run, sample_offset_seconds


VALID_REPORT_HEX = "00080008ff0fff0f0000000000000000"
INVALID_REPORT_HEX = "47616d657472616bda02d40f00005100"


def write_jsonl(path, rows) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def report_row(index: int, elapsed_ns: int, raw_bytes_hex: str = VALID_REPORT_HEX) -> dict:
    return {
        "type": "report",
        "index": index,
        "elapsed_ns": elapsed_ns,
        "raw_bytes_hex": raw_bytes_hex,
    }


def test_playback_cli_requires_one_protocol(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"

    with pytest.raises(SystemExit):
        parse_args([str(capture)])


def test_playback_cli_accepts_stdout_loop_and_rate(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"

    args = parse_args([str(capture), "--stdout", "--loop", "--rate", "30"])

    assert args.protocol == "stdout"
    assert args.loop
    assert args.rate == 30


def test_load_samples_skips_metadata_and_out_of_range_reports(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"
    write_jsonl(
        capture,
        [
            {"type": "metadata", "schema": "gametrak-record-jsonl-v1"},
            report_row(1, 1_000_000, INVALID_REPORT_HEX),
            report_row(2, 2_000_000),
        ],
    )

    samples = load_samples(capture)

    assert len(samples) == 1
    assert samples[0].index == 2
    assert samples[0].elapsed_ns == 2_000_000


def test_sample_offset_uses_recorded_timing_and_speed(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"
    write_jsonl(capture, [report_row(1, 10_000_000), report_row(2, 30_000_000)])
    samples = load_samples(capture)

    offset = sample_offset_seconds(
        samples[1],
        position=1,
        first_elapsed_ns=samples[0].elapsed_ns,
        rate=None,
        speed=2.0,
    )

    assert offset == pytest.approx(0.01)


def test_sample_offset_can_use_fixed_rate(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"
    write_jsonl(capture, [report_row(1, 10_000_000), report_row(2, 99_000_000)])
    samples = load_samples(capture)

    offset = sample_offset_seconds(
        samples[1],
        position=1,
        first_elapsed_ns=samples[0].elapsed_ns,
        rate=50.0,
        speed=1.0,
    )

    assert offset == pytest.approx(0.02)


def test_run_stdout_replays_jsonl_lines(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"
    write_jsonl(capture, [report_row(1, 0), report_row(2, 1)])
    args = parse_args([str(capture), "--stdout", "--rate", "100000"])
    stdout = StringIO()
    stderr = StringIO()

    result = run(args, stdout=stdout, stderr=stderr)

    assert result == 0
    assert stdout.getvalue().splitlines() == [
        "2048 2048 4095 4095 0 0",
        "2048 2048 4095 4095 0 0",
    ]
    assert stderr.getvalue() == ""
