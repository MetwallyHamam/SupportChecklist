from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List

from utils.compression import compress_folder
from utils.subprocess_utils import hidden_window_kwargs


@dataclass
class ServiceInfo:
    name: str
    display_name: str
    executable_path: str
    status: str


class ServiceBackup:
    """Manage Windows service discovery and backup operations."""

    def list_services(self, search: str | None = None) -> List[ServiceInfo]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Service -ErrorAction Stop | Select-Object Name,DisplayName,PathName,State | ConvertTo-Json",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_window_kwargs(),
        )
        if result.returncode != 0:
            stderr_text = (result.stderr or result.stdout or "Unknown PowerShell error").strip()
            raise RuntimeError(f"Failed to list Windows services: {stderr_text}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Failed to parse Windows services JSON: {error}") from error

        if isinstance(data, dict):
            data = [data]

        services: List[ServiceInfo] = []
        search_value = str(search or "").strip().lower()
        for record in data:
            name = str(record.get("Name", ""))
            display_name = str(record.get("DisplayName", ""))
            path_name = str(record.get("PathName", ""))
            exe_path = self._extract_executable_path(path_name)
            service = ServiceInfo(
                name=name,
                display_name=display_name,
                executable_path=exe_path,
                status=str(record.get("State", "")),
            )
            if search_value:
                if search_value not in name.lower() and search_value not in display_name.lower():
                    continue
            services.append(service)
        return services

    def _extract_executable_path(self, path_name: str) -> str:
        if not path_name:
            return ""
        match = re.match(r'"([^"]+\.exe)"', path_name)
        if match:
            return match.group(1)
        parts = path_name.split()
        if parts:
            candidate = parts[0]
            if candidate.endswith(".exe"):
                return candidate
        return path_name

    def backup_service_folder(self, executable_path: str, backup_path: Path) -> Path:
        backup_path.mkdir(parents=True, exist_ok=True)
        if not executable_path:
            return backup_path

        service_folder = Path(executable_path).resolve().parent
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_file = backup_path / f"{service_folder.name}_{timestamp}.zip"
        compress_folder(service_folder, zip_file)
        return zip_file
