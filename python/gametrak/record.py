"""Record GameTrak HID input reports as newline-delimited JSON.

``gametrak-record`` is meant for repeatable protocol captures and publishable
sample data. It records the full HID report bytes plus the current decoded
axis hypothesis, so future parser changes can be checked against old captures.

The output format is JSONL rather than one large JSON array because HID data is
inherently a stream. Each line is valid JSON on its own, partial captures remain
usable after an interrupted run, and command-line tools such as ``tail`` and
``grep`` can inspect the file while recording is still in progress.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import time
from typing import Any, TextIO

from gametrak.constants import EXPECTED_INPUT_REPORT_SIZE, GAMETRAK_PID, GAMETRAK_VID
from gametrak.diagnose import diagnostic_report
from gametrak.hid_backend import HidDeviceInfo, enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import AXIS_NAMES, decode_report, values_are_in_descriptor_range
from gametrak.values import SEMANTIC_AXIS_NAMES, raw_values

SCHEMA_NAME = "gametrak-record-jsonl-v1"
DEFAULT_SECONDS = 10.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the JSONL recorder."""

    parser = argparse.ArgumentParser(
        description="Record raw GameTrak HID input reports as JSONL.",
        epilog=(
            "When --out is omitted, stdout contains JSONL only. Diagnostics "
            "are written to stderr so captures can be redirected or piped."
        ),
    )
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="capture duration in seconds")
    parser.add_argument("--out", type=Path, default=None, help="optional JSONL output path; default is stdout")
    parser.add_argument("--label", default="", help="optional short label stored in the metadata row")
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="optional capture note; may be repeated",
    )
    parser.add_argument("--print", action="store_true", help="print compact decoded reports to stderr while recording")
    parser.add_argument("--diagnose", action="store_true", help="print a GameTrak HID/USB diagnostic report and exit")
    parser.add_argument(
        "--open-mode",
        choices=("auto", "path", "vidpid"),
        default="vidpid",
        help="how to open the device; vidpid matches the working libgametrak path",
    )
    parser.add_argument("--path", default=None, help="optional hidapi path to open")
    parser.add_argument("--read-timeout-ms", type=int, default=50, help="hidapi timed-read timeout")
    parser.add_argument("--read-size", type=int, default=EXPECTED_INPUT_REPORT_SIZE)
    parser.add_argument("--no-ps2-init", action="store_true", help="do not send the libgametrak ps2 init sequence")
    return parser.parse_args(argv)


def metadata_record(
    *,
    selected: HidDeviceInfo | None,
    args: argparse.Namespace,
    wall_time_ns: int,
    monotonic_start_ns: int,
) -> dict[str, Any]:
    """Return the first JSONL row describing this capture session."""

    return {
        "type": "metadata",
        "schema": SCHEMA_NAME,
        "tool": "gametrak-record",
        "created_unix_ns": wall_time_ns,
        "monotonic_start_ns": monotonic_start_ns,
        "seconds_requested": args.seconds,
        "label": args.label,
        "notes": list(args.note),
        "device": _device_info_record(selected),
        "open_mode": args.open_mode,
        "read_size": args.read_size,
        "read_timeout_ms": args.read_timeout_ms,
        "ps2_init": not args.no_ps2_init,
        "hid_axis_order": list(AXIS_NAMES),
        "semantic_axis_order": list(SEMANTIC_AXIS_NAMES),
    }


def report_record(*, index: int, monotonic_start_ns: int, monotonic_now_ns: int, report: bytes) -> dict[str, Any]:
    """Return one decoded HID report row.

    The row intentionally stores both semantic values and the lower-level HID
    descriptor axis names. The semantic order is the public API; the HID names
    help preserve evidence for future protocol work.
    """

    decoded = decode_report(report)
    return {
        "type": "report",
        "index": index,
        "time_ns": monotonic_now_ns,
        "elapsed_ns": monotonic_now_ns - monotonic_start_ns,
        "length": len(report),
        "raw_bytes_hex": decoded.raw.hex(),
        "hid_axes": decoded.axis_dict,
        "raw": list(decoded.axes),
        "semantic_raw": raw_values(decoded),
        "hat": decoded.hat,
        "buttons": decoded.buttons,
        "unknown_tail_hex": decoded.unknown_tail.hex(),
        "descriptor_range_ok": values_are_in_descriptor_range(decoded),
    }


def malformed_report_record(
    *,
    index: int,
    monotonic_start_ns: int,
    monotonic_now_ns: int,
    report: bytes,
    error: Exception,
) -> dict[str, Any]:
    """Return a JSONL row for a short or otherwise undecodable report."""

    return {
        "type": "malformed_report",
        "index": index,
        "time_ns": monotonic_now_ns,
        "elapsed_ns": monotonic_now_ns - monotonic_start_ns,
        "length": len(report),
        "raw_bytes_hex": report.hex(),
        "error": f"{type(error).__name__}: {error}",
    }


def summary_record(
    *,
    reports: int,
    valid_reports: int,
    malformed_reports: int,
    monotonic_start_ns: int,
    monotonic_end_ns: int,
) -> dict[str, Any]:
    """Return the final JSONL row summarizing the capture."""

    elapsed_ns = max(monotonic_end_ns - monotonic_start_ns, 0)
    elapsed_seconds = elapsed_ns / 1_000_000_000
    return {
        "type": "summary",
        "reports": reports,
        "valid_reports": valid_reports,
        "malformed_reports": malformed_reports,
        "elapsed_ns": elapsed_ns,
        "mean_report_rate_hz": (reports / elapsed_seconds) if elapsed_seconds else 0.0,
    }


def run(args: argparse.Namespace, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run the recorder until the requested duration elapses or Ctrl-C occurs."""

    if args.diagnose:
        print(diagnostic_report(open_mode=args.open_mode, path=args.path), file=stdout, end="")
        return 0

    if args.seconds <= 0:
        print("gametrak-record error: --seconds must be greater than 0", file=stderr)
        return 2

    try:
        candidates = enumerate_gametrak_devices()
    except Exception as exc:
        print(f"gametrak-record error: failed to enumerate HID devices: {exc}", file=stderr)
        return 2

    selected = select_gametrak_device(candidates)
    selected_path = _selected_path(args, selected)
    device = None

    output_context = _output_context(args.out, stdout)
    try:
        with output_context as output:
            device = open_gametrak(selected_path, use_vid_pid=args.open_mode == "vidpid")
            device.set_nonblocking(False)
            monotonic_start_ns = time.monotonic_ns()
            _write_json_line(
                output,
                metadata_record(
                    selected=selected,
                    args=args,
                    wall_time_ns=time.time_ns(),
                    monotonic_start_ns=monotonic_start_ns,
                ),
            )
            print(
                (
                    f"gametrak-record opened VID 0x{GAMETRAK_VID:04X} PID 0x{GAMETRAK_PID:04X}; "
                    f"recording for {args.seconds:g}s"
                ),
                file=stderr,
            )

            ps2_key = None
            if not args.no_ps2_init:
                ps2_key = send_ps2_init(device, args.read_size)

            counts = _record_device(
                args,
                device,
                output=output,
                stderr=stderr,
                monotonic_start_ns=monotonic_start_ns,
                ps2_key=ps2_key,
            )
            monotonic_end_ns = time.monotonic_ns()
            _write_json_line(
                output,
                summary_record(
                    reports=counts["reports"],
                    valid_reports=counts["valid_reports"],
                    malformed_reports=counts["malformed_reports"],
                    monotonic_start_ns=monotonic_start_ns,
                    monotonic_end_ns=monotonic_end_ns,
                ),
            )
    except KeyboardInterrupt:
        print("gametrak-record interrupted", file=stderr)
        return 130
    except Exception as exc:
        print(f"gametrak-record error: {exc}", file=stderr)
        return 2
    finally:
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    if counts["reports"] == 0:
        print("gametrak-record captured no reports", file=stderr)
        return 1

    print(
        (
            f"gametrak-record wrote {counts['reports']} reports "
            f"({counts['valid_reports']} descriptor-range ok)"
        ),
        file=stderr,
    )
    return 0


def _record_device(
    args: argparse.Namespace,
    device: object,
    *,
    output: TextIO,
    stderr: TextIO,
    monotonic_start_ns: int,
    ps2_key: int | None,
) -> dict[str, int]:
    """Capture reports from one open HID handle."""

    read_timeout_ms = max(int(args.read_timeout_ms), 0)
    deadline_ns = monotonic_start_ns + int(args.seconds * 1_000_000_000)
    report_index = 0
    valid_reports = 0
    malformed_reports = 0
    next_keepalive_report = 100
    no_report_notice_at = monotonic_start_ns + 2_000_000_000
    no_report_notice_printed = False

    while time.monotonic_ns() < deadline_ns:
        if ps2_key is not None and valid_reports >= next_keepalive_report:
            ps2_key = send_ps2_keepalive(device, ps2_key)
            next_keepalive_report += 100

        data = device.read(args.read_size, read_timeout_ms)
        now_ns = time.monotonic_ns()
        if not data:
            if not no_report_notice_printed and now_ns >= no_report_notice_at:
                print(
                    "gametrak-record notice: device is open, but no input reports have arrived yet",
                    file=stderr,
                )
                no_report_notice_printed = True
            continue

        report_index += 1
        raw_report = bytes(data)
        try:
            row = report_record(index=report_index, monotonic_start_ns=monotonic_start_ns, monotonic_now_ns=now_ns, report=raw_report)
            if row["descriptor_range_ok"]:
                valid_reports += 1
            if args.print:
                print(_compact_report_line(row), file=stderr)
        except ValueError as exc:
            malformed_reports += 1
            row = malformed_report_record(
                index=report_index,
                monotonic_start_ns=monotonic_start_ns,
                monotonic_now_ns=now_ns,
                report=raw_report,
                error=exc,
            )
            if args.print:
                print(f"#{report_index} malformed len={len(raw_report)} hex={raw_report.hex()} error={exc}", file=stderr)

        _write_json_line(output, row)

    return {
        "reports": report_index,
        "valid_reports": valid_reports,
        "malformed_reports": malformed_reports,
    }


def _output_context(path: Path | None, stdout: TextIO) -> Any:
    """Return a context manager for the chosen JSONL destination."""

    if path is None:
        return nullcontext(stdout)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def _write_json_line(output: TextIO, row: dict[str, Any]) -> None:
    """Write one compact JSON object and flush immediately."""

    output.write(json.dumps(row, separators=(",", ":")) + "\n")
    output.flush()


def _selected_path(args: argparse.Namespace, selected: HidDeviceInfo | None) -> bytes | str | None:
    """Return the hidapi path implied by the CLI open mode."""

    if args.path is not None:
        return args.path
    if args.open_mode == "vidpid":
        return None
    return selected.path if selected is not None else None


def _device_info_record(device: HidDeviceInfo | None) -> dict[str, Any] | None:
    """Serialize hidapi enumeration details into JSON-compatible fields."""

    if device is None:
        return None
    return {
        "vendor_id": device.vendor_id,
        "vendor_id_hex": f"0x{device.vendor_id:04X}",
        "product_id": device.product_id,
        "product_id_hex": f"0x{device.product_id:04X}",
        "manufacturer_string": device.manufacturer_string,
        "product_string": device.product_string,
        "serial_number": device.serial_number,
        "usage_page": device.usage_page,
        "usage_page_hex": None if device.usage_page is None else f"0x{device.usage_page:04X}",
        "usage": device.usage,
        "usage_hex": None if device.usage is None else f"0x{device.usage:04X}",
        "interface_number": device.interface_number,
        "joystick_collection": device.is_joystick_collection,
        "hidapi_path": device.path_text,
    }


def _compact_report_line(row: dict[str, Any]) -> str:
    """Format one report row for optional human-readable stderr output."""

    semantic = row["semantic_raw"]
    values = " ".join(f"{name}={semantic[name]:4d}" for name in SEMANTIC_AXIS_NAMES)
    status = "ok" if row["descriptor_range_ok"] else "out-of-range"
    elapsed = row["elapsed_ns"] / 1_000_000_000
    return f"{elapsed:8.3f}s #{row['index']:<6d} {values} range={status}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
