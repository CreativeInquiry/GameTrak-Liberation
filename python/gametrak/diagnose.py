"""Diagnostic report helpers shared by the GameTrak command-line tools."""

from __future__ import annotations

from dataclasses import dataclass
import json
import platform
import subprocess
from typing import Any

from gametrak.constants import GAMETRAK_MANUFACTURER, GAMETRAK_PID, GAMETRAK_PRODUCT, GAMETRAK_VID
from gametrak.hid_backend import HidDeviceInfo, enumerate_gametrak_devices, open_gametrak, select_gametrak_device


@dataclass(frozen=True)
class HidOpenProbe:
    """Result of a short HID open/close diagnostic probe."""

    mode: str
    ok: bool
    detail: str
    manufacturer_string: str = ""
    product_string: str = ""
    serial_number: str = ""
    report_descriptor_length: int | None = None


@dataclass(frozen=True)
class UsbTopologyEntry:
    """Small macOS USB topology summary for one matching device."""

    name: str
    vendor_id: str
    product_id: str
    manufacturer: str
    serial_number: str
    location_id: str
    speed: str


def diagnostic_report(
    *,
    open_mode: str = "vidpid",
    path: str | None = None,
    include_usb_topology: bool = True,
) -> str:
    """Collect and format a GameTrak diagnostic report.

    The report is intentionally read-only from the GameTrak protocol's point of
    view: it enumerates HID devices and opens/closes a handle, but it does not
    send the ps2 init sequence or read input reports.
    """

    try:
        devices = enumerate_gametrak_devices()
        enumeration_error = None
    except Exception as exc:
        devices = []
        enumeration_error = f"{type(exc).__name__}: {exc}"

    selected = select_gametrak_device(devices) if devices else None
    open_probe = probe_hid_open(open_mode=open_mode, path=path, selected=selected) if enumeration_error is None else None
    usb_entries = collect_macos_usb_topology() if include_usb_topology else []

    return format_diagnostic_report(
        devices=devices,
        selected=selected,
        open_probe=open_probe,
        usb_entries=usb_entries,
        enumeration_error=enumeration_error,
    )


def probe_hid_open(*, open_mode: str, path: str | None, selected: HidDeviceInfo | None) -> HidOpenProbe:
    """Try opening and closing the GameTrak without initializing the protocol."""

    selected_path = path
    if selected_path is None and open_mode != "vidpid" and selected is not None:
        selected_path = selected.path_text

    try:
        device = open_gametrak(selected_path, use_vid_pid=open_mode == "vidpid")
    except Exception as exc:
        return HidOpenProbe(mode=open_mode, ok=False, detail=f"{type(exc).__name__}: {exc}")

    try:
        manufacturer = _device_string(device, "get_manufacturer_string")
        product = _device_string(device, "get_product_string")
        serial = _device_string(device, "get_serial_number_string")
        descriptor_length = _report_descriptor_length(device)
        return HidOpenProbe(
            mode=open_mode,
            ok=True,
            detail="open/close ok",
            manufacturer_string=manufacturer,
            product_string=product,
            serial_number=serial,
            report_descriptor_length=descriptor_length,
        )
    finally:
        try:
            device.close()
        except Exception:
            pass


def format_diagnostic_report(
    *,
    devices: list[HidDeviceInfo],
    selected: HidDeviceInfo | None,
    open_probe: HidOpenProbe | None,
    usb_entries: list[UsbTopologyEntry],
    enumeration_error: str | None = None,
) -> str:
    """Format already-collected diagnostic facts as stable plain text."""

    lines: list[str] = [
        "GameTrak diagnostic report",
        "",
        "Expected USB identity",
        f"  vendor_id: 0x{GAMETRAK_VID:04X} ({GAMETRAK_VID})",
        f"  product_id: 0x{GAMETRAK_PID:04X} ({GAMETRAK_PID})",
        f"  manufacturer_string: {GAMETRAK_MANUFACTURER}",
        f"  product_string: {GAMETRAK_PRODUCT}",
        "  physical label: not reported over USB; tested unit underside says GameTrak V2.0",
        "",
        "Host",
        f"  system: {platform.platform()}",
        "",
        "HID enumeration",
    ]

    if enumeration_error is not None:
        lines.append(f"  error: {enumeration_error}")
    lines.append(f"  matching_devices: {len(devices)}")
    if selected is not None:
        lines.append(f"  selected_path: {selected.path_text}")

    for index, device in enumerate(devices, start=1):
        lines.extend(format_hid_device(index, device))

    lines.extend(["", "HID open test"])
    if open_probe is None:
        lines.append("  skipped")
    else:
        lines.extend(format_open_probe(open_probe))

    lines.extend(["", "macOS USB topology"])
    if usb_entries:
        for index, entry in enumerate(usb_entries, start=1):
            lines.extend(format_usb_topology_entry(index, entry))
    else:
        lines.append("  no matching system_profiler USB entry found or topology unavailable")

    return "\n".join(lines) + "\n"


def format_hid_device(index: int, device: HidDeviceInfo) -> list[str]:
    """Format one hidapi enumeration record."""

    return [
        f"  device_{index}:",
        f"    manufacturer_string: {_or_dash(device.manufacturer_string)}",
        f"    product_string: {_or_dash(device.product_string)}",
        f"    serial_number: {_or_dash(device.serial_number)}",
        f"    vendor_id: 0x{device.vendor_id:04X} ({device.vendor_id})",
        f"    product_id: 0x{device.product_id:04X} ({device.product_id})",
        f"    usage_page: {_format_optional_hex(device.usage_page)}",
        f"    usage: {_format_optional_hex(device.usage)}",
        f"    interface_number: {_or_dash(device.interface_number)}",
        f"    joystick_collection: {str(device.is_joystick_collection).lower()}",
        f"    hidapi_path: {device.path_text}",
    ]


def format_open_probe(probe: HidOpenProbe) -> list[str]:
    """Format the HID open/close probe."""

    lines = [
        f"  mode: {probe.mode}",
        f"  result: {'ok' if probe.ok else 'error'}",
        f"  detail: {probe.detail}",
    ]
    if probe.ok:
        lines.extend(
            [
                f"  manufacturer_string: {_or_dash(probe.manufacturer_string)}",
                f"  product_string: {_or_dash(probe.product_string)}",
                f"  serial_number: {_or_dash(probe.serial_number)}",
                f"  report_descriptor_length: {_or_dash(probe.report_descriptor_length)}",
            ]
        )
    return lines


def format_usb_topology_entry(index: int, entry: UsbTopologyEntry) -> list[str]:
    """Format one macOS system_profiler USB tree entry."""

    return [
        f"  device_{index}:",
        f"    name: {_or_dash(entry.name)}",
        f"    vendor_id: {_or_dash(entry.vendor_id)}",
        f"    product_id: {_or_dash(entry.product_id)}",
        f"    manufacturer: {_or_dash(entry.manufacturer)}",
        f"    serial_number: {_or_dash(entry.serial_number)}",
        f"    location_id: {_or_dash(entry.location_id)}",
        f"    speed: {_or_dash(entry.speed)}",
    ]


def collect_macos_usb_topology() -> list[UsbTopologyEntry]:
    """Return matching macOS USB topology entries from system_profiler."""

    if platform.system() != "Darwin":
        return []

    try:
        completed = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return []

    if completed.returncode != 0:
        return []

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []

    entries: list[UsbTopologyEntry] = []
    for item in _walk_system_profiler_items(payload.get("SPUSBDataType", [])):
        if _matches_gametrak_usb_item(item):
            entries.append(
                UsbTopologyEntry(
                    name=str(item.get("_name") or item.get("name") or ""),
                    vendor_id=str(item.get("vendor_id") or ""),
                    product_id=str(item.get("product_id") or ""),
                    manufacturer=str(item.get("manufacturer") or item.get("manufacturer_string") or ""),
                    serial_number=str(item.get("serial_num") or item.get("serial_number") or ""),
                    location_id=str(item.get("location_id") or ""),
                    speed=str(item.get("speed") or ""),
                )
            )
    return entries


def _walk_system_profiler_items(items: Any) -> list[dict[str, Any]]:
    """Flatten nested ``system_profiler -json`` USB items."""

    if not isinstance(items, list):
        return []

    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        flattened.append(item)
        flattened.extend(_walk_system_profiler_items(item.get("_items", [])))
    return flattened


def _matches_gametrak_usb_item(item: dict[str, Any]) -> bool:
    vendor = str(item.get("vendor_id") or "").lower()
    product = str(item.get("product_id") or "").lower()
    name = str(item.get("_name") or item.get("name") or "").lower()
    manufacturer = str(item.get("manufacturer") or "").lower()

    vendor_matches = f"0x{GAMETRAK_VID:04x}" in vendor or str(GAMETRAK_VID) == vendor
    product_matches = f"0x{GAMETRAK_PID:04x}" in product or str(GAMETRAK_PID) == product
    text_matches = "game-trak" in name or "gametrak" in name or "in2games" in manufacturer
    return (vendor_matches and product_matches) or text_matches


def _device_string(device: object, method_name: str) -> str:
    method = getattr(device, method_name, None)
    if method is None:
        return ""
    try:
        return str(method() or "")
    except Exception:
        return ""


def _report_descriptor_length(device: object) -> int | None:
    method = getattr(device, "get_report_descriptor", None)
    if method is None:
        return None
    try:
        descriptor = method()
    except Exception:
        return None
    return len(descriptor)


def _format_optional_hex(value: int | None) -> str:
    if value is None:
        return "-"
    return f"0x{value:04X} ({value})"


def _or_dash(value: object) -> object:
    if value is None or value == "":
        return "-"
    return value
