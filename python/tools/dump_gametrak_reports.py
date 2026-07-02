#!/usr/bin/env python3
"""Capture raw GameTrak HID input reports."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys
from typing import TextIO

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gametrak.constants import (
    EXPECTED_INPUT_REPORT_SIZE,
    GAMETRAK_PID,
    GAMETRAK_VID,
)
from gametrak.hid_backend import HidDeviceInfo, enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import AXIS_NAMES, decode_report, values_are_in_descriptor_range


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=None, help="capture duration; default is until Ctrl-C")
    parser.add_argument("--out", type=Path, default=None, help="optional JSONL output path")
    parser.add_argument("--poll-ms", type=float, default=1.0, help="sleep interval when no report is available")
    parser.add_argument(
        "--read-timeout-ms",
        type=int,
        default=50,
        help="hidapi timed-read timeout; use 0 for nonblocking polling",
    )
    parser.add_argument(
        "--blocking-read",
        action="store_true",
        help="use plain blocking read(size), matching libgametrak's hid_read(handle, buf, 16)",
    )
    parser.add_argument(
        "--open-mode",
        choices=("auto", "path", "vidpid"),
        default="auto",
        help="how to open the device; vidpid most closely matches libgametrak",
    )
    parser.add_argument(
        "--ps2-init",
        action="store_true",
        help="send libgametrak's optional ps2mode init writes before reading",
    )
    parser.add_argument(
        "--read-size",
        type=int,
        default=EXPECTED_INPUT_REPORT_SIZE,
        help="number of bytes to request from hidapi per read",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="optional hidapi path to open; defaults to the GameTrak joystick collection",
    )
    return parser.parse_args()


def _open_jsonl(path: Path | None) -> TextIO | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def _json_record(time_ns: int, report: bytes) -> dict[str, Any]:
    decoded = decode_report(report)
    return {
        "time_ns": time_ns,
        "raw": list(decoded.axes),
        "hat": decoded.hat,
        "buttons": decoded.buttons,
        "unknown_tail_hex": decoded.unknown_tail.hex(),
        "raw_bytes_hex": decoded.raw.hex(),
        "length": len(report),
        "descriptor_range_ok": values_are_in_descriptor_range(decoded),
    }


def _print_report(console: Console, elapsed: float, count: int, report: bytes) -> None:
    decoded = decode_report(report)
    axes = " ".join(
        f"{name}={value:4d}" for name, value in zip(AXIS_NAMES, decoded.axes, strict=True)
    )
    tail = decoded.unknown_tail.hex() or "-"
    hat = "-" if decoded.hat is None else str(decoded.hat)
    buttons = "-" if decoded.buttons is None else f"0x{decoded.buttons:03X}"
    range_status = "ok" if values_are_in_descriptor_range(decoded) else "out-of-range"
    console.print(
        f"{elapsed:9.3f}s #{count:<6d} len={len(report):2d} "
        f"hex={report.hex()} {axes} hat={hat} buttons={buttons} tail={tail} range={range_status}"
    )


def _print_summary(console: Console, count: int, start_ns: int, end_ns: int) -> None:
    elapsed = max((end_ns - start_ns) / 1_000_000_000, 0.0)
    table = Table(title="Capture Summary")
    table.add_column("Reports", justify="right")
    table.add_column("Elapsed Seconds", justify="right")
    table.add_column("Mean Rate Hz", justify="right")
    table.add_row(str(count), f"{elapsed:.3f}", f"{(count / elapsed) if elapsed else 0.0:.2f}")
    console.print(table)


def _print_candidates(console: Console, candidates: list[HidDeviceInfo]) -> None:
    table = Table(title="GameTrak HID Collections")
    table.add_column("VID")
    table.add_column("PID")
    table.add_column("Usage Page")
    table.add_column("Usage")
    table.add_column("Interface")
    table.add_column("Path", overflow="fold")

    for device in candidates:
        table.add_row(
            f"0x{device.vendor_id:04X}",
            f"0x{device.product_id:04X}",
            "" if device.usage_page is None else f"0x{device.usage_page:02X}",
            "" if device.usage is None else f"0x{device.usage:02X}",
            str(device.interface_number if device.interface_number is not None else ""),
            device.path_text,
        )

    console.print(table)


def main() -> int:
    args = parse_args()
    console = Console()
    poll_seconds = max(args.poll_ms, 0.0) / 1000.0
    read_timeout_ms = max(args.read_timeout_ms, 0)
    blocking_read = bool(args.blocking_read)

    try:
        candidates = enumerate_gametrak_devices()
    except Exception as exc:
        console.print(f"[bold red]Failed to enumerate HID devices:[/] {exc}")
        return 2

    selected = select_gametrak_device(candidates)
    if args.path is not None:
        selected_path = args.path
    elif args.open_mode == "vidpid":
        selected_path = None
    else:
        selected_path = selected.path if selected is not None else None

    if candidates:
        _print_candidates(console, candidates)

    try:
        device = open_gametrak(selected_path, use_vid_pid=args.open_mode == "vidpid")
        device.set_nonblocking((not blocking_read) and read_timeout_ms == 0)
        if args.ps2_init:
            console.print("[yellow]Sending libgametrak ps2mode init sequence.[/]")
            ps2_key = send_ps2_init(device, args.read_size, log=console.print)
        else:
            ps2_key = None
    except Exception as exc:
        console.print(
            f"[bold red]Failed to open GameTrak VID 0x{GAMETRAK_VID:04X} "
            f"PID 0x{GAMETRAK_PID:04X}:[/] {exc}"
        )
        if not candidates:
            console.print("No matching GameTrak HID collections were found during enumeration.")
        elif selected_path is not None:
            console.print(
                "Tried path: "
                + (
                    selected_path.decode("utf-8", errors="replace")
                    if isinstance(selected_path, bytes)
                    else selected_path
                )
            )
        console.print("Run tools/dump_hid_devices.py --all to confirm the device is visible.")
        return 2

    console.print(
        f"[bold green]Opened GameTrak[/] VID 0x{GAMETRAK_VID:04X} PID 0x{GAMETRAK_PID:04X}; "
        f"reading up to {args.read_size} bytes per report "
        f"({'blocking' if blocking_read else 'nonblocking' if read_timeout_ms == 0 else f'timeout={read_timeout_ms}ms'}; "
        f"open-mode={args.open_mode})."
    )

    out_file = _open_jsonl(args.out)
    if out_file is not None:
        console.print(f"Writing JSONL capture to {args.out}")

    start_ns = time.monotonic_ns()
    deadline_ns = start_ns + int(args.seconds * 1_000_000_000) if args.seconds is not None else None
    count = 0
    next_keepalive_report = 100
    no_report_notice_at = start_ns + 2_000_000_000
    no_report_notice_printed = False

    try:
        while True:
            now_ns = time.monotonic_ns()
            if deadline_ns is not None and now_ns >= deadline_ns:
                break

            if ps2_key is not None and count >= next_keepalive_report:
                ps2_key = send_ps2_keepalive(device, ps2_key, log=console.print)
                next_keepalive_report += 100

            if blocking_read:
                data = device.read(args.read_size)
            elif read_timeout_ms == 0:
                data = device.read(args.read_size)
            else:
                data = device.read(args.read_size, read_timeout_ms)
            if not data:
                if not no_report_notice_printed and now_ns >= no_report_notice_at:
                    console.print(
                        "[yellow]No input reports received yet. The device is open, but it may be idle, "
                        "blocked by another process, or require additional investigation.[/]"
                    )
                    no_report_notice_printed = True
                if read_timeout_ms == 0 and poll_seconds:
                    time.sleep(poll_seconds)
                continue

            report = bytes(data)
            count += 1
            elapsed = (now_ns - start_ns) / 1_000_000_000

            try:
                _print_report(console, elapsed, count, report)
                if out_file is not None:
                    out_file.write(json.dumps(_json_record(time.monotonic_ns(), report)) + "\n")
                    out_file.flush()
            except ValueError as exc:
                console.print(f"[red]Could not decode report #{count}: {exc}; raw={report.hex()}[/]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Capture interrupted by user.[/]")
    finally:
        end_ns = time.monotonic_ns()
        if out_file is not None:
            out_file.close()
        device.close()

    if count == 0:
        console.print("[bold yellow]No reports captured.[/]")
    else:
        _print_summary(console, count, start_ns, end_ns)
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
