# Sample Report Captures

This directory contains JSONL captures from `gametrak-record` and earlier protocol investigation tools. These captures can be replayed over various protocols (OSC, MIDI, WebSockets, stdout, etc.) using `gametrak-playback`.

The current official sample capture is available [here](gametrack_sample_10_second_recording.jsonl):

```text
gametrack_sample_10_second_recording.jsonl
```

It was recorded with `gametrak-record` and includes a metadata row, per-report
raw bytes, decoded HID axes, semantic raw values, and a summary row.

The older captures in `additional_recordings/` preserve raw 16-byte HID
reports from controlled movement tests. They were used to confirm:

```text
raw x  = left_x
raw y  = left_y
raw z  = left_r
raw rx = right_x
raw ry = right_y
raw rz = right_r
```

Keep full reports in captures. Bytes 14-15 are marked as padding by the HID
descriptor, but live captures show right-tether-related movement there.

To make a new capture, use:

```bash
gametrak-record --seconds 10 --label official --out my_gametrack_recording.jsonl
```

The recorder writes JSONL, with one complete JSON object per line. This keeps
captures valid and inspectable even when a recording is interrupted.

Use `gametrak-record --print` to mirror compact decoded values to stderr while
recording, or `gametrak-record --diagnose` to print the attached GameTrak's
HID/USB report without making a capture.
