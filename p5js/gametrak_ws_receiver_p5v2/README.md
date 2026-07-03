# GameTrak WebSocket Receiver for p5.js v2

This p5.js `2.3.0` sketch receives raw GameTrak values from `gametrak-ws`
over browser-native WebSocket JSON. It does not use MIDI, WebMIDI,
OSC-over-WebSocket, osc.js, serial, or Node.js.

This version loads p5.js `2.3.0`:

```html
<script src="https://cdn.jsdelivr.net/npm/p5@2.3.0/lib/p5.js"></script>
```

## Run

Install or upgrade the Python bridge, then start the WebSocket server:

```bash
cd path/to/GameTrak-Liberation/python
pipx install --force --python python3.10 .
gametrak-ws
```

If `gametrak-ws` is already installed, only the last command is needed. Run
`gametrak-ws --help` to see optional arguments such as `--host`, `--port`,
`--normalized`, `--rate`, `--print`, and `--diagnose`.

Open this file directly in Chrome:

```text
file:///path/to/GameTrak-Liberation/p5js/gametrak_ws_receiver_p5v2/index.html
```

The sketch connects to:

```text
ws://127.0.0.1:2436
```

No local HTTP server is required for this WebSocket receiver. If you prefer to
serve the static files with `python3 -m http.server`, that is also fine, but
port `8000` is only the page server; GameTrak data still arrives over WebSocket
port `2436`.

Use `Connect WS` or `Reconnect WS` if the WebSocket server is started after the
page loads.

## Data Contract

`gametrak-ws` sends one JSON text message per raw sample:

```json
{"address":"/gametrak/raw","args":[123,2048,4095,500,900,3900,0]}
```

The first six values are:

```text
left_x left_y left_r right_x right_y right_r
```

The seventh value is the decoded button bitfield. This sketch visualizes the
first six raw values in the GameTrak HID range `0..4095`.

Run `gametrak-ws` in its default raw mode for this sketch. The bridge also has
a `--normalized` mode, but this sketch ignores `/gametrak/normalized` messages.

## Files

| File | Purpose |
|---|---|
| `index.html` | Loads p5.js 2.3.0 and `sketch.js`. |
| `sketch.js` | Opens a browser WebSocket, decodes JSON messages, and visualizes raw values. |
