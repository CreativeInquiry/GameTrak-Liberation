# GameTrak OSC Transmitter Processing Sketch

This is an **HID-to-OSC bridge**. The sketch opens the GameTrak directly from Processing through `hid4java` and transmits raw GameTrak values over OSC. It does not require Python or any other software.

It has been tested in Processing 4.3 and Processing 4.5.5 on macOS.

Default OSC output:

```text
127.0.0.1:2434
/gametrak/raw
  int left_x left_y left_r right_x right_y right_r buttons
```

Default OSC control input:

```text
127.0.0.1:2435

/gametrak/init
  Resend the libgametrak-style init sequence on the current HID handle.

/gametrak/reconnect
  Close/reopen HID, then resend init.
```

## Dependencies

The required Java jars are bundled in this sketch's `code/` folder so
Processing can load them automatically:

| File | Purpose |
|---|---|
| `code/hid4java-0.8.0.jar` | Java wrapper around hidapi, including native hidapi binaries. |
| `code/jna-5.14.0.jar` | Java Native Access dependency used by hid4java. |

## Run

Open and run this sketch in Processing:

```text
processing/gametrak_osc_transmitter/gametrak_osc_transmitter.pde
```

To see the OSC output immediately, also run the Processing OSC receiver:

```text
processing/gametrak_osc_receiver/gametrak_osc_receiver.pde
```

## What The Sketch Does

1. Enumerates HID devices with VID `0x14B7` and PID `0x0982`.
2. Prefers the Generic Desktop / Joystick collection.
3. Opens the selected HID path.
4. Sends the same libgametrak-style init sequence used by the Python tools.
5. Reads 16-byte HID input reports.
6. Decodes the first six little-endian 16-bit fields as:

```text
left_x left_y left_r right_x right_y right_r
```

7. Sends each valid report as OSC `/gametrak/raw`.
8. Listens for `/gametrak/init` and `/gametrak/reconnect` control messages.
9. Displays the six raw `0..4095` values as bars and X/Y/R timelines.

## File Layout

| File | Purpose |
|---|---|
| `gametrak_osc_transmitter.pde` | Processing UI and visualization. It deliberately avoids hid4java types. |
| `GameTrakDirectHid.java` | Plain Java HID worker that owns hid4java imports, device open/read/write, init, keepalive, decoding, and OSC send. |

## Notes

This sketch owns the GameTrak HID handle and binds UDP control port `2435`.
Quit `gametrak-osc`, `gametrak-stdout`, `gametrak-midi`, `gametrak-record`, and
the standalone HID sketch before running it.

The sketch has a `Reconnect HID` button that appears before data arrives or
after reports go stale. It also listens for `/gametrak/init` and
`/gametrak/reconnect` over OSC so another app can request the same reset flow.
