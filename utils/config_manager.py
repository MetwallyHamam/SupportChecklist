from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .encryption import decrypt_password, encrypt_password
from .paths import get_app_dir
from .process_lock import process_lock


DEFAULT_CONFIG = {
    "general": {
        "backup_root_folder": "",
        "compression_enabled": True,
        "generate_sha256_hashes": True,
        "incremental_backups": True,
        "setup_completed": False,
    },
    "retention": {
        "enabled": True,
        "max_age_days": 90,
        "minimum_successful_backups": 1,
    },
    "email": {
        "enabled": True,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "use_tls": True,
        "username": "millensysnotifications@gmail.com",
        "sender_name": "MiChecklist",
        "subject_template": "[{company} {product}] {status} - {job_type}",
        "client_name": "",
        "client_branch": "",
        "client_server": "",
        "client_comment": "",
        "password": "uvec tcjb ikar mfun",
        "to": [],
        "cc": [],
        "notify_success": True,
        "notify_warning": True,
        "notify_failure": True,
        "notify_recovery": True,
        "notify_module_success": False,
    },
    "service": {
        "poll_seconds": 5,
        "heartbeat_seconds": 30,
    },
    "external_copy": {
        "enabled": False,
        "volume_serial": "",
        "volume_label": "",
        "relative_path": "",
        "configured_path": "",
        "copy_latest_only": True,
        "check_interval_minutes": 5,
        "reminder_interval_minutes": 120,
        "migration_requires_local_destination": False,
    },
    "sql_server": {
        "backup_enabled": True,
        "archive_compression_enabled": True,
        "server_name": "",
        "instance_name": "",
        "database_name": "",
        "username": "",
        "password": "",
        "use_windows_auth": True,
        "databases": [],
    },
    "iis": {
        "backup_enabled": True,
        "auto_detect_sites": True,
        "include_iis_configuration": True,
        "selected_sites": [],
        "selected_apps": [],
        "selected_cleanup_apps": [],
        "selected_pools": [],
    },
    "services": {
        "backup_enabled": True,
        "service_search_filter": "Mi",
        "selected_services": [],
    },
    "folder_backup": {
        "folders": [],
    },
    "cleanup": {
        "enabled": False,
        "tasks": [],
    },
    "schedule": {
        "enabled": False,
        "mode": "weekly",
        "weekday": "Sunday",
        "days_interval": 7,
        "start_time": "02:00",
        "run_missed_schedule": True,
        "run_at_startup": False,
        "test_run_after_restart": False,
        "delete_previous_automated_backups": True,
        "hide_to_tray": True,
    },
}


class ConfigError(Exception):
    pass


def get_default_config_path() -> Path:
    """Return a writable config path beside the executable when packaged."""
    return get_app_dir() / "config" / "config.json"


def apply_missing_defaults(data: dict[str, Any], defaults: dict[str, Any]) -> bool:
    """Populate absent config values while preserving user settings."""
    changed = False
    for key, default_value in defaults.items():
        if key not in data:
            data[key] = deepcopy(default_value)
            changed = True
        elif isinstance(default_value, dict) and isinstance(data[key], dict):
            changed = apply_missing_defaults(data[key], default_value) or changed
    return changed


def migrate_external_destination(data: dict[str, Any]) -> bool:
    """Convert the former direct-to-removable-drive mode into copy-after-backup settings."""
    if "external_copy" in data or "external_destination" not in data:
        return False
    legacy = dict(data.get("external_destination", {}) or {})
    was_enabled = bool(legacy.get("enabled", False))
    data["external_copy"] = {
        "enabled": False,
        "volume_serial": str(legacy.get("volume_serial", "")),
        "volume_label": str(legacy.get("volume_label", "")),
        "relative_path": str(legacy.get("relative_path", "")),
        "configured_path": str(data.get("general", {}).get("backup_root_folder", "")) if was_enabled else "",
        "copy_latest_only": True,
        "check_interval_minutes": int(legacy.get("check_interval_minutes", 5)),
        "reminder_interval_minutes": int(legacy.get("reminder_interval_minutes", 120)),
        "migration_requires_local_destination": was_enabled,
    }
    return True


@dataclass
class ConfigManager:
    config_path: Path = field(default_factory=get_default_config_path)
    data: dict[str, Any] = field(default_factory=dict)

    def load_config(self, create_if_missing: bool = True) -> None:
        """Load configuration from JSON and decrypt sensitive values."""
        with process_lock(self.config_path):
            self._load_config_locked(create_if_missing)

    def _load_config_locked(self, create_if_missing: bool = True) -> None:
        if not self.config_path.exists():
            if not create_if_missing:
                raise ConfigError(
                    f"Configuration file is missing: {self.config_path}. "
                "Open MiChecklist, save all configuration, then restart the service."
                )
            self.data = deepcopy(DEFAULT_CONFIG)
            self.save_config()
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Unable to load configuration: {exc}") from exc

        if not isinstance(self.data, dict):
            raise ConfigError("Unable to load configuration: root value must be a JSON object")

        for section_name in ("sql_server", "email"):
            section = self.data.setdefault(section_name, {})
            password = section.get("password")
            if password:
                try:
                    section["password"] = decrypt_password(password)
                except Exception as exc:
                    raise ConfigError(
                        f"Unable to decrypt the saved {section_name.replace('_', ' ')} password. "
                        "Re-enter the credential and save the configuration."
                    ) from exc

        migrated_external = migrate_external_destination(self.data)
        if apply_missing_defaults(self.data, DEFAULT_CONFIG) or migrated_external:
            self.save_config()

        # Deployment automation can provide the SMTP app password once. It is
        # immediately persisted with machine-scoped DPAPI and removed from this
        # process environment so interactive users never need to enter it.
        provisioned_password = os.environ.pop(
            "MICHECKLIST_SMTP_PASSWORD",
            os.environ.pop("SUPPORTASSISTANT_SMTP_PASSWORD", ""),
        )
        email_section = self.data.setdefault("email", {})
        if provisioned_password and not email_section.get("password"):
            email_section["password"] = provisioned_password
            self.save_config()

    def save_config(self) -> None:
        """Save configuration to JSON, encrypting sensitive values."""
        with process_lock(self.config_path):
            output_data = deepcopy(self.data)
            temporary = None
            try:
                for section_name in ("sql_server", "email"):
                    section = output_data.setdefault(section_name, {})
                    password = section.get("password", "")
                    if password:
                        section["password"] = encrypt_password(password)
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.config_path.parent,
                    prefix="config-", suffix=".tmp", delete=False,
                ) as handle:
                    json.dump(output_data, handle, indent=4)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary, self.config_path)
            except Exception as exc:
                raise ConfigError(f"Unable to save configuration: {exc}") from exc
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.data.setdefault(section, {})[key] = value

    def get_section(self, section: str) -> dict[str, Any]:
        return self.data.get(section, {})
