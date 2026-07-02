from __future__ import annotations

from gametrak.report import decode_report
from gametrak.stdout import format_stdout_line, parse_args


def test_stdout_cli_defaults_to_raw_and_unthrottled() -> None:
    args = parse_args([])

    assert args.mode == "raw"
    assert args.rate == 0.0


def test_stdout_cli_accepts_norm_and_rate() -> None:
    args = parse_args(["--norm", "--rate", "30"])

    assert args.mode == "norm"
    assert args.rate == 30


def test_stdout_cli_accepts_hex_mode() -> None:
    args = parse_args(["--hex"])

    assert args.mode == "hex"


def test_stdout_cli_accepts_zero_padded_decimal_mode() -> None:
    args = parse_args(["--0dec"])

    assert args.mode == "0dec"


def test_stdout_cli_accepts_diagnose() -> None:
    args = parse_args(["--diagnose"])

    assert args.diagnose


def test_format_stdout_line_prints_six_raw_semantic_values() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    line = format_stdout_line(
        report,
        mode="raw",
        precision=3,
    )

    assert line == "2048 2048 4095 4095 0 0"


def test_format_stdout_line_prints_six_normalized_values() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    line = format_stdout_line(
        report,
        mode="norm",
        precision=3,
    )

    assert line == "0.000 0.000 0.000 1.000 -1.000 1.000"


def test_format_stdout_line_prints_zero_padded_uppercase_hex_values() -> None:
    report = decode_report(bytes.fromhex("1a00380cfd0fc708a900ff0f00000000"))

    line = format_stdout_line(
        report,
        mode="hex",
        precision=3,
    )

    assert line == "01A C38 FFD 8C7 0A9 FFF"


def test_format_stdout_line_prints_zero_padded_decimal_values() -> None:
    report = decode_report(bytes.fromhex("1a00380cfd0fc708a900ff0f00000000"))

    line = format_stdout_line(
        report,
        mode="0dec",
        precision=3,
    )

    assert line == "0026 3128 4093 2247 0169 4095"
