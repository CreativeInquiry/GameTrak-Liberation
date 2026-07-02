from __future__ import annotations

import pytest

from gametrak.midi import (
    build_pitch_bend_messages,
    parse_args,
    pitch_bend_raw_to_raw_axis,
    raw_axis_to_mido_pitch,
    raw_axis_to_pitch_bend_raw,
)


def test_midi_cli_defaults_to_named_virtual_port_and_unthrottled() -> None:
    args = parse_args([])

    assert args.port_name == "GameTrak MIDI"
    assert args.rate == 0.0


def test_midi_cli_accepts_port_name_and_rate() -> None:
    args = parse_args(["--port-name", "GameTrak Test", "--rate", "30"])

    assert args.port_name == "GameTrak Test"
    assert args.rate == 30


def test_midi_cli_accepts_diagnose() -> None:
    args = parse_args(["--diagnose"])

    assert args.diagnose


@pytest.mark.parametrize(
    ("raw_value", "pitch_bend_raw", "mido_pitch"),
    [
        (0, 8192, 0),
        (4095, 12287, 4095),
    ],
)
def test_raw_axis_is_sent_as_unscaled_signed_pitch_bend(raw_value: int, pitch_bend_raw: int, mido_pitch: int) -> None:
    assert raw_axis_to_pitch_bend_raw(raw_value) == pitch_bend_raw
    assert raw_axis_to_mido_pitch(raw_value) == mido_pitch


@pytest.mark.parametrize("raw_value", [0, 26, 169, 2048, 3128, 4095])
def test_pitch_bend_round_trip_preserves_raw_values(raw_value: int) -> None:
    pitch_bend_raw = raw_axis_to_pitch_bend_raw(raw_value)

    assert pitch_bend_raw_to_raw_axis(pitch_bend_raw) == raw_value


def test_build_pitch_bend_messages_uses_midi_channels_one_through_six() -> None:
    messages = build_pitch_bend_messages([0, 1, 2, 3, 4, 4095])

    assert [channel for channel, _pitch in messages] == [0, 1, 2, 3, 4, 5]
    assert messages[0] == (0, 0)
    assert messages[-1] == (5, 4095)


def test_build_pitch_bend_messages_requires_six_values() -> None:
    with pytest.raises(ValueError, match="expected 6 raw values"):
        build_pitch_bend_messages([0, 1, 2])
