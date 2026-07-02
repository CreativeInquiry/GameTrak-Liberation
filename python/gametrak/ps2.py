"""libgametrak-compatible output reports needed to start streaming.

On this tested GameTrak, opening the HID device is not enough to receive sensor
reports. The device starts streaming only after a small output-report sequence
used by libgametrak's optional ps2mode path.

The sequence is:
- write the ASCII bytes ``Gametrak``;
- write ``45 23``;
- periodically write ``46 <key>`` with an evolving key byte.
"""

from __future__ import annotations

from collections.abc import Callable

PS2_INIT_TEXT = b"Gametrak"
PS2_INITIAL_KEY = 0x23


def ps2_next_key(key: int) -> int:
    """Return the next libgametrak ps2mode key value.

    The low byte of this evolving integer is sent in periodic keepalive packets.
    """

    # This bit-mixing sequence is copied from libgametrak's ps2mode path.
    # It is not cryptographic; the device appears to expect this evolving
    # one-byte key in periodic 0x46 keepalive writes.
    ret = 0
    ret |= 0x000001 & key
    ret |= 0x000002 & ((key >> 15) ^ (key >> 16))
    ret |= 0x0000FC & ((key << 2) ^ (key >> 16))
    ret |= 0xFFFF00 & (key << 7)
    return ret


def send_ps2_init(
    device: object,
    read_size: int,
    *,
    log: Callable[[str], None] | None = None,
) -> int:
    """Send libgametrak's optional ps2mode reset/init sequence.

    The currently tested GameTrak setup does not stream input reports until
    this sequence is sent. The returned key should be used for later periodic
    keepalive writes.

    Args:
        device: Open hidapi device object.
        read_size: Number of bytes to read while draining init responses.
        log: Optional callback for diagnostic strings.

    Returns:
        The next ps2 key value to use for keepalive writes.
    """

    key = PS2_INITIAL_KEY

    # These short reads deliberately drain whatever the device returns around
    # the init writes. On this unit the first read after "Gametrak" can be an
    # echo-like packet rather than a normal sensor report.
    pre_read = device.read(read_size, 10)
    _log(log, f"Pre-init 10ms read length: {len(pre_read)}")

    text_result = device.write(PS2_INIT_TEXT)
    _log(log, f"Write {PS2_INIT_TEXT!r}: result={text_result}")

    post_read = device.read(read_size, 10)
    _log(log, f"Post-text 10ms read length: {len(post_read)}")

    packet = bytes((0x45, key & 0xFF))
    packet_result = device.write(packet)
    _log(log, f"Write ps2 key packet {packet.hex()}: result={packet_result}")
    return ps2_next_key(key)


def send_ps2_keepalive(device: object, key: int, *, log: Callable[[str], None] | None = None) -> int:
    """Send one periodic ps2mode keepalive packet and return the next key."""

    packet = bytes((0x46, key & 0xFF))
    result = device.write(packet)
    _log(log, f"Write periodic ps2 key packet {packet.hex()}: result={result}")
    return ps2_next_key(key)


def _log(log: Callable[[str], None] | None, message: str) -> None:
    """Call a diagnostic logger if one was provided."""

    if log is not None:
        log(message)
