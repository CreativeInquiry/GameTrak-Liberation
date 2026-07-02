from __future__ import annotations

from io import StringIO
import time

import pytest
from rich.console import Console

from gametrak.osc import (
    OscControlSocket,
    OscControlState,
    build_osc_messages,
    handle_control_address,
    osc_address_from_packet,
    parse_args,
)
from gametrak.report import decode_report


def test_build_osc_messages_includes_raw_norm_and_wekinator_payloads() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    messages = build_osc_messages(
        report,
        include_raw=True,
        include_norm=True,
        include_wekinator=True,
    )

    assert messages == [
        ("/gametrak/raw", [2048, 2048, 4095, 4095, 0, 0, 0]),
        ("/gametrak/norm", [0.0, 0.0, 0.0, 1.0, -1.0, 1.0]),
        ("/wekinator/control/inputs", [0.0, 0.0, 0.0, 1.0, -1.0, 1.0]),
    ]


def test_build_osc_messages_can_send_only_normalized_payload() -> None:
    report = decode_report(bytes.fromhex("00080008ff0fff0f0000000000000000"))

    messages = build_osc_messages(
        report,
        include_raw=False,
        include_norm=True,
        include_wekinator=False,
    )

    assert messages == [("/gametrak/norm", [0.0, 0.0, 0.0, 1.0, -1.0, 1.0])]


def test_control_state_consumes_init_and_reconnect_requests() -> None:
    state = OscControlState()

    assert not state.consume_init_requested()
    assert not state.consume_reconnect_requested()

    state.request_init()
    state.request_reconnect()

    assert state.consume_init_requested()
    assert state.consume_reconnect_requested()
    assert not state.consume_init_requested()
    assert not state.consume_reconnect_requested()


def test_control_listener_cli_defaults() -> None:
    args = parse_args([])

    assert args.port == 2434
    assert args.control_host == "127.0.0.1"
    assert args.control_port == 2435
    assert not args.no_control
    assert not args.raw
    assert not args.normalized


def test_osc_cli_accepts_diagnose() -> None:
    args = parse_args(["--diagnose"])

    assert args.diagnose


def test_osc_address_from_packet_extracts_address() -> None:
    assert osc_address_from_packet(b"/gametrak/init\0\0,i\0\0\0\0\0\x01") == "/gametrak/init"
    assert osc_address_from_packet(b"not-osc") is None


def test_handle_control_address_sets_matching_request_flags() -> None:
    state = OscControlState()
    console = Console(file=StringIO())

    assert handle_control_address("/gametrak/init", state, console)
    assert state.consume_init_requested()

    assert handle_control_address("/gametrak/reconnect", state, console)
    assert state.consume_reconnect_requested()

    assert not handle_control_address("/other", state, console)


def test_control_socket_poll_receives_osc_packet() -> None:
    state = OscControlState()
    console = Console(file=StringIO())
    try:
        control_socket = OscControlSocket("127.0.0.1", 0, state, console)
    except PermissionError:
        pytest.skip("local UDP bind is not available in this sandbox")
    port = control_socket._socket.getsockname()[1]

    try:
        from pythonosc.udp_client import SimpleUDPClient

        SimpleUDPClient("127.0.0.1", port).send_message("/gametrak/init", 1)

        for _ in range(10):
            control_socket.poll()
            if state.consume_init_requested():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("control socket did not receive /gametrak/init")
    finally:
        control_socket.close()
