from __future__ import annotations

import pytest

from gametrak.report import decode_report, values_are_in_descriptor_range


def test_decode_report_unpacks_six_little_endian_axes() -> None:
    report = bytes.fromhex("000001000010ff0f3412cdab00010203")

    decoded = decode_report(report)

    assert decoded.axes == (0, 1, 4096, 4095, 0x1234, 0xABCD)
    assert decoded.hat == 0
    assert decoded.buttons == 0x010
    assert decoded.unknown_tail == bytes.fromhex("0203")
    assert decoded.raw == report


def test_decode_report_decodes_hat_and_twelve_buttons_from_tail_bits() -> None:
    report = bytes.fromhex("000000000000000000000000a5c30000")

    decoded = decode_report(report)

    assert decoded.hat == 0x05
    assert decoded.buttons == 0xC3A
    assert decoded.unknown_tail == b"\x00\x00"


def test_decode_report_rejects_short_report() -> None:
    with pytest.raises(ValueError, match="at least 12 bytes"):
        decode_report(b"\x00" * 11)


def test_values_are_in_descriptor_range() -> None:
    assert values_are_in_descriptor_range(decode_report(bytes.fromhex("00000100ff0f000800040002")))
    assert not values_are_in_descriptor_range(decode_report(bytes.fromhex("001000000000000000000000")))
