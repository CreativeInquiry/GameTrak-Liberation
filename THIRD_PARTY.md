# Third-Party Code, References, And Specifications

This file tracks the non-project sources used by this repository. It is not a
legal opinion and does not replace the license files or metadata shipped by the
projects listed here.

## Protocol / Reverse-Engineering Sources

### casiez/libgametrak

- Source: <https://github.com/casiez/libgametrak>
- Author: Géry Casiez
- Status in this repository: not redistributed.
- Use in this project: technical prior art for GameTrak HID behavior,
  especially VID/PID handling, 16-byte HID reads, and the optional PS2-mode
  init/keepalive sequence.

The PS2-mode sequence was the key prior-art detail:

```text
write "Gametrak"
write 45 23
periodically write 46 <evolving key>
```

The key-evolution function in `python/gametrak/ps2.py` and the Processing
`GameTrakDirectHid.java` helpers are derived from that observed libgametrak
behavior.

### Device-Derived HID Evidence

- Source: the attached In2Games GameTrak controller.
- Status in this repository: captured sample data is redistributed in
  `sample_reports/`.
- Use in this project: HID descriptor inspection, USB identity, report length,
  live report capture, and empirical axis mapping.

Tested device identity:

```text
USB vendor_id:  0x14B7
USB product_id: 0x0982
Product string: Game-Trak V1.3
Physical label: GameTrak V2.0
```

### Wekinator OSC Convention

- Source: <http://www.wekinator.org/>
- Status in this repository: not redistributed.
- Use in this project: optional OSC compatibility address only.

```text
/wekinator/control/inputs
```

No Wekinator GameTrak implementation was used to reverse-engineer HID reports.

## Technical Documentation / Specifications

These sources informed protocol formatting or API use. They are not
redistributed by this repository.

- USB HID concepts and the GameTrak's HID report descriptor.
- `hidapi` / Python `hidapi` API behavior:
  <https://github.com/libusb/hidapi> and
  <https://github.com/trezor/cython-hidapi>
- OSC packet structure:
  <https://opensoundcontrol.stanford.edu/spec-1_0.html>
- MIDI pitch bend representation and channel conventions:
  <https://midi.org/>
- WebMIDI API:
  <https://www.w3.org/TR/webmidi/>
- Processing documentation:
  <https://processing.org/reference/>
- p5.js documentation:
  <https://p5js.org/reference/>
- WebMidi.js documentation:
  <https://webmidijs.org/>

## Redistributed Binaries

The Processing sketches bundle Java dependencies in their `code/` folders so
Processing can load them without a separate install step.

| Component | Version | License | Redistributed files | Upstream |
|---|---:|---|---|---|
| hid4java | 0.8.0 | MIT | `processing/gametrak_standalone/code/hid4java-0.8.0.jar`; `processing/gametrak_osc_transmitter/code/hid4java-0.8.0.jar` | <https://github.com/gary-rowe/hid4java> |
| JNA | 5.14.0 | Apache-2.0 OR LGPL-2.1 | `processing/gametrak_standalone/code/jna-5.14.0.jar`; `processing/gametrak_osc_transmitter/code/jna-5.14.0.jar` | <https://github.com/java-native-access/jna> |

The JNA jar contains its own `META-INF/LICENSE`, `META-INF/AL2.0`, and
`META-INF/LGPL2.1` files. The hid4java jar contains Maven metadata identifying
the project and MIT license.

## Browser Libraries Loaded By CDN

These are not checked into this repository, but the p5.js examples load them
from jsDelivr.

| Component | Version | License | URL used by sketch | Upstream |
|---|---:|---|---|---|
| p5.js | 1.11.13 | LGPL-2.1 | `https://cdn.jsdelivr.net/npm/p5@1.11.13/lib/p5.js` in `p5js/gametrak_midi_receiver_p5v1/index.html` and `p5js/gametrak_ws_receiver_p5v1/index.html` | <https://github.com/processing/p5.js> |
| p5.js | 2.3.0 | LGPL-2.1 | `https://cdn.jsdelivr.net/npm/p5@2.3.0/lib/p5.js` in `p5js/gametrak_midi_receiver_p5v2/index.html` and `p5js/gametrak_ws_receiver_p5v2/index.html` | <https://github.com/processing/p5.js> |
| WebMidi.js | 3.1.16 | Apache-2.0 | `https://cdn.jsdelivr.net/npm/webmidi@3.1.16/dist/iife/webmidi.iife.js` | <https://github.com/djipco/webmidi> |

## Python Package Dependencies

These are direct dependencies declared by `python/pyproject.toml`. They are
installed by `pip`/`pipx`; they are not vendored in this repository.

| Component | Purpose | License metadata observed | Upstream |
|---|---|---|---|
| `hidapi` | Python HID access | BSD License and GPLv3 classifiers in package metadata | <https://github.com/trezor/cython-hidapi> |
| `python-osc` | OSC UDP client support | Unlicense / public domain text in package metadata | <https://github.com/attwad/python-osc> |
| `mido` | MIDI message API | MIT | <https://github.com/mido/mido> |
| `python-rtmidi` | Virtual MIDI output backend | MIT; wraps RtMidi, modified MIT | <https://github.com/SpotlightKid/python-rtmidi> |
| `rich` | CLI tables and diagnostics | MIT | <https://github.com/Textualize/rich> |

Development/test dependency:

| Component | Purpose | License metadata observed | Upstream |
|---|---|---|---|
| `pytest` | Python tests | project metadata should be checked from installed distribution before release | <https://github.com/pytest-dev/pytest> |

Package managers may also install transitive dependencies. In the local test
environment these included packages such as `markdown-it-py`, `pygments`,
`pluggy`, `packaging`, and `iniconfig`.

## Project Code Written Here

The OSC packet builders/parsers in the Processing sketches are small local
implementations based on the OSC 1.0 packet format. They do not copy oscP5 or
another Processing OSC library.

The WebMIDI receiver logic in `p5js/gametrak_midi_receiver_p5v1/sketch.js` and
`p5js/gametrak_midi_receiver_p5v2/sketch.js` is local project code using p5.js
and WebMidi.js APIs.

The WebSocket receiver logic in `p5js/gametrak_ws_receiver_p5v1/sketch.js` and
`p5js/gametrak_ws_receiver_p5v2/sketch.js` is local project code using p5.js
and the browser's native WebSocket API.
