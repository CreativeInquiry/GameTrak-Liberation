"""Bridge live GameTrak HID reports to MIDI pitch bend.

``gametrak-midi`` creates a virtual MIDI output port. Chrome sees that port
as a MIDI input, so browser sketches can receive GameTrak data through the Web
MIDI API without a Node.js, OSC, or serial relay process.

The bridge sends one MIDI pitch bend message on each of channels 1 through 6:

``left_x left_y left_r right_x right_y right_r``

The GameTrak's raw HID values are already small enough to fit inside MIDI pitch
bend's signed range, so they are sent unchanged as pitch bend values
``0..4095``. A browser-side WebMIDI listener can recover that signed value from
the unsigned wire value by subtracting the pitch bend center, ``8192``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys
import time
from typing import TextIO

from gametrak.constants import EXPECTED_INPUT_REPORT_SIZE
from gametrak.diagnose import diagnostic_report
from gametrak.hid_backend import enumerate_gametrak_devices, open_gametrak, select_gametrak_device
from gametrak.ps2 import send_ps2_init, send_ps2_keepalive
from gametrak.report import decode_report, values_are_in_descriptor_range
from gametrak.values import raw_tuple

# GameTrak HID axis values are 12-bit unsigned readings published by the HID
# descriptor. This bridge deliberately keeps those values raw; calibration,
# smoothing, deadband, and coordinate transforms belong in the receiving app.
RAW_AXIS_MIN = 0
RAW_AXIS_MAX = 4095

# MIDI pitch bend is transmitted as an unsigned 14-bit value, but most music
# APIs expose it as a signed bend centered around zero. mido follows that signed
# convention, while WebMidi.js also exposes the raw unsigned wire value.
MIDI_PITCH_BEND_RAW_MIN = 0
MIDI_PITCH_BEND_RAW_MAX = 16383
MIDO_PITCH_CENTER = 8192

MIDI_CHANNEL_COUNT = 6
DEFAULT_PORT_NAME = "GameTrak MIDI"
CHANNEL_MAP = (
    "left_x",
    "left_y",
    "left_r",
    "right_x",
    "right_y",
    "right_r",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the MIDI bridge."""

    parser = argparse.ArgumentParser(
        description="Stream GameTrak values as pitch bend on MIDI channels 1-6.",
        epilog=(
            "MIDI clients should select the virtual MIDI "
            f"input named {DEFAULT_PORT_NAME!r} unless --port-name is changed. "
            "Pitch bend channels 1-6 carry raw values in this order: "
            f"{' '.join(CHANNEL_MAP)}."
        ),
    )
    parser.add_argument("--port-name", default=DEFAULT_PORT_NAME, help="virtual MIDI output port name")
    parser.add_argument("--rate", type=float, default=0.0, help="maximum output rate in Hz; default sends every report")
    parser.add_argument("--seconds", type=float, default=None, help="optional run duration for testing")
    parser.add_argument("--print", action="store_true", help="also print sent raw values to stderr")
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


def raw_axis_to_pitch_bend_raw(raw_value: int) -> int:
    """Return the unsigned MIDI pitch bend wire value for one raw axis.

    The public bridge contract is signed pitch bend ``0..4095``. MIDI stores
    pitch bend on the wire as unsigned center-offset data, so the raw wire value
    is ``8192..12287``.
    """

    return raw_axis_to_mido_pitch(raw_value) + MIDO_PITCH_CENTER


def raw_axis_to_mido_pitch(raw_value: int) -> int:
    """Convert one raw value to mido's signed pitchwheel API range.

    The MIDI wire value is unsigned ``0..16383``. mido exposes pitch bend as a
    signed value, ``-8192..8191``. GameTrak values are sent directly in the
    positive part of that signed range: ``0..4095``.
    """

    return max(RAW_AXIS_MIN, min(RAW_AXIS_MAX, int(raw_value)))


def pitch_bend_raw_to_raw_axis(pitch_bend_raw: int) -> int:
    """Read a browser WebMIDI raw pitch bend value as a GameTrak raw axis."""

    signed_pitch = int(pitch_bend_raw) - MIDO_PITCH_CENTER
    return max(RAW_AXIS_MIN, min(RAW_AXIS_MAX, signed_pitch))


def build_pitch_bend_messages(raw_values: Sequence[int]) -> list[tuple[int, int]]:
    """Return ``(mido_channel, pitch)`` pairs for the six semantic channels.

    mido uses zero-based channel numbers, so channel ``0`` here is the MIDI
    channel shown to users as channel 1.
    """

    if len(raw_values) != MIDI_CHANNEL_COUNT:
        raise ValueError(f"expected {MIDI_CHANNEL_COUNT} raw values, got {len(raw_values)}")
    return [(channel, raw_axis_to_mido_pitch(raw_value)) for channel, raw_value in enumerate(raw_values)]


def run(args: argparse.Namespace, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run the HID-to-MIDI stream until interrupted or deadline is reached.

    The MIDI output is opened once and held across HID reconnects. If the
    GameTrak stops reporting, the HID handle is closed and reopened, but the
    virtual MIDI port name stays stable for Chrome.
    """

    if args.diagnose:
        print(diagnostic_report(open_mode=args.open_mode, path=args.path), file=stdout, end="")
        return 0

    min_send_interval = 1.0 / args.rate if args.rate and args.rate > 0 else 0.0
    read_timeout_ms = max(int(args.read_timeout_ms), 0)
    deadline = time.monotonic() + args.seconds if args.seconds is not None else None
    total_sent = 0

    midi_output = _open_midi_output(args.port_name)
    print(f"gametrak-midi virtual MIDI output: {args.port_name}", file=stderr)
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
                    midi_output=midi_output,
                    stderr=stderr,
                    min_send_interval=min_send_interval,
                    read_timeout_ms=read_timeout_ms,
                    ps2_key=ps2_key,
                    deadline=deadline,
                )
                if deadline is not None:
                    break
            except KeyboardInterrupt:
                print("gametrak-midi interrupted", file=stderr)
                break
            except Exception as exc:
                print(f"gametrak-midi error: {exc}", file=stderr)
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
        try:
            midi_output.close()
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


def _open_midi_output(port_name: str) -> object:
    """Create the virtual MIDI output port consumed by MIDI clients.

    RtMidi names this an output because data leaves this process. Chrome sees
    the same virtual endpoint as a MIDI input.
    """

    try:
        import mido
    except ImportError as exc:
        raise RuntimeError(
            "gametrak-midi requires mido and python-rtmidi. "
            "Reinstall from the python directory with: "
            "pipx install --force --python python3.10 ."
        ) from exc

    try:
        mido.set_backend("mido.backends.rtmidi")
        return mido.open_output(port_name, virtual=True)
    except Exception as exc:
        raise RuntimeError(f"could not create virtual MIDI port {port_name!r}: {exc}") from exc


def _stream_device(
    args: argparse.Namespace,
    device: object,
    *,
    midi_output: object,
    stderr: TextIO,
    min_send_interval: float,
    read_timeout_ms: int,
    ps2_key: int | None,
    deadline: float | None,
) -> int:
    """Stream valid HID reports from one open handle to the MIDI output port.

    Malformed reports and out-of-descriptor values are ignored rather than sent
    as MIDI. That keeps browser sketches from seeing transient junk values if a
    read is short or a device reconnect happens mid-report.
    """

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

        raw_values = raw_tuple(report)
        _send_pitch_bends(midi_output, raw_values)
        if args.print:
            print(" ".join(str(value) for value in raw_values), file=stderr, flush=True)
        sent_count += 1
        next_send_at = now + min_send_interval

    return sent_count


def _send_pitch_bends(midi_output: object, raw_values: Sequence[int]) -> None:
    """Send one pitch bend message per GameTrak channel.

    mido channel numbers are zero-based; the first tuple from
    ``build_pitch_bend_messages`` is displayed by MIDI clients as channel 1.
    """

    import mido

    for channel, pitch in build_pitch_bend_messages(raw_values):
        midi_output.send(mido.Message("pitchwheel", channel=channel, pitch=pitch))


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
