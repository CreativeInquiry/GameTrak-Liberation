/**
 * GameTrak MIDI Receiver for p5.js 2.x.
 * Golan Levin, July 2026
 *
 * This p5.js sketch listens to the virtual MIDI port created by
 * `gametrak-midi`. The Python bridge sends the six GameTrak raw HID values
 * as signed MIDI pitch bend values on channels 1-6:
 *
 *   left_x left_y left_r right_x right_y right_r
 *
 * MIDI stores pitch bend on the wire as an unsigned 14-bit value centered at
 * 8192. WebMidi.js exposes that wire value as `event.rawValue`, so this sketch
 * subtracts 8192 to recover the signed pitch bend value. Because the bridge
 * sends raw GameTrak readings directly, the recovered value is the original
 * 0..4095 sensor value.
 */

const MIDI_PORT_HINT = "GameTrak MIDI";

// This order is the public MIDI channel contract. It matches the Python bridge's
// semantic raw tuple and the channel map documented in this sketch's README.
const LABELS = ["left_x", "left_y", "left_r", "right_x", "right_y", "right_r"];

// The display stores a bounded number of raw X/Y/R samples for each joystick.
// Histories are sampled from the most recent MIDI values at the p5 frame rate,
// not from individual MIDI messages, because one GameTrak HID report arrives
// as six separate pitch-bend messages.
const HISTORY_SIZE = 860;
const TIMELINE_AMPLITUDE = 60;
const TIMELINE_HEADROOM = 14;

// WebMIDI's raw pitch-bend value is the unsigned 14-bit wire value. The Python
// bridge sends signed pitch bend values 0..4095, so subtracting the center
// recovers the original GameTrak sensor reading.
const PITCH_BEND_CENTER = 8192;
const RAW_MIN = 0;
const RAW_MAX = 4095;
const RECONNECT_AFTER_MS = 1000;
const UI_FONT = "Arial";

let rawValues = [0, 0, 0, 0, 0, 0];
let hasData = false;
let messageCount = 0;
let lastMessageMillis = 0;
let statusText = "midi disabled";
let inputName = "";
let midiEnabled = false;
let midiListenersAttached = false;
let connectedInput = null;
let pitchBendListeners = [];

let leftXHistory = [];
let leftYHistory = [];
let leftRHistory = [];
let rightXHistory = [];
let rightYHistory = [];
let rightRHistory = [];
let lastHistoryMillis = 0;

/**
 * Create the fixed 1024x768 canvas used by the Processing reference sketch.
 */
function setup() {
  const canvas = createCanvas(1024, 768);
  canvas.parent("sketch-root");
  textFont(UI_FONT);
  frameRate(60);
}

/**
 * Render one frame from the latest MIDI values.
 */
function draw() {
  background(20);
  textFont(UI_FONT);
  sampleHistory();
  drawHeader();
  drawMidiButton();
  drawBars();
  drawTimelines();
}

/**
 * Handle the upper-right MIDI button.
 */
function mousePressed() {
  const button = midiButtonRect();
  if (shouldShowMidiButton() && mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h) {
    enableOrReconnectMidi();
  }
}

/**
 * Enable WebMIDI if needed, then connect to the preferred input.
 *
 * Chrome prompts for MIDI permission here. The button click provides a user
 * gesture, which keeps the permission flow predictable across browser builds.
 */
async function enableOrReconnectMidi() {
  try {
    statusText = midiEnabled ? "refreshing MIDI inputs" : "requesting MIDI access";
    if (!midiEnabled) {
      await WebMidi.enable();
      midiEnabled = true;
      attachMidiPortListeners();
    }
    connectPreferredInput();
  } catch (error) {
    statusText = `midi error: ${error.message || error}`;
  }
}

/**
 * Attach global WebMidi.js port-change listeners once.
 */
function attachMidiPortListeners() {
  if (midiListenersAttached) {
    return;
  }
  WebMidi.addListener("connected", connectPreferredInput);
  WebMidi.addListener("disconnected", connectPreferredInput);
  midiListenersAttached = true;
}

/**
 * Select the GameTrak virtual input, falling back to the first available input.
 */
function connectPreferredInput() {
  if (!midiEnabled) {
    return;
  }

  clearPitchBendListeners();

  const inputs = WebMidi.inputs || [];
  if (inputs.length === 0) {
    connectedInput = null;
    inputName = "";
    statusText = "no MIDI input";
    return;
  }

  // Prefer the bridge's virtual port, but stay useful during experiments with
  // renamed ports or other MIDI routing tools.
  connectedInput = inputs.find((input) => input.name.includes(MIDI_PORT_HINT)) || inputs[0];
  inputName = connectedInput.name;

  for (let channel = 1; channel <= 6; channel += 1) {
    const index = channel - 1;
    const listener = (event) => handlePitchBend(index, event);
    connectedInput.channels[channel].addListener("pitchbend", listener);
    pitchBendListeners.push({ channel, listener });
  }

  statusText = `listening to ${inputName}`;
}

/**
 * Remove previous pitch-bend listeners before switching MIDI inputs.
 */
function clearPitchBendListeners() {
  if (!connectedInput) {
    pitchBendListeners = [];
    return;
  }

  for (const entry of pitchBendListeners) {
    try {
      connectedInput.channels[entry.channel].removeListener("pitchbend", entry.listener);
    } catch (_error) {
      // Some WebMidi.js versions tolerate removing unknown listeners; some do
      // not. A failed removal here should not prevent reconnection.
    }
  }
  pitchBendListeners = [];
}

/**
 * Decode one pitch-bend event into a raw GameTrak value.
 *
 * @param {number} index semantic GameTrak channel index, 0..5
 * @param {object} event WebMidi.js pitch-bend event
 */
function handlePitchBend(index, event) {
  const rawPitchBend = readPitchBendRawValue(event);
  const signedPitchBend = rawPitchBend - PITCH_BEND_CENTER;
  rawValues[index] = constrain(Math.round(signedPitchBend), RAW_MIN, RAW_MAX);
  hasData = true;
  messageCount += 1;
  lastMessageMillis = millis();
}

/**
 * Read the unsigned 14-bit MIDI pitch-bend wire value from a WebMidi.js event.
 *
 * @param {object} event WebMidi.js pitch-bend event
 * @return {number} unsigned pitch-bend value, 0..16383
 */
function readPitchBendRawValue(event) {
  if (Number.isFinite(event.rawValue)) {
    return event.rawValue;
  }

  const data = event.message && event.message.data;
  if (data && data.length >= 3) {
    return data[1] | (data[2] << 7);
  }

  return PITCH_BEND_CENTER;
}

/**
 * Append the current X/Y/R values to the two timeline histories.
 */
function sampleHistory() {
  if (!hasData || millis() - lastHistoryMillis < 1000 / 60) {
    return;
  }

  pushHistory(leftXHistory, rawValues[0]);
  pushHistory(leftYHistory, rawValues[1]);
  pushHistory(leftRHistory, rawValues[2]);
  pushHistory(rightXHistory, rawValues[3]);
  pushHistory(rightYHistory, rawValues[4]);
  pushHistory(rightRHistory, rawValues[5]);
  lastHistoryMillis = millis();
}

/**
 * Append one value to a bounded display history.
 *
 * @param {number[]} history timeline value buffer
 * @param {number} value latest raw value
 */
function pushHistory(history, value) {
  history.push(value);
  if (history.length > HISTORY_SIZE) {
    history.shift();
  }
}

/**
 * Draw title and live MIDI telemetry.
 */
function drawHeader() {
  noStroke();
  fill(240);
  textSize(24);
  textAlign(LEFT, BASELINE);
  text("GameTrak MIDI p5.js v2", 40, 48);

  textSize(14);
  fill(170);
  const age = lastMessageMillis === 0 ? "-" : `${nf((millis() - lastMessageMillis) / 1000.0, 0, 2)}s`;
  text(`messages ${messageCount}   latest ${age}   ${statusText}`, 40, 76);
}

/**
 * Draw the upper-right MIDI enable/reconnect button.
 */
function drawMidiButton() {
  if (!shouldShowMidiButton()) {
    return;
  }

  const button = midiButtonRect();
  const hover = mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h;

  noStroke();
  fill(hover ? color(70, 120, 170) : color(54, 78, 104));
  rect(button.x, button.y, button.w, button.h, 4);

  fill(245);
  textSize(14);
  textAlign(CENTER, CENTER);
  text(midiEnabled ? "Reconnect MIDI" : "Enable MIDI", button.x + button.w * 0.5, button.y + button.h * 0.5);
  textAlign(LEFT, BASELINE);
}

/**
 * Decide whether the MIDI action button should be visible.
 *
 * The initial `Enable MIDI` button is required for Chrome's permission flow.
 * After messages are flowing, the button is hidden to keep the display quiet.
 * It comes back as `Reconnect MIDI` if no data has arrived recently.
 */
function shouldShowMidiButton() {
  if (!midiEnabled) {
    return true;
  }
  if (lastMessageMillis === 0) {
    return true;
  }
  return millis() - lastMessageMillis > RECONNECT_AFTER_MS;
}

/**
 * @return {{x: number, y: number, w: number, h: number}} MIDI button rectangle
 */
function midiButtonRect() {
  return { x: width - 180 - 40, y: 34, w: 180, h: 34 };
}

/**
 * Draw the six current raw values as horizontal bars.
 */
function drawBars() {
  const left = 190;
  const top = 126;
  const barW = 700;
  const barH = 32;
  const gap = 40;

  for (let i = 0; i < 6; i += 1) {
    const y = top + i * gap;
    const tether = i === 2 || i === 5;

    fill(220);
    textSize(16);
    textAlign(RIGHT, CENTER);
    text(LABELS[i], left - 20, y + barH * 0.5);

    noStroke();
    fill(45);
    rect(left, y, barW, barH, 4);

    drawRawBar(left, y, barW, barH, rawValues[i], tether);

    fill(230);
    textAlign(LEFT, CENTER);
    text(String(rawValues[i]), left + barW + 22, y + barH * 0.5);
  }

  textAlign(LEFT, BASELINE);
}

/**
 * Draw one raw value in bar form.
 *
 * Joystick axes draw from center outward. Tether axes are visually inverted
 * because the raw sensor value is high when the tether is retracted.
 */
function drawRawBar(x, y, w, h, value, tether) {
  const constrained = constrain(value, RAW_MIN, RAW_MAX);
  let mappedX = map(constrained, RAW_MIN, RAW_MAX, x, x + w);

  if (tether) {
    mappedX = map(constrained, RAW_MAX, RAW_MIN, x, x + w);
    fill(70, 210, 150);
    rect(x, y, mappedX - x, h, 4);
    return;
  }

  const center = x + w * 0.5;
  fill(mappedX >= center ? color(70, 150, 240) : color(240, 120, 90));
  rect(min(center, mappedX), y, abs(mappedX - center), h, 4);
  drawTick(center, y, h);
}

/**
 * Draw a vertical center tick for joystick bars.
 */
function drawTick(x, y, h) {
  stroke(210);
  strokeWeight(2);
  line(x, y - 5, x, y + h + 5);
  noStroke();
}

/**
 * Draw both joystick X/Y/R history panels.
 */
function drawTimelines() {
  const x = 80;
  const w = width - 140;
  drawTimelinePanel("Left", leftXHistory, leftYHistory, leftRHistory, x, 490, w);
  drawTimelinePanel("Right", rightXHistory, rightYHistory, rightRHistory, x, 650, w);
}

/**
 * Draw one joystick history panel.
 *
 * @param {string} title short side label, rendered vertically
 * @param {number[]} xValues raw joystick X history
 * @param {number[]} yValues raw joystick Y history
 * @param {number[]} rValues raw tether R history
 * @param {number} x left edge of the timeline plot area
 * @param {number} centerY raw midpoint line for the timeline
 * @param {number} w width of the timeline plot area
 */
function drawTimelinePanel(title, xValues, yValues, rValues, x, centerY, w) {
  const amp = TIMELINE_AMPLITUDE;
  const panelTop = centerY - amp - TIMELINE_HEADROOM - 18;
  const panelH = (amp + TIMELINE_HEADROOM) * 2 + 36;

  noStroke();
  fill(28);
  rect(x - 12, panelTop, w + 24, panelH, 4);

  stroke(80);
  strokeWeight(1);
  line(x, centerY, x + w, centerY);
  line(x, centerY - amp, x + w, centerY - amp);
  line(x, centerY + amp, x + w, centerY + amp);

  push();
  translate(x - 40, centerY);
  rotate(-HALF_PI);
  fill(210);
  textSize(16);
  textAlign(CENTER, CENTER);
  text(title, 0, 0);
  pop();

  fill(80, 190, 255);
  textSize(14);
  textAlign(LEFT, BASELINE);
  text("X", x + w - 72, panelTop - 8);
  fill(255, 120, 190);
  text("Y", x + w - 48, panelTop - 8);
  fill(70, 210, 150);
  text("R", x + w - 24, panelTop - 8);

  if (xValues.length < 2 || yValues.length < 2 || rValues.length < 2) {
    return;
  }

  drawTimelineWave(xValues, x, centerY, w, amp, color(80, 190, 255), 1);
  drawTimelineWave(yValues, x, centerY, w, amp, color(255, 120, 190), 1);
  drawTimelineWave(rValues, x, centerY, w, amp, color(70, 210, 150), 1);
}

/**
 * Draw one raw-value history as a polyline.
 *
 * @param {number[]} values timeline values
 * @param {number} x left edge of the timeline plot area
 * @param {number} centerY y-coordinate corresponding to raw midpoint
 * @param {number} w plot width
 * @param {number} amp vertical amplitude in pixels
 * @param {p5.Color} strokeColor wave color
 * @param {number} weight stroke weight in pixels
 */
function drawTimelineWave(values, x, centerY, w, amp, strokeColor, weight) {
  stroke(strokeColor);
  strokeWeight(weight);
  noFill();
  beginShape();
  for (let i = 0; i < values.length; i += 1) {
    const px = map(i, 0, HISTORY_SIZE - 1, x, x + w);
    const py = rawToTimelineY(values[i], centerY, amp);
    vertex(px, py);
  }
  endShape();
}

/**
 * Convert one 0..4095 raw reading into a timeline y-coordinate.
 *
 * @param {number} rawValue raw HID-scale value
 * @param {number} centerY y-coordinate for the midpoint
 * @param {number} amp positive/negative excursion in pixels
 * @return {number} screen y-coordinate, with larger raw values drawn higher
 */
function rawToTimelineY(rawValue, centerY, amp) {
  const constrained = constrain(rawValue, RAW_MIN, RAW_MAX);
  return map(constrained, RAW_MIN, RAW_MAX, centerY + amp, centerY - amp);
}

/**
 * Release WebMidi.js resources when the page is closed or reloaded.
 */
window.addEventListener("beforeunload", () => {
  if (midiEnabled) {
    WebMidi.disable();
  }
});
