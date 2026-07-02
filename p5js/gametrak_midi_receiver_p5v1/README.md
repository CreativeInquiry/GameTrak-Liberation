# GameTrak MIDI Simple Receiver for p5.js v1

This p5.js (v.1.11.13) sketch receives GameTrak values from `gametrak-midi` through
Chrome's WebMIDI API. It is a browser-side counterpart to the Processing OSC
sketch, but it does not need OSC, serial, or a Node.js relay.

## Run

Install or upgrade the Python bridge, then start the virtual MIDI output:

```bash
cd ~/Desktop/gametrak/python
pipx install --force --python python3.10 .
gametrak-midi
```

If `gametrak-midi` is already installed, only the last command is needed.
Run `gametrak-midi --help` to see optional arguments such as `--rate`,
`--port-name`, `--print`, and `--diagnose`.

Serve the repository over localhost:

```bash
cd ~/Desktop/gametrak
python3 -m http.server 8000
```

Open this URL in Chrome:

```text
http://127.0.0.1:8000/p5js/gametrak_midi_receiver_p5v1/
```

Click `Enable MIDI` and allow Chrome's MIDI permission prompt. The sketch will
prefer the input named `GameTrak MIDI`, falling back to the first available
MIDI input if that exact name is not present.

The MIDI button is contextual. It is visible as `Enable MIDI` before browser
permission has been granted, hidden while messages are arriving, and shown again
as `Reconnect MIDI` if no message has arrived for more than one second.

## Data Contract

`gametrak-midi` sends the six raw GameTrak values as signed MIDI pitch bend
values on channels 1-6:

| MIDI channel | GameTrak value | Range |
|---:|---|---|
| 1 | `left_x` | `0..4095` |
| 2 | `left_y` | `0..4095` |
| 3 | `left_r` | `0..4095` |
| 4 | `right_x` | `0..4095` |
| 5 | `right_y` | `0..4095` |
| 6 | `right_r` | `0..4095` |

WebMIDI exposes pitch bend's unsigned wire value as `event.rawValue`. The
sketch subtracts `8192` to recover the signed pitch bend value, which is the
original GameTrak raw value:

```js
const rawGameTrakValue = event.rawValue - 8192;
```

The sketch displays the six current raw values as bars and keeps two scrolling
timelines for joystick X/Y/R motion.

## Files

| File | Purpose |
|---|---|
| `index.html` | Loads p5.js 1.11.13, WebMidi.js, and `sketch.js`. |
| `sketch.js` | Handles MIDI permission, input selection, pitch-bend decoding, and visualization. |

## Troubleshooting

If Chrome shows no MIDI input, make sure `gametrak-midi` is still running.
The virtual MIDI port exists only while that command is active.

If Chrome shows the wrong MIDI input, restart the bridge with a known port
name and update `MIDI_PORT_HINT` in `sketch.js` if necessary:

```bash
gametrak-midi --port-name "GameTrak MIDI"
```

If the sketch is opened with `file://`, use the localhost server instead.
WebMIDI is a browser permission API and is most reliable from `http://127.0.0.1`
or another secure context.

If the displayed values do not move, click `Reconnect MIDI` after starting or
restarting `gametrak-midi`.
