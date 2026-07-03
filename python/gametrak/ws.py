"""Bridge live GameTrak HID reports to browser WebSocket JSON.

``gametrak-ws`` is meant for browser sketches that cannot receive UDP OSC.
By default, it keeps the same raw data contract as ``gametrak-osc`` but sends
text JSON over a local WebSocket:

``{"address":"/gametrak/raw","args":[left_x,left_y,left_r,right_x,right_y,right_r,buttons]}``

The WebSocket implementation is intentionally small and dependency-free. It
implements only the server features needed here: HTTP upgrade handshakes and
server-to-client text frames. Browsers do not need to send messages back.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Sequence
import hashlib
import json
import socket
import sys
import threading
import time
from typing import TextIO

from gametrak.constants import EXPECTED_INPUT_REPORT_SIZE
from gametrak.diagnose import diagnostic_report
from gametrak.hid_backend import enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import GameTrakRawReport, decode_report, values_are_in_descriptor_range
from gametrak.values import SEMANTIC_AXIS_NAMES, normalized_tuple, raw_tuple

DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 2436
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_READ_LIMIT = 8192


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the WebSocket bridge."""

    parser = argparse.ArgumentParser(
        description="Stream live GameTrak values as WebSocket JSON for browser sketches.",
        epilog=(
            "WebSocket payloads are JSON text messages with address "
            "/gametrak/raw, or /gametrak/normalized with --normalized, "
            "and args in this order: "
            f"{' '.join(SEMANTIC_AXIS_NAMES)} buttons."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw", dest="normalized", action="store_false", help="send /gametrak/raw JSON")
    mode.add_argument("--normalized", action="store_true", help="send /gametrak/normalized JSON")
    parser.set_defaults(normalized=False)

    parser.add_argument("--host", default=DEFAULT_WS_HOST, help="WebSocket bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_WS_PORT, help="WebSocket TCP port")
    parser.add_argument("--rate", type=float, default=0.0, help="maximum send rate in Hz; default sends every report")
    parser.add_argument("--seconds", type=float, default=None, help="optional run duration for testing")
    parser.add_argument("--print", action="store_true", help="also print sent JSON messages to stderr")
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
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=5.0,
        help="reconnect if no valid input reports arrive for this long; 0 disables",
    )
    return parser.parse_args(argv)


def build_ws_message(report: GameTrakRawReport, *, normalized: bool = False) -> str:
    """Return one compact WebSocket JSON text payload for a decoded report."""

    if normalized:
        address = "/gametrak/normalized"
        values: list[int | float] = list(normalized_tuple(report))
    else:
        address = "/gametrak/raw"
        values = list(raw_tuple(report))
        values.append(0 if report.buttons is None else report.buttons)

    return json.dumps(
        {
            "address": address,
            "args": values,
        },
        separators=(",", ":"),
    )


class WebSocketJsonBroadcaster:
    """Tiny WebSocket text broadcaster for browser clients.

    The server accepts ordinary browser WebSocket connections and broadcasts
    text frames to all connected clients. It does not implement an application
    receive protocol; inbound browser data is ignored by design.
    """

    def __init__(self, host: str, port: int, *, stderr: TextIO = sys.stderr) -> None:
        self.host = host
        self.port = port
        self.stderr = stderr
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    @property
    def client_count(self) -> int:
        """Return the number of currently connected browser clients."""

        with self._clients_lock:
            return len(self._clients)

    def start(self) -> None:
        """Start the background accept loop."""

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.25)
        self._server = server
        self._running.set()
        self._thread = threading.Thread(target=self._accept_loop, name="gametrak-ws-server", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop accepting and close all connected clients."""

        self._running.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            _close_socket(client)

        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def broadcast(self, text: str) -> None:
        """Send one text frame to each connected client."""

        frame = websocket_text_frame(text)
        with self._clients_lock:
            clients = list(self._clients)

        dead: list[socket.socket] = []
        for client in clients:
            try:
                client.sendall(frame)
            except OSError:
                dead.append(client)

        if dead:
            with self._clients_lock:
                self._clients = [client for client in self._clients if client not in dead]
            for client in dead:
                _close_socket(client)

    def _accept_loop(self) -> None:
        while self._running.is_set():
            server = self._server
            if server is None:
                return
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return

            try:
                client.settimeout(2.0)
                request = _read_http_request(client)
                response = websocket_handshake_response(request)
                client.sendall(response)
                client.settimeout(None)
            except Exception as exc:
                print(f"gametrak-ws handshake error: {exc}", file=self.stderr)
                _close_socket(client)
                continue

            with self._clients_lock:
                self._clients.append(client)


def _read_http_request(client: socket.socket) -> bytes:
    """Read a WebSocket HTTP upgrade request."""

    chunks: list[bytes] = []
    total = 0
    while total < WS_READ_LIMIT:
        chunk = client.recv(1024)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        request = b"".join(chunks)
        if b"\r\n\r\n" in request:
            return request
    raise ValueError("incomplete WebSocket upgrade request")


def websocket_handshake_response(request: bytes) -> bytes:
    """Return the HTTP 101 response for a browser WebSocket upgrade."""

    headers = _parse_http_headers(request)
    key = headers.get("sec-websocket-key")
    if not key:
        raise ValueError("missing Sec-WebSocket-Key")
    accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")


def _parse_http_headers(request: bytes) -> dict[str, str]:
    """Parse HTTP headers into a case-insensitive lowercase mapping."""

    try:
        text = request.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid HTTP request encoding") from exc

    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if not line:
            break
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def websocket_text_frame(text: str) -> bytes:
    """Build one unmasked server-to-client WebSocket text frame."""

    payload = text.encode("utf-8")
    length = len(payload)
    header = bytearray([0x81])
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.extend((126, (length >> 8) & 0xFF, length & 0xFF))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    return bytes(header) + payload


def run(args: argparse.Namespace, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run the live HID-to-WebSocket bridge until interrupted or deadline is reached."""

    if args.diagnose:
        print(diagnostic_report(open_mode=args.open_mode, path=args.path), file=stdout, end="")
        return 0

    broadcaster = WebSocketJsonBroadcaster(args.host, args.port, stderr=stderr)
    broadcaster.start()
    print(f"gametrak-ws WebSocket JSON: ws://{args.host}:{args.port}", file=stderr)

    min_send_interval = 1.0 / args.rate if args.rate and args.rate > 0 else 0.0
    read_timeout_ms = max(int(args.read_timeout_ms), 0)
    deadline = time.monotonic() + args.seconds if args.seconds is not None else None
    total_sent = 0

    try:
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
                    broadcaster=broadcaster,
                    stderr=stderr,
                    min_send_interval=min_send_interval,
                    read_timeout_ms=read_timeout_ms,
                    ps2_key=ps2_key,
                    deadline=deadline,
                )
                if deadline is not None:
                    break
            except KeyboardInterrupt:
                print("gametrak-ws interrupted", file=stderr)
                break
            except Exception as exc:
                print(f"gametrak-ws error: {exc}", file=stderr)
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
        broadcaster.close()

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
    broadcaster: WebSocketJsonBroadcaster,
    stderr: TextIO,
    min_send_interval: float,
    read_timeout_ms: int,
    ps2_key: int | None,
    deadline: float | None,
) -> int:
    """Stream valid HID reports from one open handle to WebSocket clients."""

    report_count = 0
    sent_count = 0
    next_keepalive_report = 100
    next_send_at = 0.0
    stale_deadline = _next_stale_deadline(args.stale_seconds)

    while deadline is None or time.monotonic() < deadline:
        if ps2_key is not None and report_count >= next_keepalive_report:
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

        message = build_ws_message(report, normalized=args.normalized)
        broadcaster.broadcast(message)
        if args.print:
            print(message, file=stderr, flush=True)
        sent_count += 1
        next_send_at = now + min_send_interval

    return sent_count


def _next_stale_deadline(stale_seconds: float) -> float | None:
    """Return the next stale-report deadline, or ``None`` when disabled."""

    if stale_seconds <= 0:
        return None
    return time.monotonic() + stale_seconds


def _close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
