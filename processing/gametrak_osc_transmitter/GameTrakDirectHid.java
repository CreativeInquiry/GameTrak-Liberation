import org.hid4java.HidDevice;
import org.hid4java.HidManager;
import org.hid4java.HidServices;
import org.hid4java.HidServicesSpecification;
import org.hid4java.jna.HidApi;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Direct HID worker for the GameTrak OSC transmitter Processing sketch.
 *
 * <p>This is a real Java tab, not a PDE tab, so Processing does not run it
 * through the sketch preprocessor. Keeping hid4java types here avoids parser
 * problems in Processing 4.5.x.</p>
 */
public class GameTrakDirectHid implements Runnable {
  static final int GAMETRAK_VID = 0x14B7;
  static final int GAMETRAK_PID = 0x0982;
  static final int HID_USAGE_PAGE_GENERIC_DESKTOP = 0x01;
  static final int HID_USAGE_JOYSTICK = 0x04;

  static final int EXPECTED_INPUT_REPORT_SIZE = 16;
  static final int READ_TIMEOUT_MS = 50;
  static final int STALE_RECONNECT_MS = 5000;
  static final int RECONNECT_DELAY_MS = 1000;
  static final int STOP_JOIN_MS = 2000;
  static final int PS2_INITIAL_KEY = 0x23;
  static final byte[] PS2_INIT_TEXT = "Gametrak".getBytes(StandardCharsets.UTF_8);
  static final String OSC_RAW_ADDRESS = "/gametrak/raw";
  static final String OSC_INIT_ADDRESS = "/gametrak/init";
  static final String OSC_RECONNECT_ADDRESS = "/gametrak/reconnect";

  final Object stateLock = new Object();
  final Object hidLock = new Object();
  final String oscHost;
  final int oscPort;
  final String controlHost;
  final int controlPort;

  HidServices hidServices;
  HidDevice currentDevice;
  DatagramSocket oscSocket;
  InetAddress oscAddress;
  DatagramSocket controlSocket;
  Thread thread;

  volatile boolean running;
  volatile boolean reconnectRequested;
  volatile boolean initRequested;

  int[] rawValues = new int[6];
  int reportCount;
  int oscPacketCount;
  int controlPacketCount;
  long lastReportMillis;
  String status = "starting HID";
  String deviceSummary = "";
  String oscSummary = "";
  String controlSummary = "";
  String diagnosticSummary = "HID startup pending";
  String diagnosticDetail = "";
  String diagnosticSeverity = "info";

  public GameTrakDirectHid() {
    this("127.0.0.1", 2434, "127.0.0.1", 2435);
  }

  public GameTrakDirectHid(String oscHost, int oscPort) {
    this(oscHost, oscPort, "127.0.0.1", 2435);
  }

  public GameTrakDirectHid(String oscHost, int oscPort, String controlHost, int controlPort) {
    this.oscHost = oscHost;
    this.oscPort = oscPort;
    this.controlHost = controlHost;
    this.controlPort = controlPort;
  }

  /**
   * Start the background HID reader thread.
   */
  public void start() {
    if (thread != null) {
      return;
    }
    running = true;
    thread = new Thread(this);
    thread.setName("gametrak-hid-reader");
    thread.start();
  }

  /**
   * Stop the reader and release HID resources.
   *
   * <p>Do not close the HID device or shut hid4java down from Processing's
   * animation/dispose thread. On macOS, closing IOHID objects concurrently with
   * an active read or a second hid_exit call can crash in native CFRelease().
   * Instead, request shutdown and wait briefly; the HID worker owns all native
   * close/shutdown calls.</p>
   */
  public void stop() {
    running = false;
    reconnectRequested = true;
    setStatus("stopping HID");

    Thread localThread = thread;
    if (localThread != null && localThread != Thread.currentThread()) {
      try {
        localThread.join(STOP_JOIN_MS);
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    }
  }

  /**
   * Ask the HID loop to close and reopen the device.
   */
  public void requestReconnect() {
    reconnectRequested = true;
    setStatus("reconnect requested");
  }

  /**
   * Ask the HID loop to resend the libgametrak init sequence.
   */
  public void requestInit() {
    initRequested = true;
    setStatus("init requested");
  }

  public void copyRawValues(int[] destination) {
    synchronized (stateLock) {
      for (int i = 0; i < rawValues.length && i < destination.length; i++) {
        destination[i] = rawValues[i];
      }
    }
  }

  public int getReportCount() {
    synchronized (stateLock) {
      return reportCount;
    }
  }

  public int getOscPacketCount() {
    synchronized (stateLock) {
      return oscPacketCount;
    }
  }

  public int getControlPacketCount() {
    synchronized (stateLock) {
      return controlPacketCount;
    }
  }

  public long getLastReportMillis() {
    synchronized (stateLock) {
      return lastReportMillis;
    }
  }

  public String getStatus() {
    synchronized (stateLock) {
      return status;
    }
  }

  public String getDeviceSummary() {
    synchronized (stateLock) {
      return deviceSummary;
    }
  }

  public String getOscSummary() {
    synchronized (stateLock) {
      return oscSummary;
    }
  }

  public String getControlSummary() {
    synchronized (stateLock) {
      return controlSummary;
    }
  }

  public String getDiagnosticSummary() {
    synchronized (stateLock) {
      return diagnosticSummary;
    }
  }

  public String getDiagnosticDetail() {
    synchronized (stateLock) {
      return diagnosticDetail;
    }
  }

  public String getDiagnosticSeverity() {
    synchronized (stateLock) {
      return diagnosticSeverity;
    }
  }

  /**
   * Own the HID service and active device handle.
   */
  public void run() {
    try {
      setDiagnostic("info", "Starting HID service", "Initializing hid4java without automatic background reads.");
      HidApi.darwinOpenDevicesNonExclusive = true;

      HidServicesSpecification spec = new HidServicesSpecification();
      spec.setAutoStart(false);
      spec.setAutoDataRead(false);
      spec.setAutoShutdown(false);

      hidServices = HidManager.getHidServices(spec);
      hidServices.start();
      setDiagnostic("info", "HID service started", "Scanning for GameTrak VID 0x14B7 PID 0x0982.");
      openOscOutput();
      openControlInput();

      while (running) {
        reconnectRequested = false;
        SelectionResult selection = selectGameTrakDevice();
        HidDevice device = selection.device;

        if (device == null) {
          setStatus("GameTrak not found");
          setDiagnostic(
            "error",
            "GameTrak not found",
            "hid4java sees " + selection.totalHidDevices + " HID devices, but none match VID 0x14B7 PID 0x0982. Check USB power, cable, adapter, and macOS USB permission."
          );
          clearDeviceSummary();
          sleepQuietly(RECONNECT_DELAY_MS);
          continue;
        }

        if (selection.joystickMatches == 0) {
          setDiagnostic(
            "warning",
            "GameTrak VID/PID found, but joystick collection was not identified",
            "Found " + selection.gametrakMatches + " matching HID collection(s); opening the first fallback collection. This can indicate a descriptor or OS enumeration difference."
          );
        } else {
          setDiagnostic(
            "info",
            "GameTrak HID collection found",
            "Found " + selection.gametrakMatches + " matching collection(s), " + selection.joystickMatches + " joystick collection(s)."
          );
        }

        openAndStreamDevice(device);
        closeCurrentDevice();
        if (running && !reconnectRequested) {
          sleepQuietly(RECONNECT_DELAY_MS);
        }
      }
    } catch (Exception e) {
      setStatus("HID service error: " + e.getMessage());
      setDiagnostic("error", "HID service error", exceptionDetail(e));
    } finally {
      closeCurrentDevice();
      closeControlInput();
      closeOscOutput();
      if (hidServices != null) {
        hidServices.shutdown();
        hidServices = null;
      }
      thread = null;
    }
  }

  /**
   * Select the joystick collection from all matching GameTrak HID collections.
   */
  SelectionResult selectGameTrakDevice() {
    List devices = hidServices.getAttachedHidDevices();
    HidDevice fallback = null;
    HidDevice selected = null;
    int gametrakMatches = 0;
    int joystickMatches = 0;

    for (int i = 0; i < devices.size(); i++) {
      HidDevice device = (HidDevice)devices.get(i);
      if (device.getVendorId() == GAMETRAK_VID && device.getProductId() == GAMETRAK_PID) {
        gametrakMatches++;
        if (fallback == null) {
          fallback = device;
        }
        if (device.getUsagePage() == HID_USAGE_PAGE_GENERIC_DESKTOP && device.getUsage() == HID_USAGE_JOYSTICK) {
          joystickMatches++;
          if (selected == null) {
            selected = device;
          }
        }
      }
    }

    if (selected == null) {
      selected = fallback;
    }
    return new SelectionResult(selected, devices.size(), gametrakMatches, joystickMatches);
  }

  /**
   * Open one selected GameTrak, initialize it, and read reports until reconnect.
   */
  void openAndStreamDevice(HidDevice device) {
    if (!device.open()) {
      String lastError = safeString(device.getLastErrorMessage());
      if (lastError.length() == 0) {
        lastError = "hid4java did not provide an error string";
      }
      setStatus("open failed: " + lastError);
      setDeviceSummary(deviceSummary(device));
      setDiagnostic(
        "error",
        "GameTrak found but HID open failed (may be in use)",
        lastError + ". This usually means another app owns the HID handle, macOS has a stale handle, or the selected HID collection is unavailable. Quit other GameTrak/Python/Processing/Chrome MIDI tools, then use Reconnect HID."
      );
      return;
    }

    synchronized (hidLock) {
      currentDevice = device;
    }

    device.setNonBlocking(false);
    setDeviceSummary(deviceSummary(device));
    setStatus("opened; sending init");
    setDiagnostic("info", "GameTrak opened", "HID handle opened; sending libgametrak-style PS2 init sequence.");
    int ps2Key = sendPs2Init(device);
    int localReportCount = 0;
    int nextKeepaliveReport = 100;
    long staleDeadline = System.currentTimeMillis() + STALE_RECONNECT_MS;
    setStatus("receiving HID");
    setDiagnostic("info", "Waiting for reports", "Init was sent; waiting for valid 16-byte HID input reports.");

    while (running && !reconnectRequested) {
      pollControlMessages();
      if (reconnectRequested) {
        break;
      }
      if (initRequested) {
        initRequested = false;
        setStatus("control init");
        ps2Key = sendPs2Init(device);
        localReportCount = 0;
        nextKeepaliveReport = 100;
      }

      if (localReportCount >= nextKeepaliveReport) {
        ps2Key = sendPs2Keepalive(device, ps2Key);
        nextKeepaliveReport += 100;
      }

      byte[] data = new byte[EXPECTED_INPUT_REPORT_SIZE];
      int n = device.read(data, READ_TIMEOUT_MS);
      long now = System.currentTimeMillis();

      if (n <= 0) {
        if (now >= staleDeadline) {
          setStatus("no valid reports for " + (STALE_RECONNECT_MS / 1000) + "s");
          setDiagnostic(
            "warning",
            "GameTrak opened but no reports arrived",
            "The HID handle is open, but no valid sensor reports arrived after init. Try Reconnect HID, unplug/replug, or move the controller."
          );
          return;
        }
        continue;
      }

      int[] parsed = decodeReport(data, n);
      if (parsed == null) {
        continue;
      }

      localReportCount++;
      staleDeadline = now + STALE_RECONNECT_MS;
      updateValues(parsed);
      sendOscRaw(parsed);
    }

    if (reconnectRequested) {
      setStatus("reconnecting");
    }
  }

  /**
   * Send libgametrak's ps2mode init sequence.
   */
  int sendPs2Init(HidDevice device) {
    byte[] drain = new byte[EXPECTED_INPUT_REPORT_SIZE];
    device.read(drain, 10);

    int textResult = device.write(PS2_INIT_TEXT, PS2_INIT_TEXT.length, (byte)0x00);
    device.read(drain, 10);

    byte[] packet = {
      (byte)(0x45),
      (byte)(PS2_INITIAL_KEY & 0xff)
    };
    int packetResult = device.write(packet, packet.length, (byte)0x00);
    setStatus("init writes text=" + textResult + " key=" + packetResult);
    return ps2NextKey(PS2_INITIAL_KEY);
  }

  /**
   * Send one periodic libgametrak ps2mode keepalive packet.
   */
  int sendPs2Keepalive(HidDevice device, int key) {
    byte[] packet = {
      (byte)(0x46),
      (byte)(key & 0xff)
    };
    device.write(packet, packet.length, (byte)0x00);
    return ps2NextKey(key);
  }

  /**
   * Return the next libgametrak ps2mode key value.
   */
  int ps2NextKey(int key) {
    int ret = 0;
    ret |= 0x000001 & key;
    ret |= 0x000002 & ((key >> 15) ^ (key >> 16));
    ret |= 0x0000FC & ((key << 2) ^ (key >> 16));
    ret |= 0xFFFF00 & (key << 7);
    return ret;
  }

  /**
   * Decode a 16-byte GameTrak input report into semantic raw channels.
   *
   * <p>The returned array contains seven integers: six raw axes followed by
   * the decoded button bitfield. That matches the public
   * {@code /gametrak/raw} OSC payload contract.</p>
   */
  int[] decodeReport(byte[] data, int length) {
    if (length < 12) {
      return null;
    }

    int[] parsed = new int[7];
    parsed[0] = readU16LE(data, 0);
    parsed[1] = readU16LE(data, 2);
    parsed[2] = readU16LE(data, 4);
    parsed[3] = readU16LE(data, 6);
    parsed[4] = readU16LE(data, 8);
    parsed[5] = readU16LE(data, 10);
    parsed[6] = 0;
    if (length >= 14) {
      int tailBits = readU16LE(data, 12);
      parsed[6] = (tailBits >> 4) & 0x0fff;
    }

    for (int i = 0; i < 6; i++) {
      if (parsed[i] < 0 || parsed[i] > 4095) {
        return null;
      }
    }
    return parsed;
  }

  int readU16LE(byte[] data, int offset) {
    return (data[offset] & 0xff) | ((data[offset + 1] & 0xff) << 8);
  }

  /**
   * Publish one parsed sample to the drawing thread.
   */
  void updateValues(int[] parsedRaw) {
    synchronized (stateLock) {
      for (int i = 0; i < rawValues.length; i++) {
        rawValues[i] = parsedRaw[i];
      }
      reportCount++;
      lastReportMillis = System.currentTimeMillis();
      status = "receiving HID";
      if (!"ok".equals(diagnosticSeverity)) {
        diagnosticSeverity = "ok";
        diagnosticSummary = "Receiving GameTrak reports";
        diagnosticDetail = "Startup succeeded; valid raw HID reports are streaming.";
        logDiagnostic("ok", diagnosticSummary, diagnosticDetail);
      }
    }
  }

  /**
   * Open the UDP socket used for outbound OSC.
   */
  void openOscOutput() {
    try {
      oscAddress = InetAddress.getByName(oscHost);
      oscSocket = new DatagramSocket();
      setOscSummary("OSC -> " + oscHost + ":" + oscPort);
    } catch (Exception e) {
      oscSocket = null;
      oscAddress = null;
      setOscSummary("OSC error: " + e.getMessage());
    }
  }

  /**
   * Open the UDP socket used for OSC control messages.
   */
  void openControlInput() {
    try {
      controlSocket = new DatagramSocket(controlPort, InetAddress.getByName(controlHost));
      controlSocket.setSoTimeout(1);
      setControlSummary("control <- " + controlHost + ":" + controlPort);
    } catch (Exception e) {
      controlSocket = null;
      setControlSummary("control error: " + e.getMessage());
    }
  }

  /**
   * Drain pending OSC control packets without doing HID work on this socket.
   */
  void pollControlMessages() {
    if (controlSocket == null) {
      return;
    }

    byte[] buffer = new byte[1024];
    while (true) {
      try {
        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
        controlSocket.receive(packet);
        String address = oscAddressFromPacket(packet.getData(), packet.getLength());
        if (address != null) {
          handleControlAddress(address);
        }
      } catch (SocketTimeoutException e) {
        return;
      } catch (Exception e) {
        setControlSummary("control read error: " + e.getMessage());
        return;
      }
    }
  }

  /**
   * Convert recognized OSC control addresses into HID-loop flags.
   */
  void handleControlAddress(String address) {
    if (OSC_RECONNECT_ADDRESS.equals(address)) {
      reconnectRequested = true;
      incrementControlPacketCount();
      setControlSummary("control reconnect");
      return;
    }

    if (OSC_INIT_ADDRESS.equals(address)) {
      initRequested = true;
      incrementControlPacketCount();
      setControlSummary("control init");
    }
  }

  /**
   * Return the OSC address string from a UDP packet.
   */
  String oscAddressFromPacket(byte[] data, int length) {
    if (length <= 0 || data[0] != '/') {
      return null;
    }

    int end = 0;
    while (end < length && data[end] != 0) {
      end++;
    }
    if (end == length) {
      return null;
    }
    return new String(data, 0, end, StandardCharsets.UTF_8);
  }

  /**
   * Send one {@code /gametrak/raw} OSC packet.
   */
  void sendOscRaw(int[] parsedRaw) {
    if (oscSocket == null || oscAddress == null) {
      return;
    }

    try {
      byte[] packet = buildOscIntPacket(OSC_RAW_ADDRESS, parsedRaw, 7);
      DatagramPacket datagram = new DatagramPacket(packet, packet.length, oscAddress, oscPort);
      oscSocket.send(datagram);
      incrementOscPacketCount();
    } catch (Exception e) {
      setOscSummary("OSC send error: " + e.getMessage());
    }
  }

  /**
   * Close the outbound OSC socket.
   */
  void closeOscOutput() {
    if (oscSocket != null) {
      oscSocket.close();
      oscSocket = null;
    }
    oscAddress = null;
  }

  /**
   * Close the OSC control listener.
   */
  void closeControlInput() {
    if (controlSocket != null) {
      controlSocket.close();
      controlSocket = null;
    }
  }

  /**
   * Build a minimal OSC packet containing only 32-bit integer arguments.
   */
  byte[] buildOscIntPacket(String address, int[] values, int count) {
    byte[] addressBytes = oscPaddedString(address);
    String tags = ",";
    for (int i = 0; i < count; i++) {
      tags += "i";
    }
    byte[] tagBytes = oscPaddedString(tags);
    byte[] packet = new byte[addressBytes.length + tagBytes.length + count * 4];
    int offset = 0;

    for (int i = 0; i < addressBytes.length; i++) {
      packet[offset++] = addressBytes[i];
    }
    for (int i = 0; i < tagBytes.length; i++) {
      packet[offset++] = tagBytes[i];
    }
    for (int i = 0; i < count; i++) {
      writeIntBigEndian(packet, offset, values[i]);
      offset += 4;
    }
    return packet;
  }

  /**
   * Return an OSC string: ASCII text, NUL terminator, padded to four bytes.
   */
  byte[] oscPaddedString(String text) {
    byte[] textBytes = text.getBytes(StandardCharsets.UTF_8);
    int size = ((textBytes.length + 1 + 3) / 4) * 4;
    byte[] padded = new byte[size];
    for (int i = 0; i < textBytes.length; i++) {
      padded[i] = textBytes[i];
    }
    return padded;
  }

  void writeIntBigEndian(byte[] packet, int offset, int value) {
    packet[offset] = (byte)((value >> 24) & 0xff);
    packet[offset + 1] = (byte)((value >> 16) & 0xff);
    packet[offset + 2] = (byte)((value >> 8) & 0xff);
    packet[offset + 3] = (byte)(value & 0xff);
  }

  void incrementOscPacketCount() {
    synchronized (stateLock) {
      oscPacketCount++;
    }
  }

  void incrementControlPacketCount() {
    synchronized (stateLock) {
      controlPacketCount++;
    }
  }

  /**
   * Close the current HID handle, if any.
   */
  void closeCurrentDevice() {
    synchronized (hidLock) {
      if (currentDevice != null && !currentDevice.isClosed()) {
        currentDevice.close();
      }
      currentDevice = null;
    }
  }

  void setStatus(String message) {
    boolean changed = false;
    synchronized (stateLock) {
      changed = !message.equals(status);
      status = message;
    }
    if (changed) {
      System.out.println("[GameTrak HID] status: " + message);
    }
  }

  void setDiagnostic(String severity, String summary, String detail) {
    boolean changed = false;
    synchronized (stateLock) {
      changed = !severity.equals(diagnosticSeverity) || !summary.equals(diagnosticSummary) || !detail.equals(diagnosticDetail);
      diagnosticSeverity = severity;
      diagnosticSummary = summary;
      diagnosticDetail = detail;
    }
    if (changed) {
      logDiagnostic(severity, summary, detail);
    }
  }

  void logDiagnostic(String severity, String summary, String detail) {
    String line = "[GameTrak HID] " + severity.toUpperCase() + ": " + summary;
    if (detail != null && detail.length() > 0) {
      line += " - " + detail;
    }
    if ("error".equals(severity)) {
      System.err.println(line);
    } else {
      System.out.println(line);
    }
  }

  void setDeviceSummary(String summary) {
    synchronized (stateLock) {
      deviceSummary = summary;
    }
  }

  void clearDeviceSummary() {
    synchronized (stateLock) {
      deviceSummary = "";
    }
  }

  void setOscSummary(String summary) {
    synchronized (stateLock) {
      oscSummary = summary;
    }
  }

  void setControlSummary(String summary) {
    synchronized (stateLock) {
      controlSummary = summary;
    }
  }

  String deviceSummary(HidDevice device) {
    return safeString(device.getManufacturer()) + " " +
      safeString(device.getProduct()) +
      "   usage 0x" + toFixedHex(device.getUsagePage(), 4) + "/0x" + toFixedHex(device.getUsage(), 4) +
      "   interface " + device.getInterfaceNumber();
  }

  String safeString(String value) {
    if (value == null) {
      return "";
    }
    return value;
  }

  String exceptionDetail(Exception e) {
    if (e == null) {
      return "unknown exception";
    }
    String message = e.getMessage();
    if (message == null || message.length() == 0) {
      return e.getClass().getName();
    }
    return e.getClass().getName() + ": " + message;
  }

  String toFixedHex(int value, int width) {
    String text = Integer.toHexString(value).toUpperCase();
    while (text.length() < width) {
      text = "0" + text;
    }
    return text;
  }

  void sleepQuietly(int millis) {
    try {
      Thread.sleep(millis);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    }
  }

  static class SelectionResult {
    final HidDevice device;
    final int totalHidDevices;
    final int gametrakMatches;
    final int joystickMatches;

    SelectionResult(HidDevice device, int totalHidDevices, int gametrakMatches, int joystickMatches) {
      this.device = device;
      this.totalHidDevices = totalHidDevices;
      this.gametrakMatches = gametrakMatches;
      this.joystickMatches = joystickMatches;
    }
  }
}
