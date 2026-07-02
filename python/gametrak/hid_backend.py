"""Small hidapi helpers for locating and opening the GameTrak."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import hid

from gametrak.constants import (
    GAMETRAK_PID,
    GAMETRAK_VID,
    HID_USAGE_JOYSTICK,
    HID_USAGE_PAGE_GENERIC_DESKTOP,
)


@dataclass(frozen=True)
class HidDeviceInfo:
    path: bytes
    vendor_id: int
    product_id: int
    manufacturer_string: str
    product_string: str
    serial_number: str
    usage_page: int | None
    usage: int | None
    interface_number: int | None

    @classmethod
    def from_hidapi(cls, raw: dict[str, Any]) -> "HidDeviceInfo":
        path = raw.get("path")
        if isinstance(path, str):
            path = path.encode("utf-8")
        if not isinstance(path, bytes):
            raise ValueError(f"hidapi device is missing a bytes path: {raw!r}")

        return cls(
            path=path,
            vendor_id=int(raw.get("vendor_id", 0)),
            product_id=int(raw.get("product_id", 0)),
            manufacturer_string=str(raw.get("manufacturer_string") or ""),
            product_string=str(raw.get("product_string") or ""),
            serial_number=str(raw.get("serial_number") or ""),
            usage_page=_optional_int(raw.get("usage_page")),
            usage=_optional_int(raw.get("usage")),
            interface_number=_optional_int(raw.get("interface_number")),
        )

    @property
    def path_text(self) -> str:
        return self.path.decode("utf-8", errors="replace")

    @property
    def is_joystick_collection(self) -> bool:
        return (
            self.usage_page == HID_USAGE_PAGE_GENERIC_DESKTOP
            and self.usage == HID_USAGE_JOYSTICK
        )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def enumerate_hid_devices() -> list[HidDeviceInfo]:
    return [HidDeviceInfo.from_hidapi(device) for device in hid.enumerate()]


def enumerate_gametrak_devices() -> list[HidDeviceInfo]:
    return [
        device
        for device in enumerate_hid_devices()
        if device.vendor_id == GAMETRAK_VID and device.product_id == GAMETRAK_PID
    ]


def select_gametrak_device(devices: list[HidDeviceInfo]) -> HidDeviceInfo | None:
    for device in devices:
        if device.is_joystick_collection:
            return device
    return devices[0] if devices else None


def open_gametrak(path: bytes | str | None = None, *, use_vid_pid: bool = False) -> hid.device:
    device = hid.device()
    if use_vid_pid:
        device.open(GAMETRAK_VID, GAMETRAK_PID)
        return device

    if path is not None:
        if isinstance(path, str):
            path = path.encode("utf-8")
        device.open_path(path)
        return device

    selected = select_gametrak_device(enumerate_gametrak_devices())
    if selected is not None:
        device.open_path(selected.path)
    else:
        device.open(GAMETRAK_VID, GAMETRAK_PID)
    return device
