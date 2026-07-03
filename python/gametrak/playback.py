"""Replay GameTrak JSONL recordings through the project output protocols.

``gametrak-playback`` is an offline stand-in for the physical controller. It
reads files produced by ``gametrak-record`` and emits the decoded samples over
one selected protocol: stdout, OSC, MIDI, or browser WebSocket JSON.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Literal, Protocol, TextIO

from gametrak.report import GameTrakRawReport, decode_report, values_are_in_descriptor_range
from gametrak.values import normalized_tuple, raw_tuple

ProtocolName = Literal["stdout", "osc", "midi", "ws"]
ValueMode = Literal["raw", "normalized", "hex", "0dec"]
DEFAULT_OSC_HOST = "127.0.0.1"
DEFAULT_OSC_PORT = 2434
DEFAULT_MIDI_PORT_NAME = "GameTrak MIDI"
DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 2436
HEX_WIDTH = 3
ZERO_DECIMAL_WIDTH = 4
NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class PlaybackSample:
    """One replayable report from a JSONL capture."""

    index: int
    elapsed_ns: int
    report: GameTrakRawReport


class PlaybackSink(Protocol):
    """Minimal sink interface used by the playback scheduler."""

    def send(self, report: GameTrakRawReport) -> None: ...

    def close(self) -> None: ...


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for JSONL playback."""

    parser = argparse.ArgumentParser(
        description="Replay a gametrak-record JSONL capture through one output protocol.",
        epilog=(
            "Timing follows recorded elapsed_ns by default. Use --rate to force "
            "a fixed output rate, --speed to scale recorded timing, and --loop "
            "for receiver development without the physical GameTrak."
        ),
    )
    parser.add_argument("jsonl", type=Path, help="JSONL file produced by gametrak-record")

    protocol = parser.add_mutually_exclusive_group(required=True)
    protocol.add_argument("--stdout", dest="protocol", action="store_const", const="stdout", help="print samples to stdout")
    protocol.add_argument("--osc", dest="protocol", action="store_const", const="osc", help="send OSC samples")
    protocol.add_argument("--midi", dest="protocol", action="store_const", const="midi", help="send MIDI pitch bend samples")
    protocol.add_argument("--ws", dest="protocol", action="store_const", const="ws", help="serve WebSocket JSON samples")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw", dest="mode", action="store_const", const="raw", help="emit raw values")
    mode.add_argument("--normalized", dest="mode", action="store_const", const="normalized", help="emit normalized values")
    mode.add_argument("--hex", dest="mode", action="store_const", const="hex", help="stdout only: emit fixed-width raw hex")
    mode.add_argument("--0dec", dest="mode", action="store_const", const="0dec", help="stdout only: emit zero-padded raw decimals")
    parser.set_defaults(mode="raw")

    parser.add_argument("--rate", type=float, default=None, help="override recorded timing with a fixed output rate in Hz")
    parser.add_argument("--speed", type=float, default=1.0, help="play recorded timing this many times faster")
    parser.add_argument("--seconds", type=float, default=None, help="optional maximum playback duration")
    parser.add_argument("--loop", action="store_true", help="restart at the beginning after the last sample")
    parser.add_argument("--include-invalid", action="store_true", help="include rows outside the 0..4095 descriptor range")
    parser.add_argument("--precision", type=int, default=3, help="decimal places for normalized stdout output")
    parser.add_argument("--host", default=None, help="OSC destination host or WebSocket bind host")
    parser.add_argument("--port", type=int, default=None, help="OSC destination UDP port or WebSocket TCP port")
    parser.add_argument("--wekinator", action="store_true", help="OSC only: also send /wekinator/control/inputs")
    parser.add_argument("--port-name", default=DEFAULT_MIDI_PORT_NAME, help="MIDI only: virtual MIDI output port name")
    parser.add_argument("--print", action="store_true", help="print compact playback diagnostics to stderr")
    return parser.parse_args(argv)


def load_samples(path: Path, *, include_invalid: bool = False) -> list[PlaybackSample]:
    """Load replayable report rows from a ``gametrak-record`` JSONL file."""

    samples: list[PlaybackSample] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            sample = sample_from_row(row, line_number=line_number, include_invalid=include_invalid)
            if sample is not None:
                samples.append(sample)
    return samples


def sample_from_row(row: dict[str, Any], *, line_number: int, include_invalid: bool = False) -> PlaybackSample | None:
    """Return a replay sample for one JSONL row, or ``None`` for non-report rows."""

    if row.get("type") != "report":
        return None

    raw_hex = row.get("raw_bytes_hex")
    if not isinstance(raw_hex, str):
        raise ValueError(f"line {line_number}: report row is missing raw_bytes_hex")

    try:
        report_bytes = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise ValueError(f"line {line_number}: invalid raw_bytes_hex") from exc

    report = decode_report(report_bytes)
    if not include_invalid and not values_are_in_descriptor_range(report):
        return None

    elapsed_ns = int(row.get("elapsed_ns", 0))
    index = int(row.get("index", line_number))
    return PlaybackSample(index=index, elapsed_ns=elapsed_ns, report=report)


def validate_args(args: argparse.Namespace) -> None:
    """Reject protocol/mode combinations that would be ambiguous or invalid."""

    if args.rate is not None and args.rate <= 0:
        raise ValueError("--rate must be greater than 0")
    if args.speed <= 0:
        raise ValueError("--speed must be greater than 0")
    if args.seconds is not None and args.seconds <= 0:
        raise ValueError("--seconds must be greater than 0")
    if args.protocol != "stdout" and args.mode in ("hex", "0dec"):
        raise ValueError("--hex and --0dec are only valid with --stdout")
    if args.protocol == "midi" and args.mode != "raw":
        raise ValueError("--midi playback supports raw values only")
    if args.wekinator and args.protocol != "osc":
        raise ValueError("--wekinator is only valid with --osc")


def run(args: argparse.Namespace, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run playback until the capture ends, loops forever, or a deadline fires."""

    try:
        validate_args(args)
        samples = load_samples(args.jsonl, include_invalid=args.include_invalid)
    except Exception as exc:
        print(f"gametrak-playback error: {exc}", file=stderr)
        return 2

    if not samples:
        print("gametrak-playback error: no replayable report rows found", file=stderr)
        return 1

    try:
        sink = create_sink(args, stdout=stdout, stderr=stderr)
    except Exception as exc:
        print(f"gametrak-playback error: {exc}", file=stderr)
        return 2
    if args.print:
        print(
            f"gametrak-playback loaded {len(samples)} samples from {args.jsonl}",
            file=stderr,
        )

    try:
        sent = play_samples(samples, args, sink=sink, stderr=stderr)
    except KeyboardInterrupt:
        print("gametrak-playback interrupted", file=stderr)
        return 0
    except BrokenPipeError:
        return 0
    except Exception as exc:
        print(f"gametrak-playback error: {exc}", file=stderr)
        return 2
    finally:
        sink.close()

    return 0 if sent else 1


def create_sink(args: argparse.Namespace, *, stdout: TextIO, stderr: TextIO) -> PlaybackSink:
    """Create the selected output sink."""

    if args.protocol == "stdout":
        return StdoutSink(stdout=stdout, mode=args.mode, precision=args.precision)
    if args.protocol == "osc":
        return OscSink(
            host=args.host or DEFAULT_OSC_HOST,
            port=args.port or DEFAULT_OSC_PORT,
            normalized=args.mode == "normalized",
            wekinator=args.wekinator,
        )
    if args.protocol == "midi":
        return MidiSink(port_name=args.port_name, stderr=stderr)
    if args.protocol == "ws":
        return WebSocketSink(
            host=args.host or DEFAULT_WS_HOST,
            port=args.port or DEFAULT_WS_PORT,
            normalized=args.mode == "normalized",
            stderr=stderr,
        )
    raise ValueError(f"unknown protocol: {args.protocol}")


def play_samples(
    samples: Sequence[PlaybackSample],
    args: argparse.Namespace,
    *,
    sink: PlaybackSink,
    stderr: TextIO,
) -> int:
    """Replay samples with recorded or fixed-rate timing."""

    sent = 0
    deadline = time.monotonic() + args.seconds if args.seconds is not None else None

    while True:
        loop_start = time.monotonic()
        first_elapsed_ns = samples[0].elapsed_ns
        for position, sample in enumerate(samples):
            if deadline is not None and time.monotonic() >= deadline:
                return sent

            target_offset = sample_offset_seconds(
                sample,
                position=position,
                first_elapsed_ns=first_elapsed_ns,
                rate=args.rate,
                speed=args.speed,
            )
            sleep_until(loop_start + target_offset, deadline=deadline)
            if deadline is not None and time.monotonic() >= deadline:
                return sent

            sink.send(sample.report)
            sent += 1
            if args.print and sent % 100 == 0:
                print(f"gametrak-playback sent {sent} samples", file=stderr)

        if not args.loop:
            return sent


def sample_offset_seconds(
    sample: PlaybackSample,
    *,
    position: int,
    first_elapsed_ns: int,
    rate: float | None,
    speed: float,
) -> float:
    """Return when one sample should be emitted relative to the loop start."""

    if rate is not None:
        return (position / rate) / speed
    return max(sample.elapsed_ns - first_elapsed_ns, 0) / NS_PER_SECOND / speed


def sleep_until(target_monotonic: float, *, deadline: float | None) -> None:
    """Sleep in short chunks so Ctrl-C and --seconds remain responsive."""

    while True:
        now = time.monotonic()
        wake_at = min(target_monotonic, deadline) if deadline is not None else target_monotonic
        remaining = wake_at - now
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


class StdoutSink:
    """Playback sink that prints the same line format as ``gametrak-stdout``."""

    def __init__(self, *, stdout: TextIO, mode: ValueMode, precision: int) -> None:
        self.stdout = stdout
        self.mode = mode
        self.precision = precision

    def send(self, report: GameTrakRawReport) -> None:
        print(format_stdout_line(report, mode=self.mode, precision=self.precision), file=self.stdout, flush=True)

    def close(self) -> None:
        return None


class OscSink:
    """Playback sink that sends OSC messages matching ``gametrak-osc``."""

    def __init__(self, *, host: str, port: int, normalized: bool, wekinator: bool) -> None:
        from pythonosc.udp_client import SimpleUDPClient

        self.client = SimpleUDPClient(host, port)
        self.normalized = normalized
        self.wekinator = wekinator

    def send(self, report: GameTrakRawReport) -> None:
        from gametrak.osc import build_osc_messages, send_osc_messages

        messages = build_osc_messages(
            report,
            include_raw=not self.normalized,
            include_normalized=self.normalized or self.wekinator,
            include_wekinator=self.wekinator,
        )
        send_osc_messages(self.client, messages)

    def close(self) -> None:
        return None


class MidiSink:
    """Playback sink that sends pitch bend messages matching ``gametrak-midi``."""

    def __init__(self, *, port_name: str, stderr: TextIO) -> None:
        from gametrak.midi import _open_midi_output

        self.output = _open_midi_output(port_name)
        print(f"gametrak-playback virtual MIDI output: {port_name}", file=stderr)

    def send(self, report: GameTrakRawReport) -> None:
        from gametrak.midi import _send_pitch_bends

        _send_pitch_bends(self.output, raw_tuple(report))

    def close(self) -> None:
        try:
            self.output.close()
        except Exception:
            pass


class WebSocketSink:
    """Playback sink that broadcasts JSON matching ``gametrak-ws``."""

    def __init__(self, *, host: str, port: int, normalized: bool, stderr: TextIO) -> None:
        from gametrak.ws import WebSocketJsonBroadcaster

        self.normalized = normalized
        self.broadcaster = WebSocketJsonBroadcaster(host, port, stderr=stderr)
        self.broadcaster.start()
        print(f"gametrak-playback WebSocket JSON: ws://{host}:{port}", file=stderr)

    def send(self, report: GameTrakRawReport) -> None:
        from gametrak.ws import build_ws_message

        self.broadcaster.broadcast(build_ws_message(report, normalized=self.normalized))

    def close(self) -> None:
        self.broadcaster.close()


def format_stdout_line(report: GameTrakRawReport, *, mode: ValueMode, precision: int) -> str:
    """Format playback stdout exactly like ``gametrak-stdout``."""

    if mode == "normalized":
        decimals = max(0, precision)
        return " ".join(f"{value:.{decimals}f}" for value in normalized_tuple(report))

    if mode == "hex":
        return " ".join(f"{value:0{HEX_WIDTH}X}" for value in raw_tuple(report))

    if mode == "0dec":
        return " ".join(f"{value:0{ZERO_DECIMAL_WIDTH}d}" for value in raw_tuple(report))

    return " ".join(str(value) for value in raw_tuple(report))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
