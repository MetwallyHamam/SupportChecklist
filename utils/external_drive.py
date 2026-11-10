from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any


class ExternalDriveUnavailable(RuntimeError):
    """Raised when the configured removable backup volume is not mounted."""


def _volume_identity(root: str) -> tuple[str, str]:
    if os.name != "nt":
        raise OSError("External drive identity is supported on Windows only.")
    label = ctypes.create_unicode_buffer(261)
    filesystem = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint32()
    maximum_component = ctypes.c_uint32()
    flags = ctypes.c_uint32()
    success = ctypes.windll.kernel32.GetVolumeInformationW(
        root, label, len(label), ctypes.byref(serial), ctypes.byref(maximum_component),
        ctypes.byref(flags), filesystem, len(filesystem),
    )
    if not success:
        raise ctypes.WinError()
    return f"{serial.value:08X}", label.value


def capture_external_destination(path: str | Path) -> dict[str, Any]:
    selected = Path(path).expanduser().resolve()
    anchor = selected.anchor
    if not anchor:
        raise ValueError("Select a folder located on the external drive.")
    serial, label = _volume_identity(anchor)
    relative = selected.relative_to(Path(anchor)).as_posix()
    return {
        "enabled": True,
        "volume_serial": serial,
        "volume_label": label,
        "relative_path": relative,
    }


def iter_windows_volumes() -> list[tuple[str, str, str]]:
    if os.name != "nt":
        return []
    mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    volumes: list[tuple[str, str, str]] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        try:
            serial, label = _volume_identity(root)
        except OSError:
            continue
        volumes.append((root, serial, label))
    return volumes


def resolve_external_destination(settings: dict[str, Any]) -> Path:
    expected = str(settings.get("volume_serial", "")).strip().upper()
    if not expected:
        raise ExternalDriveUnavailable("External destination has no saved volume identity.")
    relative = str(settings.get("relative_path", "")).strip().replace("/", os.sep)
    for root, serial, _label in iter_windows_volumes():
        if serial.upper() != expected:
            continue
        destination = Path(root) / relative
        if not destination.is_dir():
            raise ExternalDriveUnavailable(
                f"External drive is connected, but its backup folder is unavailable: {destination}"
            )
        if not os.access(destination, os.W_OK):
            raise ExternalDriveUnavailable(
                f"External backup folder is not writable by MiChecklistService: {destination}"
            )
        return destination
    label = str(settings.get("volume_label", "")).strip() or expected
    raise ExternalDriveUnavailable(f"External backup drive '{label}' is not connected.")
