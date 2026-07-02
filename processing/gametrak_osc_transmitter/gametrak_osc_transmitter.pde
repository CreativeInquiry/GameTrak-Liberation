/**
 * GameTrak HID-to-OSC Transmitter
 * Golan Levin, July 2026
 *
 * <p>This Processing sketch talks to the GameTrak directly through hid4java,
 * sends the libgametrak-style init sequence, and transmits each valid HID
 * report as an OSC {@code /gametrak/raw} message.</p>
 */

final String OSC_HOST = "127.0.0.1";
final int OSC_PORT = 2434;
final String CONTROL_HOST = "127.0.0.1";
final int CONTROL_PORT = 2435;
final float RESET_BUTTON_W = 150;
final float RESET_BUTTON_H = 34;
final String[] LABELS = {
  "left_x", "left_y", "left_r", "right_x", "right_y", "right_r"
};
final int HISTORY_SIZE = 860;
final int RECONNECT_AFTER_MS = 1000;
final float TIMELINE_AMPLITUDE = 60;
final float TIMELINE_HEADROOM = 14;

GameTrakDirectHid gameTrak;
boolean gameTrakStopped = false;

int[][] history = new int[6][HISTORY_SIZE];
int historyCount = 0;
int lastHistoryReportCount = 0;

/**
 * Configure the Processing canvas.
 */
public void settings() {
  size(1024, 768);
}

/**
 * Initialize the UI and start the direct HID reader.
 */
public void setup() {
  surface.setTitle("GameTrak HID-to-OSC Transmitter");
  textFont(createFont("Helvetica", 16));
  frameRate(60);
  registerMethod("dispose", this);

  gameTrak = new GameTrakDirectHid(OSC_HOST, OSC_PORT, CONTROL_HOST, CONTROL_PORT);
  gameTrak.start();
}

/**
 * Render one animation frame from the latest HID state.
 */
public void draw() {
  background(20);

  int[] rawSnapshot = new int[6];
  gameTrak.copyRawValues(rawSnapshot);

  int reports = gameTrak.getReportCount();
  if (reports != lastHistoryReportCount) {
    appendHistory(rawSnapshot);
    lastHistoryReportCount = reports;
  }

  drawHeader(
    gameTrak.getStatus(),
    gameTrak.getDeviceSummary(),
    gameTrak.getOscSummary(),
    gameTrak.getControlSummary(),
    reports,
    gameTrak.getOscPacketCount(),
    gameTrak.getControlPacketCount(),
    gameTrak.getLastReportMillis()
  );
  drawReconnectButton();
  drawBars(rawSnapshot);
  drawTimelines(history, historyCount);
}

/**
 * Stop HID before Processing begins tearing down the sketch.
 */
public void exit() {
  shutdownGameTrak();
  super.exit();
}

/**
 * Release HID resources when Processing disposes the sketch.
 */
public void dispose() {
  shutdownGameTrak();
}

/**
 * Idempotent shutdown hook shared by exit() and dispose().
 */
void shutdownGameTrak() {
  if (gameTrakStopped) {
    return;
  }
  gameTrakStopped = true;
  if (gameTrak != null) {
    gameTrak.stop();
  }
}

/**
 * Draw the title and connection telemetry.
 *
 * @param currentStatus human-readable HID status
 * @param currentDeviceSummary selected HID device summary
 * @param oscStatus OSC destination or latest OSC error
 * @param controlStatus OSC control listener status
 * @param reports count of valid decoded HID reports
 * @param oscPackets count of transmitted OSC packets
 * @param controlPackets count of recognized OSC control packets
 * @param lastSeen wall-clock timestamp for the latest report
 */
void drawHeader(String currentStatus, String currentDeviceSummary, String oscStatus, String controlStatus, int reports, int oscPackets, int controlPackets, long lastSeen) {
  noStroke();
  fill(240);
  textSize(24);
  textAlign(LEFT, BASELINE);
  text("GameTrak HID-to-OSC Transmitter", 40, 48);

  textSize(14);
  fill(170);
  String age = "-";
  if (lastSeen != 0) {
    age = nf((System.currentTimeMillis() - lastSeen) / 1000.0f, 0, 2) + "s";
  }
  text("reports " + reports + "   osc " + oscPackets + "   control " + controlPackets + "   latest " + age + "   " + currentStatus, 40, 76);
  text(oscStatus, 40, 100);
  text(controlStatus, 40, 124);
  if (currentDeviceSummary.length() > 0) {
    text(currentDeviceSummary, 40, 148);
  }
}

/**
 * Draw the button that asks the HID helper to close and reopen the device.
 */
void drawReconnectButton() {
  if (!shouldShowReconnectButton()) {
    return;
  }

  float x = reconnectButtonX();
  float y = reconnectButtonY();
  boolean hover = mouseX >= x && mouseX <= x + RESET_BUTTON_W && mouseY >= y && mouseY <= y + RESET_BUTTON_H;

  noStroke();
  if (hover) {
    fill(70, 120, 170);
  } else {
    fill(54, 78, 104);
  }
  rect(x, y, RESET_BUTTON_W, RESET_BUTTON_H, 4);

  fill(245);
  textSize(14);
  textAlign(CENTER, CENTER);
  text("Reconnect HID", x + RESET_BUTTON_W * 0.5f, y + RESET_BUTTON_H * 0.5f);
  textAlign(LEFT, BASELINE);
}

float reconnectButtonX() {
  return width - RESET_BUTTON_W - 40;
}

float reconnectButtonY() {
  return 34;
}

/**
 * Handle reconnect-button clicks.
 */
public void mousePressed() {
  if (!shouldShowReconnectButton()) {
    return;
  }

  float x = reconnectButtonX();
  float y = reconnectButtonY();
  if (mouseX >= x && mouseX <= x + RESET_BUTTON_W && mouseY >= y && mouseY <= y + RESET_BUTTON_H) {
    gameTrak.requestReconnect();
  }
}

/**
 * Show reconnect only before data arrives or after reports go stale.
 */
boolean shouldShowReconnectButton() {
  if (gameTrak == null) {
    return false;
  }
  if (gameTrak.getReportCount() == 0) {
    return true;
  }
  long lastSeen = gameTrak.getLastReportMillis();
  if (lastSeen == 0) {
    return true;
  }
  return System.currentTimeMillis() - lastSeen > RECONNECT_AFTER_MS;
}

/**
 * Draw the six current raw values as horizontal bars.
 */
void drawBars(int[] rawSnapshot) {
  float left = 190;
  float top = 178;
  float barW = 700;
  float barH = 32;
  float gap = 40;

  for (int i = 0; i < 6; i++) {
    float y = top + i * gap;
    boolean tether = i == 2 || i == 5;

    fill(220);
    textSize(16);
    textAlign(RIGHT, CENTER);
    text(LABELS[i], left - 20, y + barH * 0.5f);

    noStroke();
    fill(45);
    rect(left, y, barW, barH, 4);

    drawRawBar(left, y, barW, barH, rawSnapshot[i], tether);

    fill(230);
    textAlign(LEFT, CENTER);
    text(str(rawSnapshot[i]), left + barW + 22, y + barH * 0.5f);
  }

  textAlign(LEFT, BASELINE);
}

/**
 * Draw both joystick X/Y/R history panels.
 */
void drawTimelines(int[][] historySnapshot, int count) {
  float x = 80;
  float w = width - 140;

  drawTimelineLegend();
  drawTimelinePanel("Left", historySnapshot[0], historySnapshot[1], historySnapshot[2], count, x, 490, w);
  drawTimelinePanel("Right", historySnapshot[3], historySnapshot[4], historySnapshot[5], count, x, 650, w);
}

/**
 * Draw one joystick history panel.
 */
void drawTimelinePanel(String title, int[] xValues, int[] yValues, int[] rValues, int count, float x, float centerY, float w) {
  float amp = TIMELINE_AMPLITUDE;
  float panelTop = centerY - amp - TIMELINE_HEADROOM - 18;
  float panelH = (amp + TIMELINE_HEADROOM) * 2 + 36;

  noStroke();
  fill(28);
  rect(x - 12, panelTop, w + 24, panelH, 4);

  stroke(80);
  strokeWeight(1);
  line(x, centerY, x + w, centerY);
  line(x, centerY - amp, x + w, centerY - amp);
  line(x, centerY + amp, x + w, centerY + amp);

  pushMatrix();
  translate(x - 40, centerY);
  rotate(-HALF_PI);
  fill(210);
  textSize(16);
  textAlign(CENTER, CENTER);
  text(title, 0, 0);
  popMatrix();

  if (count < 2) {
    return;
  }

  drawTimelineWave(xValues, count, x, centerY, w, amp, color(80, 190, 255));
  drawTimelineWave(yValues, count, x, centerY, w, amp, color(255, 120, 190));
  drawTimelineWave(rValues, count, x, centerY, w, amp, color(70, 210, 150));
}

/**
 * Draw one shared legend for timeline wave colors.
 */
void drawTimelineLegend() {
  float cx = 40;
  float cy = 425;
  float gap = 18;

  textSize(14);
  textAlign(CENTER, CENTER);
  noStroke();
  fill(80, 190, 255);
  text("X", cx - gap, cy);
  fill(255, 120, 190);
  text("Y", cx, cy);
  fill(70, 210, 150);
  text("R", cx + gap, cy);
  textAlign(LEFT, BASELINE);
}

/**
 * Draw one raw-value history as a polyline.
 */
void drawTimelineWave(int[] vals, int count, float x, float centerY, float w, float amp, int strokeColor) {
  stroke(strokeColor);
  strokeWeight(1);
  noFill();
  beginShape();
  for (int i = 0; i < count; i++) {
    float px = map(i, 0, HISTORY_SIZE - 1, x, x + w);
    float py = rawToTimelineY(vals[i], centerY, amp);
    vertex(px, py);
  }
  endShape();
}

float rawToTimelineY(int rawValue, float centerY, float amp) {
  int constrained = constrain(rawValue, 0, 4095);
  return map(constrained, 0, 4095, centerY + amp, centerY - amp);
}

/**
 * Draw one raw value in bar form.
 */
void drawRawBar(float x, float y, float w, float h, int value, boolean tether) {
  int constrained = constrain(value, 0, 4095);
  float mappedX = map(constrained, 0, 4095, x, x + w);

  if (tether) {
    mappedX = map(constrained, 4095, 0, x, x + w);
    fill(70, 210, 150);
    rect(x, y, mappedX - x, h, 4);
    return;
  }

  float center = x + w * 0.5f;
  if (mappedX >= center) {
    fill(70, 150, 240);
  } else {
    fill(240, 120, 90);
  }
  rect(min(center, mappedX), y, abs(mappedX - center), h, 4);
  drawTick(center, y, h);
}

/**
 * Draw the center tick used by centered joystick bars.
 */
void drawTick(float x, float y, float h) {
  stroke(210);
  strokeWeight(2);
  line(x, y - 5, x, y + h + 5);
  noStroke();
}

/**
 * Append all six channels to the fixed-length timeline history.
 */
void appendHistory(int[] parsedRaw) {
  if (historyCount < HISTORY_SIZE) {
    for (int i = 0; i < 6; i++) {
      history[i][historyCount] = parsedRaw[i];
    }
    historyCount++;
    return;
  }

  for (int i = 0; i < 6; i++) {
    arrayCopy(history[i], 1, history[i], 0, HISTORY_SIZE - 1);
    history[i][HISTORY_SIZE - 1] = parsedRaw[i];
  }
}
