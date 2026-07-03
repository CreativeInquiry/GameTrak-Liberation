# GameTrak HID Protocol Notes

This document records the known USB HID behavior of the In2Games GameTrak unit under investigation.

## 1. Scope

Device under investigation:

```text
Physical label: GameTrak V2.0 golfing interface device for PlayStation
USB product string: Game-Trak V1.3
Manufacturer string: In2Games Ltd.
VID: 0x14B7
PID: 0x0982
```

Important version distinction:

```text
GameTrak V2.0 appears to be the physical/hardware/product label.
Game-Trak V1.3 is the USB product string reported to macOS.
```

Do not assume all GameTrak hardware revisions behave identically.

---

## 2. Verified macOS USB identity

Observed USB identity:

| Property | Value |
|---|---|
| Product | `Game-Trak V1.3` |
| Manufacturer | `In2Games Ltd.` |
| VID | `0x14B7` |
| PID | `0x0982` |
| USB speed | Full-speed, 12 Mbps |
| Current required | 20 mA |

Observed upstream topology:

```text
USB2.0 Hub, Genesys Logic, VID 0x05E3 PID 0x0618
  -> Jess Technology Co. hub/device, VID 0x0F30 PID 0x001C
      -> Game-Trak V1.3, VID 0x14B7 PID 0x0982
```

The Jess Technology node appears to be an upstream USB hub or hub-like component. It is not the HID device to open.

---

## 3. Verified HID identity

macOS binds the device as:

```text
AppleUserUSBHostHIDDevice
```

Observed HID properties:

| Property | Value |
|---|---|
| Product | `Game-Trak V1.3` |
| Manufacturer | `In2Games Ltd.` |
| VendorID | `5303` / `0x14B7` |
| ProductID | `2434` / `0x0982` |
| Transport | `USB` |
| IOProviderClass | `IOUSBHostInterface` |
| bInterfaceClass | `3` |
| bInterfaceSubClass | `0` |
| PrimaryUsagePage | `1` |
| PrimaryUsage | `4` |
| DeviceUsagePairs | Generic Desktop Joystick and Pointer |
| MaxInputReportSize | 16 |
| MaxOutputReportSize | 4 |
| MaxFeatureReportSize | 0 |

Interpretation:

```text
bInterfaceClass = 3 means USB HID.
PrimaryUsagePage = 1 means Generic Desktop.
PrimaryUsage = 4 means Joystick.
```

---

## 4. Raw HID report descriptor

Raw descriptor retrieved with `hidapi` on this Mac:

```text
05010904a1010901a10009300931093209330934093516000026ff0f36000046ff0f660000751095068102c00939150125083500463b01651475049501810205091901290c150025017501950c55006500810275089502810105080943150026ff00350046ff00750895019182094491820945918209469182c0
```

Length: 122 bytes.

---

## 5. Parsed HID descriptor: known six-axis section

Parsed beginning of the descriptor:

```text
05 01        Usage Page (Generic Desktop)
09 04        Usage (Joystick)
A1 01        Collection (Application)
09 01        Usage (Pointer)
A1 00        Collection (Physical)
09 30        Usage X
09 31        Usage Y
09 32        Usage Z
09 33        Usage Rx
09 34        Usage Ry
09 35        Usage Rz
16 00 00     Logical Minimum 0
26 FF 0F     Logical Maximum 4095
36 00 00     Physical Minimum 0
46 FF 0F     Physical Maximum 4095
66 00 00     Unit 0
75 10        Report Size 16
95 06        Report Count 6
81 02        Input: Data, Variable, Absolute
C0           End Collection
```

Descriptor-derived facts:

```text
The device exposes six absolute HID axes:
X, Y, Z, Rx, Ry, Rz

Each axis is a 16-bit field.
Logical minimum: 0
Logical maximum: 4095
Physical minimum: 0
Physical maximum: 4095
```

The 0..4095 range implies 12-bit sensor precision stored in 16-bit HID fields.

---

## 6. Input report layout

The first six 16-bit fields are confirmed by the descriptor and live movement
captures. The hat/buttons and tail bytes are still only partially understood.

| Byte offset | Field | Type | Range | Status |
|---:|---|---|---|---|
| 0-1 | X | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| 2-3 | Y | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| 4-5 | Z | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| 6-7 | Rx | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| 8-9 | Ry | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| 10-11 | Rz | `uint16_le` | 0..4095 | descriptor-derived, live-confirmed |
| byte 12 bits 0-3 | hat switch | unsigned nibble | 1..8 per descriptor; neutral TBD | descriptor-derived |
| byte 12 bits 4-7 | buttons 1-4 | bit mask | 0 or 1 per bit | descriptor-derived |
| byte 13 bits 0-7 | buttons 5-12 | bit mask | 0 or 1 per bit | descriptor-derived |
| 14-15 | descriptor padding / live tail value | `uint16_le`? | TBD | descriptor says padding; live right-tether capture varies |

Current decoder:

```python
x, y, z, rx, ry, rz = struct.unpack_from("<6H", report, 0)
tail_bits = int.from_bytes(report[12:14], "little")
hat = tail_bits & 0x0F
buttons = (tail_bits >> 4) & 0x0FFF
```

This must not be treated as final until verified against additional
`gametrak-record` captures from other GameTrak units.

Prior-art warning:

```text
casiez/libgametrak decodes bytes 14-15 as one axis in normal mode:
rawLeftTheta = (buf[15] << 8) + buf[14]
```

This conflicts with this unit's HID descriptor, which places the hat/buttons
after the six axes and marks bytes 14-15 as padding. Preserve all 16 bytes in
captures and test both interpretations once reports are available.

Live-capture update:

```text
sample_reports/right_tether_r_001.jsonl:
  raw rz:      min=1062 max=4050 span=2988
  bytes 14-15: min=  78 max=3035 span=2957
  rz + bytes14_15: mean=4098.4 stdev=12.2
```

During the right tether R test, bytes 14-15 tracked approximately
`4095 - rz`. Treat this field as live data until its purpose is understood,
despite the HID descriptor labeling it as padding.

---

## 7. Axis mapping: empirical default

The HID descriptor names the six fields:

```text
X, Y, Z, Rx, Ry, Rz
```

The public APIs expose semantic fields:

```text
left_x, left_y, left_r
right_x, right_y, right_r
```

Empirical mapping from current live captures:

```text
raw x  = left_x   low=left,  high=right
raw y  = left_y   low=toward, high=away
raw z  = left_r   high=retracted/short, lower=extended

raw rx = right_x  low=left,  high=right
raw ry = right_y  low=toward, high=away
raw rz = right_r  high=retracted/short, lower=extended
```

`python/gametrak/values.py` encodes this fixed semantic channel order. The
Python tools and Processing sketches do not apply per-device calibration,
deadband, smoothing, or range fitting. Applications should perform those
transforms after receiving raw data.

---

## 8. Buttons and footswitch: unresolved

The descriptor exposes 12 one-bit buttons after the 4-bit hat switch. The exact physical meaning of these bits is not yet confirmed. The footswitch is likely one of these 12 bits, but its bit index and polarity are unknown.

Current hardware note: the available unit used for testing and developing this project does not include the external
footswitch. Thus footswitch bit mapping cannot be physically tested in the current
setup. Keep decoding and exposing the 12 button bits, but document footswitch
semantics as unavailable unless a footswitch is later obtained.

Expected process:

```text
1. Capture raw reports with footswitch released.
2. Capture raw reports with footswitch pressed.
3. XOR reports or compare decoded hat/buttons fields after the first 12 axis bytes.
4. Identify stable bit transitions.
5. Document bit index and polarity.
```

---

## 9. Report rate: unresolved

The macOS HID node reports:

```text
ReportInterval = 8000
```

This suggests an 8 ms interval, approximately 125 Hz, but actual report timing must be measured from timestamps in live reads.

Document:

```text
- nominal report interval
- measured mean interval
- jitter
- behavior when idle
- behavior during movement
```

---

## 10. Output reports and stream initialization

The device reports:

```text
MaxOutputReportSize = 4
MaxFeatureReportSize = 0
```

Empirical update:

```text
Input streaming did not begin after open/read alone.
Input streaming did begin after sending libgametrak's optional ps2mode init sequence.
```

Observed successful init sequence:

```text
1. Open VID 0x14B7 PID 0x0982 with hidapi.
2. Optional 10 ms read attempt returns no data.
3. Write 8 bytes: 47 61 6D 65 74 72 61 6B  ("Gametrak")
4. A 16-byte echo/input report appears:
   47 61 6D 65 74 72 61 6B 00 00 00 00 00 00 00 00
5. Write 2 bytes: 45 23
6. 16-byte input reports stream continuously.
```

The current HID implementations use this sequence when initializing the device.

After streaming starts, the tool periodically sends the follow-up `0x46, key`
packets used by `casiez/libgametrak` ps2mode.

Descriptor-derived output report section:

```text
Usage Page (LEDs)
Usages 0x43, 0x44, 0x45, 0x46
Each output field is 8 bits.
Output report total is 4 bytes.
```

The meaning of these LED usages is unknown. Do not write them during normal capture.

Note: the successful init writes are not described cleanly by the 4-byte LED
output section. They are retained because they are prior-art-backed and
empirically required for this unit to stream through HIDAPI.

---

## 11. Suggested decoder API

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class GameTrakRawReport:
    x: int
    y: int
    z: int
    rx: int
    ry: int
    rz: int
    hat: int | None
    buttons: int | None
    unknown_tail: bytes
    raw: bytes
```

```python
def decode_report(report: bytes) -> GameTrakRawReport:
    if len(report) < 12:
        raise ValueError("GameTrak report must contain at least 12 bytes")
    x, y, z, rx, ry, rz = struct.unpack_from("<6H", report, 0)
    hat = None
    buttons = None
    unknown_tail = report[12:]
    if len(report) >= 14:
        tail_bits = int.from_bytes(report[12:14], "little")
        hat = tail_bits & 0x0F
        buttons = (tail_bits >> 4) & 0x0FFF
        unknown_tail = report[14:]
    return GameTrakRawReport(x, y, z, rx, ry, rz, hat, buttons, unknown_tail, report)
```

This is a starting point, not final protocol truth.

---

## 12. OSC mapping

Recommended OSC messages after semantic axis mapping is known:

```text
/gametrak/raw
  int left_x left_y left_r right_x right_y right_r buttons

/gametrak/normalized
  float left_x left_y left_r right_x right_y right_r

  x/y joystick axes are centered -1.0..1.0.
  r tether axes are unipolar 0.0..1.0, where 0.0 is retracted/short and 1.0 is extended.
  These are descriptor-scale convenience values, not calibrated values.

/gametrak/left
  float x y r

/gametrak/right
  float x y r

/gametrak/buttons
  int buttons

/gametrak/foot
  int 0_or_1
```

Wekinator-compatible message:

```text
/wekinator/control/inputs
  float left_x left_y left_r right_x right_y right_r

  Same value ranges as /gametrak/normalized.
```

Default port:

```text
2434
```

Implemented command:

```bash
gametrak-osc
```

The standalone Processing sketch `processing/gametrak_osc_transmitter/` sends
the same `/gametrak/raw` payload without requiring Python. It also listens on
the same default OSC control port for `/gametrak/init` and
`/gametrak/reconnect`.

Default behavior sends every valid HID report, currently about 75 Hz on the
tested unit. `--rate` is a throttle, not a required frame-rate setting.

OSC control listener:

```text
Default control host: 127.0.0.1
Default control port: 2435

/gametrak/init
  Resend the libgametrak ps2 init sequence on the current HID handle.

/gametrak/reconnect
  Close the current HID handle. The reconnect loop then reopens the GameTrak
  and sends ps2 init again.
```

The control socket is nonblocking and is polled by the main HID loop. HID
open/close/init operations therefore happen in one place, so the HID handle has
a single owner.

The port must remain configurable.

---

## 13. Verification checklist

```text
[x] HIDAPI enumeration finds VID 0x14B7 PID 0x0982.
[x] Raw report dumper opens the device.
[x] Raw report dumper receives 16-byte input reports after ps2 init.
[x] First six fields decode plausibly as 0..4095 values.
[x] Pulling each tether changes the documented R field.
[x] Moving each joystick changes the documented X/Y fields.
[x] Left/right physical mapping is documented.
[x] Footswitch bits are explicitly marked unresolved.
[x] OSC output matches documented addresses and argument order.
[ ] Wekinator receives six floats.
[x] Processing receives six raw integer values.
```

---

## 14. Known uncertainty

Live reports have confirmed the six primary axes and semantic mapping. Remaining
uncertainties are:

```text
- Exact byte-level meaning of bytes 14-15 is still uncertain. Live captures show right-tether-related movement there despite descriptor padding.
- Axis endianness is confirmed little-endian for plausible 0..4095 values.
- Hat neutral value is not yet known.
- Physical meaning of the 12 button bits is not yet known.
- Actual report rate after init has been measured around 76 Hz, but the device's intended nominal rate is not confirmed.
- Input reports require the libgametrak ps2mode init sequence in the current setup.
- Behavior across other GameTrak hardware revisions is unknown.
```

---

## 15. Things not to claim

Do not claim:

```text
- That this device uses proprietary USB transport.
- That this device is not HID.
- That the physical V2.0 label and USB V1.3 string are the same version number.
- That left/right semantic axis order is known without movement tests.
- That all GameTrak revisions behave identically.
```
