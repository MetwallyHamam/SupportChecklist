from copy import deepcopy

import pytest

from utils.config_manager import ConfigError, ConfigManager, DEFAULT_CONFIG


def test_interactive_load_can_create_first_run_config(tmp_path):
    path = tmp_path / "config" / "config.json"
    manager = ConfigManager(config_path=path)

    manager.load_config()

    assert path.exists()
    assert manager.data == DEFAULT_CONFIG


def test_first_run_enables_email_and_encrypts_default_app_password(tmp_path):
    path = tmp_path / "config" / "config.json"
    manager = ConfigManager(config_path=path)

    manager.load_config()

    stored = path.read_text(encoding="utf-8")
    assert manager.get("email", "enabled") is True
    assert manager.get("email", "password") == DEFAULT_CONFIG["email"]["password"]
    assert DEFAULT_CONFIG["email"]["password"] not in stored


def test_service_style_load_does_not_recreate_missing_config(tmp_path):
    path = tmp_path / "config" / "config.json"
    manager = ConfigManager(config_path=path, data=deepcopy(DEFAULT_CONFIG))

    with pytest.raises(ConfigError, match="Configuration file is missing"):
        manager.load_config(create_if_missing=False)

    assert not path.exists()
