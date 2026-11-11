from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from utils.subprocess_utils import hidden_window_kwargs


@dataclass
class IISSite:
    name: str
    physical_path: str
    application_pool: str
    bindings: str


class IISManager:
    """Manage IIS detection and configuration backup operations."""

    def detect_sites(self) -> list[IISSite]:
        """Detect IIS websites by invoking PowerShell Get-Website."""
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-Website | Select-Object name,physicalpath,applicationpool,bindings | ConvertTo-Json",
        ]
        output = subprocess.check_output(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_window_kwargs(),
        )
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return []

        if isinstance(data, dict):
            data = [data]

        sites: list[IISSite] = []
        for record in data:
            physical_path = str(record.get("physicalPath") or record.get("physicalpath") or "")
            # Expand environment variables like %SystemDrive%
            physical_path = os.path.expandvars(physical_path)
            sites.append(
                IISSite(
                    name=str(record.get("name", "")),
                    physical_path=physical_path,
                    application_pool=str(record.get("applicationpool") or record.get("applicationPool") or ""),
                    bindings=str(record.get("bindings") or ""),
                )
            )
        return sites

    def backup_configuration(self, backup_path: Path) -> Path:
        """Create an IIS configuration backup using appcmd and copy it into the backup tree."""
        backup_path.mkdir(parents=True, exist_ok=True)
        appcmd = Path(os.environ.get("windir", "C:\\Windows")) / "system32" / "inetsrv" / "appcmd.exe"
        if not appcmd.exists():
            raise FileNotFoundError(f"IIS appcmd.exe not found at {appcmd}")

        backup_name = f"SupportChecklistBackup_{datetime.now():%Y%m%d_%H%M%S}"
        command = [str(appcmd), "add", "backup", backup_name]
        result = subprocess.run(command, capture_output=True, text=True, **hidden_window_kwargs())
        if result.returncode != 0:
            error_message = result.stderr.strip() or result.stdout.strip() or "Unknown appcmd error"
            raise RuntimeError(f"IIS configuration backup failed: {error_message}")

        source_backup = Path(os.environ.get("windir", "C:\\Windows")) / "system32" / "inetsrv" / "backup" / backup_name
        target_backup = backup_path / backup_name
        if not source_backup.exists():
            raise FileNotFoundError(f"Expected IIS backup folder not found: {source_backup}")

        if target_backup.exists():
            shutil.rmtree(target_backup)
        shutil.copytree(source_backup, target_backup)
        return target_backup

    def list_applications(self) -> list[dict[str, str]]:
        """Return a list of IIS applications with their site, path and application pool."""
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Import-Module WebAdministration; Get-WebApplication | Select-Object Path,PhysicalPath,ApplicationPool,ItemXPath | ConvertTo-Json",
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
            raise RuntimeError(f"Failed to list IIS applications: {stderr_text}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        if isinstance(data, dict):
            data = [data]

        apps: list[dict[str, str]] = []
        for record in data:
            site_value = str(record.get("Site") or record.get("site") or "")
            if not site_value:
                item_xpath = str(record.get("ItemXPath") or record.get("itemXPath") or record.get("itemxpath") or "")
                match = re.search(r"@name=['\"]([^'\"]+)['\"]", item_xpath)
                if match:
                    site_value = match.group(1)
            path_value = str(record.get("Path") or record.get("path") or "")
            physical_path = str(record.get("PhysicalPath") or record.get("physicalPath") or record.get("physicalpath") or "")
            apps.append({
                "site": site_value,
                "path": path_value,
                "physical_path": os.path.expandvars(physical_path),
                "application_pool": str(record.get("ApplicationPool") or record.get("applicationPool") or ""),
            })
        return apps

    def list_app_pools(self) -> list[dict[str, str]]:
        """Return a list of application pools and their state."""
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Import-Module WebAdministration; Get-ChildItem IIS:\\AppPools | Select-Object Name,State | ConvertTo-Json",
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
            raise RuntimeError(f"Failed to list IIS application pools: {stderr_text}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        if isinstance(data, dict):
            data = [data]

        pools: list[dict[str, str]] = []
        for record in data:
            pools.append({
                "name": str(record.get("Name") or record.get("name") or ""),
                "state": str(record.get("State") or record.get("state") or ""),
            })
        return pools
