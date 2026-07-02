#!/usr/bin/env python3
"""List HID devices and highlight the GameTrak if present."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gametrak.constants import GAMETRAK_MANUFACTURER, GAMETRAK_PID, GAMETRAK_PRODUCT, GAMETRAK_VID
from gametrak.hid_backend import HidDeviceInfo, enumerate_hid_devices


def _hex_or_blank(value: int | None, width: int = 4) -> str:
    if value is None:
        return ""
    return f"0x{value:0{width}X}"


def is_gametrak(device: HidDeviceInfo) -> bool:
    return device.vendor_id == GAMETRAK_VID and device.product_id == GAMETRAK_PID


def build_table(devices: list[HidDeviceInfo]) -> Table:
    table = Table(title="HID Devices", show_lines=False)
    table.add_column("Target")
    table.add_column("VID")
    table.add_column("PID")
    table.add_column("Manufacturer")
    table.add_column("Product")
    table.add_column("Usage Page")
    table.add_column("Usage")
    table.add_column("Interface")
    table.add_column("Serial")
    table.add_column("Path", overflow="fold")

    for device in devices:
        target = "GameTrak" if is_gametrak(device) else ""
        style = "bold green" if target else None
        table.add_row(
            target,
            _hex_or_blank(device.vendor_id),
            _hex_or_blank(device.product_id),
            device.manufacturer_string,
            device.product_string,
            _hex_or_blank(device.usage_page, width=2),
            _hex_or_blank(device.usage, width=2),
            str(device.interface_number if device.interface_number is not None else ""),
            device.serial_number,
            device.path_text,
            style=style,
        )

    return table


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="print all HID devices instead of only the GameTrak match",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()

    try:
        devices = enumerate_hid_devices()
    except Exception as exc:  # hidapi can raise platform-specific errors.
        console.print(f"[bold red]Failed to enumerate HID devices:[/] {exc}")
        return 2

    matches = [device for device in devices if is_gametrak(device)]
    visible_devices = devices if args.all else matches

    if visible_devices:
        console.print(build_table(visible_devices))

    if matches:
        first = matches[0]
        manufacturer = first.manufacturer_string or GAMETRAK_MANUFACTURER
        product = first.product_string or GAMETRAK_PRODUCT
        console.print(
            f"[bold green]Found {product} / {manufacturer} "
            f"at VID 0x{GAMETRAK_VID:04X} PID 0x{GAMETRAK_PID:04X}.[/]"
        )
        return 0

    console.print(
        f"[bold red]GameTrak not found.[/] Expected VID 0x{GAMETRAK_VID:04X} "
        f"PID 0x{GAMETRAK_PID:04X}."
    )
    console.print("Run with --all to inspect every visible HID device.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
