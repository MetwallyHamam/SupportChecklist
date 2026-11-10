from __future__ import annotations

import os
import re
from pathlib import Path


def classify_storage_path(value: str) -> str:
    text = str(value).strip().strip('"')
    if text.startswith("\\\\"):
        return "unc"
    if re.match(r"^[A-Za-z]:[\\/]", text):
        drive = text[:2]
        try:
            import ctypes
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
            if drive_type == 4:
                return "mapped_network_drive"
        except Exception:
            pass
    lowered = text.casefold()
    if "onedrive" in lowered:
        return "onedrive_sync"
    if lowered.startswith(("https://", "http://")) and ("sharepoint" in lowered or "onedrive" in lowered):
        return "cloud_url"
    return "local"


def storage_warnings(value: str, service_mode: bool = False) -> list[str]:
    kind = classify_storage_path(value)
    warnings: list[str] = []
    if kind == "mapped_network_drive" and service_mode:
        warnings.append("Windows services cannot reliably use mapped drive letters; use a UNC path instead.")
    elif kind == "onedrive_sync" and service_mode:
        warnings.append("A OneDrive sync folder requires the service to run as the same user that runs OneDrive.")
    elif kind == "cloud_url":
        warnings.append("Direct OneDrive/SharePoint web URLs are not filesystem paths; use a synchronized folder or UNC path.")
    return warnings
