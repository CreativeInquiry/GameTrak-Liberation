"""Stream live GameTrak values as whitespace-separated terminal lines.

``gametrak-stdout`` is intentionally a Unix-style command-line interface:
stdout contains only samples, one per line, and diagnostics go to stderr. That
lets another program consume the stream with a normal pipe:

``gametrak-stdout --rate 30 | other-program``
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys
import time
from typing import Literal, TextIO

from gametrak.constants import EXPECTED_INPUT_REPORT_SIZE
from gametrak.diagnose import diagnostic_report
from gametrak.hid_backend import enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import GameTrakRawReport, decode_report, values_are_in_descriptor_range
from gametrak.values import normalized_tuple, raw_tuple

ValueMode = Literal["raw", "norm", "hex", "0dec"]
HEX_WIDTH = 3
ZERO_DECIMAL_WIDTH = 4


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the stdout streamer."""

    parser = argparse.ArgumentParser(
        description="Stream live GameTrak values to stdout, one sample per line.",
        epilog=(
            "stdout contains only six whitespace-separated values. Diagnostics "
            "and errors are written to stderr so the stream is pipe-friendly."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw", dest="mode", action="store_const", const="raw", help="print raw 0..4095 values")
    mode.add_argument("--norm", dest="mode", action="store_const", const="norm", help="print normalized values")
    mode.add_argument("--hex", dest="mode", action="store_const", const="hex", help="print raw values as 3-digit uppercase hex")
    mode.add_argument("--0dec", dest="mode", action="store_const", const="0dec", help="print raw values as 4-digit zero-padded decimal")
    parser.set_defaults(mode="raw")

    parser.add_argument("--rate", type=float, default=0.0, help="maximum output rate in Hz; default prints every report")
    parser.add_argument("--seconds", type=float, default=None, help="optional run duration for testing")
    parser.add_argument("--diagnose", action="store_true", help="print a GameTrak HID/USB diagnostic report and exit")
    parser.add_argument("--precision", type=int, default=3, help="decimal places for --norm output")
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
    parser.add_argument("--reconnect-delay", type=float, default=1.0, help="seconds to wait before reconnecting")
    parser.add_argument("--no-reconnect", action="store_true", help="exit instead of reconnecting after device errors")
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=5.0,
        help="reconnect if no valid input reports arrive for this long; 0 disables",
    )
    return parser.parse_args(argv)


def format_stdout_line(
    report: GameTrakRawReport,
    *,
    mode: ValueMode,
    precision: int,
) -> str:
    """Format one report as the public stdout line protocol.

    All modes use the same six-channel semantic order:
    ``left_x left_y left_r right_x right_y right_r``.

    Formatting modes:
    - ``raw`` prints ordinary base-10 integers in the HID descriptor range
      ``0..4095``.
    - ``hex`` prints those same raw integers as uppercase fixed-width hex. The
      width is 3 because the largest possible axis value is ``0xFFF``.
    - ``0dec`` prints those same raw integers as fixed-width decimal. The width
      is 4 because the largest possible axis value is ``4095``.
    - ``norm`` prints descriptor-scale convenience values, not calibrated
      values.
    """

    if mode == "norm":
        decimals = max(0, precision)
        return " ".join(f"{value:.{decimals}f}" for value in normalized_tuple(report))

    if mode == "hex":
        return " ".join(f"{value:0{HEX_WIDTH}X}" for value in raw_tuple(report))

    if mode == "0dec":
        return " ".join(f"{value:0{ZERO_DECIMAL_WIDTH}d}" for value in raw_tuple(report))

    return " ".join(str(value) for value in raw_tuple(report))


def run(args: argparse.Namespace, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run the HID-to-stdout stream until interrupted or deadline is reached."""

    if args.diagnose:
        print(diagnostic_report(open_mode=args.open_mode, path=args.path), file=stdout, end="")
        return 0

    min_send_interval = 1.0 / args.rate if args.rate and args.rate > 0 else 0.0
    read_timeout_ms = max(int(args.read_timeout_ms), 0)
    deadline = time.monotonic() + args.seconds if args.seconds is not None else None
    total_sent = 0

    while deadline is None or time.monotonic() < deadline:
        device = None
        try:
            device = _open_device(args)
            device.set_nonblocking(False)
            ps2_key = None
            if not args.no_ps2_init:
                ps2_key = send_ps2_init(device, args.read_size)

            total_sent += _stream_device(
                args,
                device,
                stdout=stdout,
                mode=args.mode,
                precision=args.precision,
                min_send_interval=min_send_interval,
                read_timeout_ms=read_timeout_ms,
                ps2_key=ps2_key,
                deadline=deadline,
            )
            if deadline is not None:
                break
        except KeyboardInterrupt:
            print("gametrak-stdout interrupted", file=stderr)
            break
        except BrokenPipeError:
            # A downstream program such as `head` may close the pipe early.
            # That is normal for Unix pipelines, so exit without a device error.
            return 0
        except Exception as exc:
            print(f"gametrak-stdout error: {exc}", file=stderr)
            if args.no_reconnect:
                return 2
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(max(args.reconnect_delay, 0.0))
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

    return 0 if total_sent else 1


def _open_device(args: argparse.Namespace) -> object:
    """Open the GameTrak according to CLI selection rules."""

    selected_path = args.path
    if selected_path is None and args.open_mode != "vidpid":
        selected = select_gametrak_device(enumerate_gametrak_devices())
        selected_path = None if selected is None else selected.path
    return open_gametrak(selected_path, use_vid_pid=args.open_mode == "vidpid")


def _stream_device(
    args: argparse.Namespace,
    device: object,
    *,
    stdout: TextIO,
    mode: ValueMode,
    precision: int,
    min_send_interval: float,
    read_timeout_ms: int,
    ps2_key: int | None,
    deadline: float | None,
) -> int:
    """Stream valid reports from one open HID handle to stdout."""

    report_count = 0
    sent_count = 0
    next_keepalive_report = 100
    next_send_at = 0.0
    stale_deadline = _next_stale_deadline(args.stale_seconds)

    while deadline is None or time.monotonic() < deadline:
        if ps2_key is not None and report_count >= next_keepalive_report:
            # Match the OSC bridge's keepalive behavior: send ps2 keepalives
            # after valid reports, not after read timeouts.
            ps2_key = send_ps2_keepalive(device, ps2_key)
            next_keepalive_report += 100

        data = device.read(args.read_size, read_timeout_ms)
        now = time.monotonic()
        if not data:
            if stale_deadline is not None and now >= stale_deadline:
                raise RuntimeError(f"no valid GameTrak reports for {args.stale_seconds:.1f}s")
            continue

        report = decode_report(bytes(data))
        if not values_are_in_descriptor_range(report):
            continue

        report_count += 1
        stale_deadline = _next_stale_deadline(args.stale_seconds)

        if min_send_interval and now < next_send_at:
            continue

        print(format_stdout_line(report, mode=mode, precision=precision), file=stdout, flush=True)
        sent_count += 1
        next_send_at = now + min_send_interval

    return sent_count


def _next_stale_deadline(stale_seconds: float) -> float | None:
    """Return the next stale-report deadline, or ``None`` when disabled."""

    if stale_seconds <= 0:
        return None
    return time.monotonic() + stale_seconds


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
