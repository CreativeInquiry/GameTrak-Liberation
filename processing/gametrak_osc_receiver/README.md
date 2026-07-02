# GameTrak OSC Receiver Processing Sketch

This Processing sketch receives raw GameTrak OSC data and visualizes the six
channels as bars and X/Y/R timelines. It is an OSC receiver only: it does not
open the GameTrak HID device directly.

The sketch has no external Processing-library dependency. It uses Java UDP
sockets and a small built-in OSC parser so it can run as-is in Processing.

It has been tested in Processing 4.3 and Processing 4.5.5 on macOS.

## Run

Start an OSC transmitter in another app:

```bash
gametrak-osc
```

or run the standalone Processing transmitter:

```text
processing/gametrak_osc_transmitter/
```

Then open and run:

```text
processing/gametrak_osc_receiver/gametrak_osc_receiver.pde
```

## Data Contract

The receiver listens on UDP port `2434` for:

```text
/gametrak/raw
  int left_x left_y left_r right_x right_y right_r buttons
```

Only the first six raw values are visualized. They are expected to be in the
GameTrak HID range `0..4095`.

The receiver ignores `/gametrak/norm`, Wekinator messages, and any other OSC
address. This keeps the display raw-first and avoids mixing normalized floats
with raw integer bars.

## Reset Button

The `Reset Device` button sends:

```text
127.0.0.1:2435
/gametrak/reconnect
```

That message asks a compatible bridge to close/reopen the HID handle and resend
the GameTrak init sequence. It works with `gametrak-osc`. It also works with the
Processing OSC transmitter, which listens on the same control port.

## Notes

If the sketch shows no packets, confirm that the transmitter is sending to UDP
port `2434`. `gametrak-osc` uses that port by default; with an explicit port:

```bash
gametrak-osc --host 127.0.0.1 --port 2434
```

Only one program can usually own the GameTrak HID handle at a time. Also, only
one receiver can normally bind a given local UDP port, so close other receivers
that are already listening on `2434`.
