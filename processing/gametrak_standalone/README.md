# GameTrak Standalone HID Processing Sketch

This sketch opens the GameTrak directly from Processing through `hid4java`.
It does not require `gametrak-osc`, `gametrak-stdout`, `gametrak-midi`, OSC,
or Python.

It has been tested in Processing 4.3 and Processing 4.5.5 on macOS.

## Dependencies

The required Java jars are bundled in this sketch's `code/` folder so
Processing can load them automatically:

| File | Purpose |
|---|---|
| `code/hid4java-0.8.0.jar` | Java wrapper around hidapi, including native hidapi binaries. |
| `code/jna-5.14.0.jar` | Java Native Access dependency used by hid4java. |

`hid4java` is documented at:

```text
https://github.com/gary-rowe/hid4java
```

## Run

Open and run this sketch in Processing:

```text
processing/gametrak_standalone/gametrak_standalone.pde
```

No Python process, OSC transmitter, or MIDI bridge should be running against
the GameTrak at the same time.

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

7. Displays the six raw `0..4095` values as bars and X/Y/R timelines.

## File Layout

| File | Purpose |
|---|---|
| `gametrak_standalone.pde` | Processing UI and visualization. It deliberately avoids hid4java types. |
| `GameTrakDirectHid.java` | Plain Java HID worker that owns hid4java imports, device open/read/write, init, keepalive, and decoding. |

The direct HID code is in a `.java` file because Processing 4.5.x's PDE
preprocessor can misparse methods that use external-library types such as
`HidDevice`. Plain `.java` files are compiled by Java without that preprocessor
step.

## Notes

Processing and hid4java must be the only active GameTrak HID client. Quit any
other gametrak app, if it is running, before running this sketch.

The `Reconnect HID` button appears only before reports arrive or after reports
go stale. During normal streaming it stays hidden.

The sketch uses report ID `0` for HID output writes. That is the normal hidapi
convention for unnumbered reports, but this direct Java path should still be
physically tested because the proven Python path writes through Python hidapi's
slightly different API.

Shutdown is deliberately single-threaded. The Processing `exit()` and
`dispose()` hooks both call one idempotent sketch shutdown helper, which asks
the HID worker to stop and waits briefly. The HID worker owns native
close/shutdown calls so macOS IOHID objects are not released from two Java
threads at once.
