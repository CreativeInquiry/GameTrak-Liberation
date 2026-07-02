from __future__ import annotations

from gametrak.diagnose import (
    HidOpenProbe,
    UsbTopologyEntry,
    format_diagnostic_report,
)
from gametrak.hid_backend import HidDeviceInfo


def test_format_diagnostic_report_includes_expected_identity_and_device_fields() -> None:
    device = HidDeviceInfo(
        path=b"IOService:/example/Game-Trak",
        vendor_id=0x14B7,
        product_id=0x0982,
        manufacturer_string="In2Games Ltd.",
        product_string="Game-Trak V1.3",
        serial_number="",
        usage_page=0x01,
        usage=0x04,
        interface_number=0,
    )
    probe = HidOpenProbe(
        mode="vidpid",
        ok=True,
        detail="open/close ok",
        manufacturer_string="In2Games Ltd.",
        product_string="Game-Trak V1.3",
        report_descriptor_length=83,
    )
    usb_entry = UsbTopologyEntry(
        name="Game-Trak V1.3",
        vendor_id="0x14b7",
        product_id="0x0982",
        manufacturer="In2Games Ltd.",
        serial_number="",
        location_id="0x12300000",
        speed="Up to 12 Mb/s",
    )

    report = format_diagnostic_report(
        devices=[device],
        selected=device,
        open_probe=probe,
        usb_entries=[usb_entry],
    )

    assert "GameTrak diagnostic report" in report
    assert "vendor_id: 0x14B7 (5303)" in report
    assert "product_id: 0x0982 (2434)" in report
    assert "matching_devices: 1" in report
    assert "selected_path: IOService:/example/Game-Trak" in report
    assert "joystick_collection: true" in report
    assert "report_descriptor_length: 83" in report
    assert "location_id: 0x12300000" in report


def test_format_diagnostic_report_handles_no_device() -> None:
    report = format_diagnostic_report(
        devices=[],
        selected=None,
        open_probe=HidOpenProbe(mode="vidpid", ok=False, detail="OSError: not found"),
        usb_entries=[],
    )

    assert "matching_devices: 0" in report
    assert "result: error" in report
    assert "detail: OSError: not found" in report
