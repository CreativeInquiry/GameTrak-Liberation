"""OSC transmitter for live GameTrak HID reports.

This module owns the live bridge from the GameTrak HID device to OSC.

Responsibilities:
- open and close exactly one hidapi handle at a time;
- send the libgametrak ps2 init sequence required by this device;
- decode reports into semantic axis order;
- transmit raw and/or normalized OSC payloads;
- listen for lightweight OSC control messages that request init or reconnect.

The control socket is intentionally polled from the same loop that owns the HID
handle. That keeps close/reopen/init operations serialized, which matters on
macOS when competing HID clients or stale handles can make reconnection
behavior confusing.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import socket
import sys
import time
from typing import Protocol

from pythonosc.udp_client import SimpleUDPClient
from rich.console import Console

from gametrak.constants import EXPECTED_INPUT_REPORT_SIZE, GAMETRAK_PID, GAMETRAK_VID
from gametrak.diagnose import diagnostic_report
from gametrak.hid_backend import enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import GameTrakRawReport, decode_report, values_are_in_descriptor_range
from gametrak.values import normalized_tuple, raw_tuple


class OscClient(Protocol):
    """Minimal send interface shared by python-osc and tests."""

    def send_message(self, address: str, value: Sequence[int | float]) -> None: ...


OscMessage = tuple[str, list[int | float]]


class ReconnectRequested(Exception):
    """Raised inside the HID loop when an operator requests a full reconnect."""


@dataclass
class OscControlState:
    """Latched control requests consumed by the HID stream loop.

    The control socket only knows about OSC packets. It should not close or
    reinitialize the HID device directly. Instead, it sets these flags, and the
    HID loop consumes them at a known point before the next read.
    """

    reconnect_requested: bool = False
    init_requested: bool = False

    def request_reconnect(self) -> None:
        """Request a close/reopen cycle on the next HID loop iteration."""

        self.reconnect_requested = True

    def request_init(self) -> None:
        """Request a ps2 init resend on the current HID handle."""

        self.init_requested = True

    def consume_reconnect_requested(self) -> bool:
        """Return and clear the pending reconnect request."""

        if self.reconnect_requested:
            self.reconnect_requested = False
            return True
        return False

    def consume_init_requested(self) -> bool:
        """Return and clear the pending init request."""

        if self.init_requested:
            self.init_requested = False
            return True
        return False


class OscControlSocket:
    """Nonblocking OSC-address listener for bridge control messages.

    Only the OSC address is parsed because the control messages do not need
    arguments:

    - ``/gametrak/init`` resends the ps2 init sequence.
    - ``/gametrak/reconnect`` releases and reacquires the HID device.

    The socket is not run on a background thread. ``poll()`` is called by the
    main HID loop, which keeps all HID lifecycle work single-threaded.
    """

    def __init__(self, host: str, port: int, control_state: OscControlState, console: Console) -> None:
        self.control_state = control_state
        self.console = console
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        self._socket.setblocking(False)

    def poll(self) -> None:
        """Drain all pending control packets without blocking."""

        while True:
            try:
                packet, _addr = self._socket.recvfrom(4096)
            except BlockingIOError:
                return
            except OSError:
                return

            address = osc_address_from_packet(packet)
            if address is not None:
                handle_control_address(address, self.control_state, self.console)

    def close(self) -> None:
        """Close the UDP control socket."""

        self._socket.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the OSC bridge."""

    parser = argparse.ArgumentParser(
        description="Transmit live GameTrak HID reports as OSC messages.",
        epilog=(
            "By default, sends /gametrak/raw at the device's natural report "
            "rate. Use --normalized only for descriptor-scale convenience "
            "values; application calibration belongs downstream."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="OSC destination host")
    parser.add_argument("--port", type=int, default=2434, help="OSC destination UDP port")
    parser.add_argument("--wekinator", action="store_true", help="also send /wekinator/control/inputs")
    parser.add_argument("--raw", action="store_true", help="send /gametrak/raw")
    parser.add_argument("--normalized", action="store_true", help="send /gametrak/norm")
    parser.add_argument("--rate", type=float, default=0.0, help="maximum OSC send rate in Hz; default sends every report")
    parser.add_argument("--print", action="store_true", help="print transmitted values")
    parser.add_argument("--seconds", type=float, default=None, help="optional run duration for testing")
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
    parser.add_argument("--reconnect-delay", type=float, default=1.0, help="seconds to wait before reconnecting")
    parser.add_argument("--no-reconnect", action="store_true", help="exit instead of reconnecting after device errors")
    parser.add_argument("--control-host", default="127.0.0.1", help="OSC control listener host")
    parser.add_argument("--control-port", type=int, default=2435, help="OSC control listener UDP port")
    parser.add_argument("--no-control", action="store_true", help="disable OSC control listener")
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=5.0,
        help="reconnect if no valid input reports arrive for this long; 0 disables",
    )
    return parser.parse_args(argv)


def build_osc_messages(
    report: GameTrakRawReport,
    *,
    include_raw: bool,
    include_norm: bool,
    include_wekinator: bool,
) -> list[OscMessage]:
    """Build OSC payloads from one decoded report.

    Raw and descriptor-normalized payloads are both emitted in semantic order:
    ``left_x, left_y, left_r, right_x, right_y, right_r``.

    ``/gametrak/raw`` appends the decoded button bitfield as a seventh
    argument. ``/gametrak/norm`` and Wekinator payloads contain only the six
    convenience-normalized floats. No calibration profile, deadband, smoothing,
    or range fitting is applied.
    """

    messages: list[OscMessage] = []
    norm = list(normalized_tuple(report))

    if include_raw:
        # Raw payloads are semantic-axis ordered, not HID descriptor ordered.
        raw = list(raw_tuple(report))
        raw.append(0 if report.buttons is None else report.buttons)
        messages.append(("/gametrak/raw", raw))

    if include_norm:
        messages.append(("/gametrak/norm", norm))

    if include_wekinator:
        messages.append(("/wekinator/control/inputs", norm))

    return messages


def send_osc_messages(client: OscClient, messages: Sequence[OscMessage]) -> None:
    """Send a batch of already-built OSC messages."""

    for address, values in messages:
        client.send_message(address, values)


def osc_address_from_packet(packet: bytes) -> str | None:
    """Return the OSC address string from a UDP packet.

    This is deliberately a partial OSC parser. For bridge control we only need
    the address segment before the first NUL byte.
    """

    address_bytes = packet.split(b"\0", 1)[0]
    if not address_bytes.startswith(b"/"):
        return None
    try:
        return address_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None


def handle_control_address(address: str, control_state: OscControlState, console: Console) -> bool:
    """Handle one recognized OSC control address.

    Returns ``True`` when the address was recognized and converted into a
    control-state request.
    """

    if address == "/gametrak/reconnect":
        control_state.request_reconnect()
        console.print(f"[yellow]OSC control requested reconnect via {address}.[/]")
        return True

    if address == "/gametrak/init":
        control_state.request_init()
        console.print(f"[yellow]OSC control requested ps2 init via {address}.[/]")
        return True

    return False


def run(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Run the live HID-to-OSC bridge until interrupted or deadline is reached."""

    console = console or Console()
    if args.diagnose:
        console.print(diagnostic_report(open_mode=args.open_mode, path=args.path), end="", markup=False)
        return 0

    client = SimpleUDPClient(args.host, args.port)
    control_state = OscControlState()
    control_socket: OscControlSocket | None = None

    include_raw = bool(args.raw)
    include_norm = bool(args.normalized)
    if not include_raw and not include_norm and not args.wekinator:
        include_raw = True
    if args.wekinator:
        include_norm = True

    min_send_interval = 1.0 / args.rate if args.rate and args.rate > 0 else 0.0
    read_timeout_ms = max(int(args.read_timeout_ms), 0)
    deadline = time.monotonic() + args.seconds if args.seconds is not None else None
    total_sent = 0

    if not args.no_control:
        control_socket = OscControlSocket(args.control_host, args.control_port, control_state, console)
        console.print(
            f"[dim]Listening for OSC control on {args.control_host}:{args.control_port} "
            "(/gametrak/init, /gametrak/reconnect).[/]"
        )

    try:
        while deadline is None or time.monotonic() < deadline:
            device = None
            try:
                device = _open_device(args)
                device.set_nonblocking(False)
                ps2_key = None
                if not args.no_ps2_init:
                    ps2_key = send_ps2_init(
                        device,
                        args.read_size,
                        log=(lambda message: console.print(f"[dim]{message}[/]")) if args.print else None,
                    )

                if args.print:
                    console.print(f"Sending OSC to {args.host}:{args.port}")

                sent = _stream_device(
                    args,
                    device,
                    client,
                    control_state=control_state,
                    control_socket=control_socket,
                    include_raw=include_raw,
                    include_norm=include_norm,
                    min_send_interval=min_send_interval,
                    read_timeout_ms=read_timeout_ms,
                    ps2_key=ps2_key,
                    deadline=deadline,
                    console=console,
                )
                total_sent += sent
                if deadline is not None:
                    break
            except ReconnectRequested:
                console.print("[yellow]Closing HID handle for requested reconnect.[/]")
                if deadline is not None and time.monotonic() >= deadline:
                    break
            except KeyboardInterrupt:
                console.print("\n[yellow]OSC transmitter interrupted by user.[/]")
                break
            except Exception as exc:
                console.print(f"[bold red]GameTrak OSC error:[/] {exc}")
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
    finally:
        if control_socket is not None:
            control_socket.close()

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
    client: OscClient,
    *,
    control_state: OscControlState,
    control_socket: OscControlSocket | None,
    include_raw: bool,
    include_norm: bool,
    min_send_interval: float,
    read_timeout_ms: int,
    ps2_key: int | None,
    deadline: float | None,
    console: Console,
) -> int:
    """Stream valid HID reports from one open device handle.

    This function owns the active HID handle for its whole lifetime. Requested
    reconnects are signaled by raising ``ReconnectRequested`` so the caller can
    close the handle in its ``finally`` block, then reopen a fresh one.
    """

    report_count = 0
    sent_count = 0
    next_keepalive_report = 100
    next_send_at = 0.0
    stale_deadline = _next_stale_deadline(args.stale_seconds)

    while deadline is None or time.monotonic() < deadline:
        if control_socket is not None:
            control_socket.poll()

        if control_state.consume_reconnect_requested():
            raise ReconnectRequested

        if control_state.consume_init_requested():
            console.print("[yellow]Resending libgametrak ps2 init on current HID handle.[/]")
            ps2_key = send_ps2_init(
                device,
                args.read_size,
                log=(lambda message: console.print(f"[dim]{message}[/]")) if args.print else None,
            )
            next_keepalive_report = report_count + 100

        if ps2_key is not None and report_count >= next_keepalive_report:
            # The ps2 init mode needs periodic 0x46 keepalives. Count valid
            # reports rather than loop iterations so read timeouts cannot
            # cause repeated writes with the same key.
            ps2_key = send_ps2_keepalive(
                device,
                ps2_key,
                log=(lambda message: console.print(f"[dim]{message}[/]")) if args.print else None,
            )
            next_keepalive_report += 100

        data = device.read(args.read_size, read_timeout_ms)
        now = time.monotonic()
        if not data:
            if stale_deadline is not None and now >= stale_deadline:
                raise RuntimeError(f"no valid GameTrak reports for {args.stale_seconds:.1f}s")
            continue

        report = decode_report(bytes(data))
        if not values_are_in_descriptor_range(report):
            # Ignore the "Gametrak" init echo and any malformed reports before
            # they reach OSC consumers.
            continue

        report_count += 1
        stale_deadline = _next_stale_deadline(args.stale_seconds)

        if min_send_interval and now < next_send_at:
            continue

        messages = build_osc_messages(
            report,
            include_raw=include_raw,
            include_norm=include_norm,
            include_wekinator=args.wekinator,
        )
        send_osc_messages(client, messages)
        sent_count += 1
        next_send_at = now + min_send_interval

        if args.print:
            _print_values(console, messages)

    return sent_count


def _next_stale_deadline(stale_seconds: float) -> float | None:
    """Return the next stale-report deadline, or ``None`` when disabled."""

    if stale_seconds <= 0:
        return None
    return time.monotonic() + stale_seconds


def _print_values(console: Console, messages: Sequence[OscMessage]) -> None:
    """Print OSC messages in a compact one-line debug format."""

    rendered = " ".join(
        f"{address}=[{', '.join(_format_value(value) for value in values)}]"
        for address, values in messages
    )
    console.print(rendered)


def _format_value(value: int | float) -> str:
    """Format debug values without hiding integer/raw channels."""

    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
