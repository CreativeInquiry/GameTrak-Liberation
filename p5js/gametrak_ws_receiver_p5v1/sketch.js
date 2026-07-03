/**
 * GameTrak WebSocket Receiver for p5.js 1.x.
 * Golan Levin, July 2026
 *
 * This sketch receives WebSocket JSON from `gametrak-ws`. Each message is
 * shaped like the raw OSC payload used elsewhere in this repo:
 *
 *   {"address":"/gametrak/raw","args":[left_x,left_y,left_r,right_x,right_y,right_r,buttons]}
 */

const WS_URL = "ws://127.0.0.1:2436";
const LABELS = ["left_x", "left_y", "left_r", "right_x", "right_y", "right_r"];
const HISTORY_SIZE = 860;
const TIMELINE_AMPLITUDE = 60;
const TIMELINE_HEADROOM = 14;
const RAW_MIN = 0;
const RAW_MAX = 4095;
const RECONNECT_AFTER_MS = 1000;
const UI_FONT = "Arial";

let rawValues = [0, 0, 0, 0, 0, 0];
let hasData = false;
let messageCount = 0;
let lastMessageMillis = 0;
let setupMillis = 0;
let statusText = "ws disconnected";
let wsConnected = false;
let socket = null;

let leftXHistory = [];
let leftYHistory = [];
let leftRHistory = [];
let rightXHistory = [];
let rightYHistory = [];
let rightRHistory = [];
let lastHistoryMillis = 0;

function setup() {
  const canvas = createCanvas(1024, 768);
  canvas.parent("sketch-root");
  textFont(UI_FONT);
  frameRate(60);
  setupMillis = millis();
  connectWebSocket();
}

function draw() {
  background(20);
  textFont(UI_FONT);
  sampleHistory();
  drawHeader();
  drawWsButton();
  drawBars();
  drawTimelines();
}

function mousePressed() {
  const button = wsButtonRect();
  if (shouldShowWsButton() && mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h) {
    connectWebSocket();
  }
}

function connectWebSocket() {
  closeWebSocket();
  statusText = "connecting";
  const nextSocket = new WebSocket(WS_URL);
  socket = nextSocket;

  nextSocket.onopen = () => {
    if (socket !== nextSocket) {
      return;
    }
    wsConnected = true;
    statusText = `connected ${WS_URL}`;
  };

  nextSocket.onmessage = (event) => {
    if (socket !== nextSocket) {
      return;
    }
    handleWsMessage(event.data);
  };

  nextSocket.onerror = () => {
    if (socket !== nextSocket) {
      return;
    }
    statusText = "ws error";
  };

  nextSocket.onclose = () => {
    if (socket !== nextSocket) {
      return;
    }
    wsConnected = false;
    statusText = "ws closed";
  };
}

function closeWebSocket() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    socket.close();
  }
  socket = null;
  wsConnected = false;
}

function handleWsMessage(data) {
  let message;
  try {
    message = JSON.parse(data);
  } catch (_error) {
    statusText = "bad json";
    return;
  }

  if (message.address !== "/gametrak/raw" || !Array.isArray(message.args) || message.args.length < 6) {
    statusText = "unexpected message";
    return;
  }

  for (let i = 0; i < 6; i += 1) {
    rawValues[i] = constrain(Math.round(Number(message.args[i]) || 0), RAW_MIN, RAW_MAX);
  }
  hasData = true;
  messageCount += 1;
  lastMessageMillis = millis();
  statusText = `connected ${WS_URL}`;
}

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

function pushHistory(history, value) {
  history.push(value);
  if (history.length > HISTORY_SIZE) {
    history.shift();
  }
}

function drawHeader() {
  noStroke();
  fill(240);
  textSize(24);
  textAlign(LEFT, BASELINE);
  text("GameTrak (via WebSocket) for p5.js v1", 40, 48);

  textSize(14);
  fill(170);
  const age = lastMessageMillis === 0 ? "-" : `${nf((millis() - lastMessageMillis) / 1000.0, 0, 2)}s`;
  text(`messages ${messageCount}   latest ${age}   ${statusText}`, 40, 76);
}

function drawWsButton() {
  if (!shouldShowWsButton()) {
    return;
  }

  const button = wsButtonRect();
  const hover = mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h;

  noStroke();
  fill(hover ? color(70, 120, 170) : color(54, 78, 104));
  rect(button.x, button.y, button.w, button.h, 4);

  fill(245);
  textSize(14);
  textAlign(CENTER, CENTER);
  text(wsConnected ? "Reconnect WS" : "Connect WS", button.x + button.w * 0.5, button.y + button.h * 0.5);
  textAlign(LEFT, BASELINE);
}

function shouldShowWsButton() {
  if (!wsConnected) {
    return true;
  }
  if (lastMessageMillis === 0) {
    return millis() - setupMillis > RECONNECT_AFTER_MS;
  }
  return millis() - lastMessageMillis > RECONNECT_AFTER_MS;
}

function wsButtonRect() {
  return { x: width - 160 - 40, y: 34, w: 160, h: 34 };
}

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

function drawTick(x, y, h) {
  stroke(210);
  strokeWeight(2);
  line(x, y - 5, x, y + h + 5);
  noStroke();
}

function drawTimelines() {
  const x = 80;
  const w = width - 140;
  drawTimelinePanel("Left", leftXHistory, leftYHistory, leftRHistory, x, 490, w);
  drawTimelinePanel("Right", rightXHistory, rightYHistory, rightRHistory, x, 650, w);
}

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

function rawToTimelineY(rawValue, centerY, amp) {
  const constrained = constrain(rawValue, RAW_MIN, RAW_MAX);
  return map(constrained, RAW_MIN, RAW_MAX, centerY + amp, centerY - amp);
}

window.addEventListener("beforeunload", () => {
  closeWebSocket();
});
