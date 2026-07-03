from __future__ import annotations

import json

from gametrak.report import decode_report
from gametrak.ws import parse_args, websocket_handshake_response, websocket_text_frame, build_ws_message


def test_ws_cli_defaults_to_localhost_port_2436() -> None:
    args = parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 2436
    assert args.rate == 0.0
    assert not args.normalized


def test_ws_cli_accepts_normalized() -> None:
    args = parse_args(["--normalized"])

    assert args.normalized


def test_ws_cli_accepts_diagnose() -> None:
    args = parse_args(["--diagnose"])

    assert args.diagnose


def test_build_ws_message_uses_raw_osc_shaped_payload() -> None:
    report = decode_report(bytes.fromhex("1a00380cfd0fc708a900ff0f00000000"))

    message = json.loads(build_ws_message(report))

    assert message == {
        "address": "/gametrak/raw",
        "args": [26, 3128, 4093, 2247, 169, 4095, 0],
    }


def test_build_ws_message_can_use_normalized_payload() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    message = json.loads(build_ws_message(report, normalized=True))

    assert message == {
        "address": "/gametrak/normalized",
        "args": [0.0, 0.0, 0.0, 1.0, -1.0, 1.0],
    }


def test_websocket_text_frame_builds_unmasked_server_text_frame() -> None:
    frame = websocket_text_frame("hello")

    assert frame == b"\x81\x05hello"


def test_websocket_handshake_response_uses_rfc_accept_key() -> None:
    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1:2436\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Version: 13\r\n"
        b"\r\n"
    )

    response = websocket_handshake_response(request)

    assert b"HTTP/1.1 101 Switching Protocols\r\n" in response
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n" in response
