import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * GameTrak OSC Receiver.
 * Golan Levin, July 2026
 *
 * <p>This Processing sketch listens for UDP OSC messages from
 * {@code gametrak-osc} and visualizes the six GameTrak controls:
 * left joystick X/Y/R and right joystick X/Y/R. It intentionally avoids oscP5
 * or other external Processing libraries so it can be dropped into Processing
 * and run as-is.</p>
 *
 * <p>The bridge sends both {@code /gametrak/raw} and {@code /gametrak/norm} by
 * default. This sketch accepts only {@code /gametrak/raw}; raw HID-scale
 * values are easier to inspect while reverse-engineering because they expose
 * the device's actual 0..4095 sensor readings instead of calibration output.</p>
 *
 * <p>A receive thread owns the blocking UDP socket. The animation thread copies
 * shared values under {@code valueLock}, then renders from snapshots so drawing
 * never blocks incoming packets for long.</p>
 */
final int OSC_PORT = 2434;
final String OSC_BIND_HOST = "0.0.0.0";
final String CONTROL_HOST = "127.0.0.1";
final int CONTROL_PORT = 2435;
final float RESET_BUTTON_W = 150;
final float RESET_BUTTON_H = 34;

// Keep this true for the simple visualizer. The sketch is raw-first and maps
// incoming 0..4095 values to screen coordinates with Processing's map().
final boolean ACCEPT_RAW_MESSAGES = true;
final boolean ACCEPT_NORMALIZED_MESSAGES = false;
final String[] LABELS = {
  "left_x", "left_y", "left_r", "right_x", "right_y", "right_r"
};
final int HISTORY_SIZE = 860;
final float TIMELINE_AMPLITUDE = 60;
final float TIMELINE_HEADROOM = 14;

DatagramSocket oscSocket;
Thread oscThread;
volatile boolean running = true;

final Object valueLock = new Object();
int[] rawValues = new int[6];
// These histories are written by the OSC receive thread and copied by draw().
int[] leftXHistory = new int[HISTORY_SIZE];
int[] leftYHistory = new int[HISTORY_SIZE];
int[] leftRHistory = new int[HISTORY_SIZE];
int[] rightXHistory = new int[HISTORY_SIZE];
int[] rightYHistory = new int[HISTORY_SIZE];
int[] rightRHistory = new int[HISTORY_SIZE];
int historyCount = 0;
String lastAddress = "";
int packetCount = 0;
long lastPacketMillis = 0;
String status = "waiting for OSC";

/**
 * Configure the Processing canvas.
 *
 * <p>Processing requires {@code size()} to be called from {@code settings()}
 * when a sketch also uses setup-time initialization.</p>
 */
void settings() {
  size(1024,768);
}

/**
 * Initialize the UI and start the UDP OSC listener.
 */
void setup() {
  surface.setTitle("GameTrak OSC Receiver");
  textFont(createFont("Menlo", 16));
  frameRate(60);
  startOscListener();
}

/**
 * Render one animation frame from a consistent snapshot of the latest OSC data.
 *
 * <p>The OSC receive thread may update values at any time. This method copies
 * shared state while holding {@code valueLock}, releases the lock, and then
 * performs all drawing from local arrays.</p>
 */
void draw() {
  background(20);

  int[] rawSnapshot = new int[6];
  int[] lx = new int[HISTORY_SIZE];
  int[] ly = new int[HISTORY_SIZE];
  int[] lr = new int[HISTORY_SIZE];
  int[] rx = new int[HISTORY_SIZE];
  int[] ry = new int[HISTORY_SIZE];
  int[] rr = new int[HISTORY_SIZE];
  int historySnapshotCount;
  String address;
  int packets;
  long lastSeen;
  String currentStatus;

  synchronized (valueLock) {
    // Copy shared state quickly, then draw outside the lock so UDP receive
    // does not block on rendering.
    arrayCopy(rawValues, rawSnapshot);
    arrayCopy(leftXHistory, lx);
    arrayCopy(leftYHistory, ly);
    arrayCopy(leftRHistory, lr);
    arrayCopy(rightXHistory, rx);
    arrayCopy(rightYHistory, ry);
    arrayCopy(rightRHistory, rr);
    historySnapshotCount = historyCount;
    address = lastAddress;
    packets = packetCount;
    lastSeen = lastPacketMillis;
    currentStatus = status;
  }

  drawHeader(currentStatus, address, packets, lastSeen);
  drawResetButton();
  drawBars(rawSnapshot);
  drawTimelines(lx, ly, lr, rx, ry, rr, historySnapshotCount);
}

/**
 * Draw the title and connection telemetry.
 *
 * @param currentStatus human-readable receive/control status
 * @param address latest accepted OSC address
 * @param packets count of accepted OSC packets
 * @param lastSeen Processing {@code millis()} timestamp for the latest packet
 */
void drawHeader(String currentStatus, String address, int packets, long lastSeen) {
  fill(240);
  textSize(24);
  textAlign(LEFT, BASELINE);
  text("GameTrak OSC Receiver", 40, 48);

  textSize(14);
  fill(170);
  String age = lastSeen == 0 ? "-" : nf((millis() - lastSeen) / 1000.0, 0, 2) + "s";
  text("port " + OSC_PORT + "   bind " + OSC_BIND_HOST + "   packets " + packets + "   latest " + age + "   mode raw", 40, 76);
  text(currentStatus + (address.length() > 0 ? "   " + address : ""), 40, 100);
}

/**
 * Draw the button that asks the Python bridge to release and reopen the HID.
 *
 * <p>The sketch does not talk to HID directly. It sends
 * {@code /gametrak/reconnect} to the Python bridge's control port.</p>
 */
void drawResetButton() {
  float x = resetButtonX();
  float y = resetButtonY();
  boolean hover = mouseX >= x && mouseX <= x + RESET_BUTTON_W && mouseY >= y && mouseY <= y + RESET_BUTTON_H;

  noStroke();
  fill(hover ? color(70, 120, 170) : color(54, 78, 104));
  rect(x, y, RESET_BUTTON_W, RESET_BUTTON_H, 4);

  fill(245);
  textSize(14);
  textAlign(CENTER, CENTER);
  text("Reset Device", x + RESET_BUTTON_W * 0.5, y + RESET_BUTTON_H * 0.5);
  textAlign(LEFT, BASELINE);
}

/**
 * @return left edge of the reset button in canvas coordinates
 */
float resetButtonX() {
  return width - RESET_BUTTON_W - 40;
}

/**
 * @return top edge of the reset button in canvas coordinates
 */
float resetButtonY() {
  return 34;
}

/**
 * Handle reset-button clicks.
 */
void mousePressed() {
  float x = resetButtonX();
  float y = resetButtonY();
  if (mouseX >= x && mouseX <= x + RESET_BUTTON_W && mouseY >= y && mouseY <= y + RESET_BUTTON_H) {
    // The Python bridge owns the HID handle. This button only asks it to do a
    // close/reopen/init cycle through the OSC control port.
    sendControlMessage("/gametrak/reconnect");
  }
}

/**
 * Draw the six current raw values as horizontal bars.
 *
 * @param rawSnapshot raw values in semantic order:
 *        left_x, left_y, left_r, right_x, right_y, right_r
 */
void drawBars(int[] rawSnapshot) {
  float left = 190;
  float top = 126;
  float barW = 700;
  float barH = 32;
  float gap = 40;

  for (int i = 0; i < 6; i++) {
    float y = top + i * gap;
    boolean tether = i == 2 || i == 5;

    fill(220);
    textSize(16);
    textAlign(RIGHT, CENTER);
    text(LABELS[i], left - 20, y + barH * 0.5);

    noStroke();
    fill(45);
    rect(left, y, barW, barH, 4);

    drawRawBar(left, y, barW, barH, rawSnapshot[i], tether);

    fill(230);
    textAlign(LEFT, CENTER);
    text(str(rawSnapshot[i]), left + barW + 22, y + barH * 0.5);
  }

  textAlign(LEFT, BASELINE);
}

/**
 * Draw both joystick X/Y/R history panels.
 */
void drawTimelines(int[] lx, int[] ly, int[] lr, int[] rx, int[] ry, int[] rr, int count) {
  float x = 80;
  float w = width - 140;
  float leftCenterY = 490;
  float rightCenterY = 650;

  drawTimelineLegend();
  drawTimelinePanel("Left", lx, ly, lr, count, x, leftCenterY, w);
  drawTimelinePanel("Right", rx, ry, rr, count, x, rightCenterY, w);
}

/**
 * Draw one joystick history panel.
 *
 * @param title short side label, rendered vertically
 * @param xValues raw joystick X history
 * @param yValues raw joystick Y history
 * @param rValues raw tether R history
 * @param count number of valid history samples
 * @param x left edge of the timeline plot area
 * @param centerY raw midpoint line for the timeline
 * @param w width of the timeline plot area
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

  strokeWeight(1);
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
 *
 * @param vals ring-like history buffer stored in display order
 * @param count number of valid samples in {@code vals}
 * @param x left edge of the timeline plot area
 * @param centerY y-coordinate corresponding to raw midpoint
 * @param w plot width
 * @param amp vertical amplitude in pixels
 * @param strokeColor Processing color for this channel
 */
void drawTimelineWave(int[] vals, int count, float x, float centerY, float w, float amp, int strokeColor) {
  stroke(strokeColor);
  noFill();
  beginShape();
  for (int i = 0; i < count; i++) {
    float px = map(i, 0, HISTORY_SIZE - 1, x, x + w);
    float py = rawToTimelineY(vals[i], centerY, amp);
    vertex(px, py);
  }
  endShape();
}

/**
 * Convert one 0..4095 raw reading into a timeline y-coordinate.
 *
 * @param rawValue raw HID-scale value
 * @param centerY y-coordinate for the midpoint
 * @param amp positive/negative excursion in pixels
 * @return screen y-coordinate, with larger raw values drawn higher
 */
float rawToTimelineY(int rawValue, float centerY, float amp) {
  int constrained = constrain(rawValue, 0, 4095);
  return map(constrained, 0, 4095, centerY + amp, centerY - amp);
}

/**
 * Draw one raw value in bar form.
 *
 * <p>Joystick axes are drawn from center outward because they are centered
 * controls. Tether axes are drawn as extension bars and are visually inverted
 * because the raw sensor value is high when the tether is retracted.</p>
 *
 * @param x left edge of the bar track
 * @param y top edge of the bar track
 * @param w width of the bar track
 * @param h height of the bar track
 * @param value raw HID-scale value
 * @param tether true when the value is an R/tether channel
 */
void drawRawBar(float x, float y, float w, float h, int value, boolean tether) {
  int constrained = constrain(value, 0, 4095);
  float mappedX = map(constrained, 0, 4095, x, x + w);

  if (tether) {
    // Raw tether values are high when retracted, so invert the display width
    // to make "more extension" read as a longer bar.
    mappedX = map(constrained, 4095, 0, x, x + w);
    fill(70, 210, 150);
    rect(x, y, mappedX - x, h, 4);
    return;
  }

  float center = x + w * 0.5;
  fill(mappedX >= center ? color(70, 150, 240) : color(240, 120, 90));
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
 * Create and start the UDP listener thread.
 *
 * <p>The socket binds to {@code 0.0.0.0} explicitly so packets sent to
 * {@code 127.0.0.1} arrive reliably on macOS/Processing.</p>
 */
void startOscListener() {
  try {
    // Bind explicitly to IPv4. On macOS/Processing the default DatagramSocket
    // can bind as IPv6-only, which misses packets sent to 127.0.0.1.
    oscSocket = new DatagramSocket(OSC_PORT, InetAddress.getByName(OSC_BIND_HOST));
  } catch (Exception e) {
    synchronized (valueLock) {
      status = "socket error: " + e.getMessage();
    }
    return;
  }

  oscThread = new Thread(new Runnable() {
    public void run() {
      receiveLoop();
    }
  });
  oscThread.start();
}

/**
 * Send one OSC control message to the Python bridge.
 *
 * @param address OSC address such as {@code /gametrak/reconnect}
 */
void sendControlMessage(String address) {
  DatagramSocket sendSocket = null;
  try {
    // The reset button sends one minimal OSC int message to the Python
    // bridge's control socket. No Processing OSC library is required.
    byte[] data = makeOscIntMessage(address, 1);
    DatagramPacket packet = new DatagramPacket(
      data,
      data.length,
      InetAddress.getByName(CONTROL_HOST),
      CONTROL_PORT
    );
    sendSocket = new DatagramSocket();
    sendSocket.send(packet);
    synchronized (valueLock) {
      status = "sent " + address + " to " + CONTROL_HOST + ":" + CONTROL_PORT;
    }
  } catch (Exception e) {
    synchronized (valueLock) {
      status = "control send error: " + e.getMessage();
    }
  } finally {
    if (sendSocket != null) {
      sendSocket.close();
    }
  }
}

/**
 * Build a minimal OSC packet containing one integer argument.
 *
 * <p>This is enough for the reset button's control message and avoids bringing
 * in an OSC dependency solely for one outbound command.</p>
 *
 * @param address OSC address
 * @param value integer argument
 * @return binary OSC packet
 */
byte[] makeOscIntMessage(String address, int value) {
  byte[] addressBytes = address.getBytes();
  byte[] typeBytes = ",i".getBytes();
  int addressLen = paddedOscLength(addressBytes.length + 1);
  int typeLen = paddedOscLength(typeBytes.length + 1);
  byte[] data = new byte[addressLen + typeLen + 4];

  // OSC strings are NUL-terminated and padded to 4-byte boundaries; numeric
  // arguments are big-endian.
  arrayCopy(addressBytes, 0, data, 0, addressBytes.length);
  arrayCopy(typeBytes, 0, data, addressLen, typeBytes.length);

  int offset = addressLen + typeLen;
  data[offset] = byte((value >> 24) & 0xff);
  data[offset + 1] = byte((value >> 16) & 0xff);
  data[offset + 2] = byte((value >> 8) & 0xff);
  data[offset + 3] = byte(value & 0xff);
  return data;
}

/**
 * Round a NUL-terminated OSC string length up to a 4-byte boundary.
 *
 * @param length string byte count including the NUL terminator
 * @return padded byte count
 */
int paddedOscLength(int length) {
  return ((length + 3) / 4) * 4;
}

/**
 * Blocking UDP receive loop executed on {@code oscThread}.
 */
void receiveLoop() {
  byte[] buffer = new byte[4096];
  while (running) {
    DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
    try {
      oscSocket.receive(packet);
      parseOscPacket(packet.getData(), packet.getLength());
    } catch (Exception e) {
      if (running) {
        synchronized (valueLock) {
          status = "receive error: " + e.getMessage();
        }
      }
    }
  }
}

/**
 * Parse one incoming OSC packet and update displayed values if it is accepted.
 *
 * <p>The parser supports the small OSC subset used by the bridge: one address
 * string, one type-tag string, and six 32-bit int or float arguments. Unknown
 * addresses are ignored.</p>
 *
 * @param data packet buffer
 * @param length number of valid bytes in {@code data}
 */
void parseOscPacket(byte[] data, int length) {
  OscString address = readOscString(data, length, 0);
  if (address == null) {
    return;
  }

  OscString types = readOscString(data, length, address.nextOffset);
  if (types == null || types.text.length() < 2 || types.text.charAt(0) != ',') {
    return;
  }

  String typeTags = types.text.substring(1);
  int offset = types.nextOffset;
  int[] parsedRaw = new int[6];

  if (address.text.equals("/gametrak/raw")) {
    if (!ACCEPT_RAW_MESSAGES) {
      return;
    }
    int n = min(6, typeTags.length());
    for (int i = 0; i < n; i++) {
      if (offset + 4 > length) return;
      if (typeTags.charAt(i) == 'i') {
        parsedRaw[i] = readInt(data, offset);
      } else if (typeTags.charAt(i) == 'f') {
        parsedRaw[i] = round(readFloat(data, offset));
      }
      offset += 4;
    }
    updateValues(address.text, parsedRaw);
    return;
  }

  if (address.text.equals("/gametrak/norm") || address.text.equals("/wekinator/control/inputs")) {
    if (!ACCEPT_NORMALIZED_MESSAGES) {
      return;
    }
    int n = min(6, typeTags.length());
    for (int i = 0; i < n; i++) {
      if (offset + 4 > length) return;
      float normalized = 0;
      if (typeTags.charAt(i) == 'f') {
        normalized = readFloat(data, offset);
      } else if (typeTags.charAt(i) == 'i') {
        normalized = readInt(data, offset);
      }
      // Optional compatibility path: convert normalized joystick values back
      // to a raw-ish 0..4095 display range if this mode is enabled.
      parsedRaw[i] = round(map(normalized, -1, 1, 0, 4095));
      offset += 4;
    }
    updateValues(address.text, parsedRaw);
  }
}

/**
 * Publish a parsed six-channel sample to the drawing thread.
 *
 * @param address accepted OSC address
 * @param parsedRaw six raw or raw-like values in semantic order
 */
void updateValues(String address, int[] parsedRaw) {
  synchronized (valueLock) {
    arrayCopy(parsedRaw, rawValues);
    appendHistory(parsedRaw);
    lastAddress = address;
    packetCount++;
    lastPacketMillis = millis();
    status = "receiving";
  }
}

/**
 * Append joystick X/Y/R values to the fixed-length timeline history.
 */
void appendHistory(int[] parsedRaw) {
  if (historyCount < HISTORY_SIZE) {
    leftXHistory[historyCount] = parsedRaw[0];
    leftYHistory[historyCount] = parsedRaw[1];
    leftRHistory[historyCount] = parsedRaw[2];
    rightXHistory[historyCount] = parsedRaw[3];
    rightYHistory[historyCount] = parsedRaw[4];
    rightRHistory[historyCount] = parsedRaw[5];
    historyCount++;
    return;
  }

  arrayCopy(leftXHistory, 1, leftXHistory, 0, HISTORY_SIZE - 1);
  arrayCopy(leftYHistory, 1, leftYHistory, 0, HISTORY_SIZE - 1);
  arrayCopy(leftRHistory, 1, leftRHistory, 0, HISTORY_SIZE - 1);
  arrayCopy(rightXHistory, 1, rightXHistory, 0, HISTORY_SIZE - 1);
  arrayCopy(rightYHistory, 1, rightYHistory, 0, HISTORY_SIZE - 1);
  arrayCopy(rightRHistory, 1, rightRHistory, 0, HISTORY_SIZE - 1);

  int last = HISTORY_SIZE - 1;
  leftXHistory[last] = parsedRaw[0];
  leftYHistory[last] = parsedRaw[1];
  leftRHistory[last] = parsedRaw[2];
  rightXHistory[last] = parsedRaw[3];
  rightYHistory[last] = parsedRaw[4];
  rightRHistory[last] = parsedRaw[5];
}

/**
 * Read one OSC string from a packet.
 *
 * @param data packet buffer
 * @param length number of valid bytes in {@code data}
 * @param offset byte offset where the OSC string starts
 * @return parsed string plus next 4-byte-aligned offset, or null if malformed
 */
OscString readOscString(byte[] data, int length, int offset) {
  if (offset >= length) return null;

  int end = offset;
  while (end < length && data[end] != 0) {
    end++;
  }
  if (end >= length) return null;

  String text = new String(data, offset, end - offset);
  int next = ((end + 4) / 4) * 4;
  return new OscString(text, next);
}

/**
 * Read a big-endian 32-bit OSC integer.
 */
int readInt(byte[] data, int offset) {
  return ByteBuffer.wrap(data, offset, 4).order(ByteOrder.BIG_ENDIAN).getInt();
}

/**
 * Read a big-endian 32-bit OSC float.
 */
float readFloat(byte[] data, int offset) {
  return ByteBuffer.wrap(data, offset, 4).order(ByteOrder.BIG_ENDIAN).getFloat();
}

/**
 * Stop the UDP listener and release the socket when Processing exits.
 */
void stop() {
  running = false;
  if (oscSocket != null) {
    oscSocket.close();
  }
  super.stop();
}

/**
 * Parsed OSC string result.
 *
 * <p>OSC strings are NUL-terminated and padded to 4-byte boundaries, so callers
 * need both the decoded text and the offset where the next OSC field starts.</p>
 */
class OscString {
  String text;
  int nextOffset;

  /**
   * @param text decoded OSC string
   * @param nextOffset next 4-byte-aligned packet offset
   */
  OscString(String text, int nextOffset) {
    this.text = text;
    this.nextOffset = nextOffset;
  }
}
