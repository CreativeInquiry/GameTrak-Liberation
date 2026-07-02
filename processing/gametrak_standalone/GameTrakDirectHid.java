import org.hid4java.HidDevice;
import org.hid4java.HidManager;
import org.hid4java.HidServices;
import org.hid4java.HidServicesSpecification;
import org.hid4java.jna.HidApi;

import java.util.List;

/**
 * Direct HID worker for the GameTrak standalone Processing sketch.
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
  static final byte[] PS2_INIT_TEXT = "Gametrak".getBytes();

  final Object stateLock = new Object();
  final Object hidLock = new Object();

  HidServices hidServices;
  HidDevice currentDevice;
  Thread thread;

  volatile boolean running;
  volatile boolean reconnectRequested;

  int[] rawValues = new int[6];
  int reportCount;
  long lastReportMillis;
  String status = "starting HID";
  String deviceSummary = "";

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

  /**
   * Own the HID service and active device handle.
   */
  public void run() {
    try {
      HidApi.darwinOpenDevicesNonExclusive = true;

      HidServicesSpecification spec = new HidServicesSpecification();
      spec.setAutoStart(false);
      spec.setAutoDataRead(false);
      spec.setAutoShutdown(false);

      hidServices = HidManager.getHidServices(spec);
      hidServices.start();

      while (running) {
        reconnectRequested = false;
        HidDevice device = selectGameTrakDevice();

        if (device == null) {
          setStatus("GameTrak not found");
          clearDeviceSummary();
          sleepQuietly(RECONNECT_DELAY_MS);
          continue;
        }

        openAndStreamDevice(device);
        closeCurrentDevice();
        if (running && !reconnectRequested) {
          sleepQuietly(RECONNECT_DELAY_MS);
        }
      }
    } catch (Exception e) {
      setStatus("HID service error: " + e.getMessage());
    } finally {
      closeCurrentDevice();
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
  HidDevice selectGameTrakDevice() {
    List devices = hidServices.getAttachedHidDevices();
    HidDevice fallback = null;

    for (int i = 0; i < devices.size(); i++) {
      HidDevice device = (HidDevice)devices.get(i);
      if (device.getVendorId() == GAMETRAK_VID && device.getProductId() == GAMETRAK_PID) {
        if (fallback == null) {
          fallback = device;
        }
        if (device.getUsagePage() == HID_USAGE_PAGE_GENERIC_DESKTOP && device.getUsage() == HID_USAGE_JOYSTICK) {
          return device;
        }
      }
    }

    return fallback;
  }

  /**
   * Open one selected GameTrak, initialize it, and read reports until reconnect.
   */
  void openAndStreamDevice(HidDevice device) {
    if (!device.open()) {
      setStatus("open failed: " + device.getLastErrorMessage());
      return;
    }

    synchronized (hidLock) {
      currentDevice = device;
    }

    device.setNonBlocking(false);
    setDeviceSummary(deviceSummary(device));
    setStatus("opened; sending init");
    int ps2Key = sendPs2Init(device);
    int localReportCount = 0;
    int nextKeepaliveReport = 100;
    long staleDeadline = System.currentTimeMillis() + STALE_RECONNECT_MS;
    setStatus("receiving HID");

    while (running && !reconnectRequested) {
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
   */
  int[] decodeReport(byte[] data, int length) {
    if (length < 12) {
      return null;
    }

    int[] parsed = new int[6];
    parsed[0] = readU16LE(data, 0);
    parsed[1] = readU16LE(data, 2);
    parsed[2] = readU16LE(data, 4);
    parsed[3] = readU16LE(data, 6);
    parsed[4] = readU16LE(data, 8);
    parsed[5] = readU16LE(data, 10);

    for (int i = 0; i < parsed.length; i++) {
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
    synchronized (stateLock) {
      status = message;
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
}
