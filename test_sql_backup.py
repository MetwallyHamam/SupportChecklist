from __future__ import annotations

import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from modules.checklist_runner import ChecklistRunner
from modules.sql_backup import SQLBackup
from utils.config_manager import ConfigManager, DEFAULT_CONFIG
from utils.compression import compress_file_to_zip


class SQLBackupTests(unittest.TestCase):
    def _backup(self, compression_enabled: bool) -> SQLBackup:
        return SQLBackup(
            server=".",
            instance="",
            database="SupportDB",
            username="",
            password="",
            compression_enabled=compression_enabled,
        )

    def test_backup_command_uses_native_compression_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            command = self._backup(True).build_backup_command(Path(temp_dir) / "SupportDB.bak")

        self.assertIn("WITH COMPRESSION, CHECKSUM", command)

    def test_backup_command_omits_compression_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            command = self._backup(False).build_backup_command(Path(temp_dir) / "SupportDB.bak")

        self.assertIn("WITH CHECKSUM", command)
        self.assertNotIn("COMPRESSION", command)

    def test_sql_credentials_are_safely_quoted_in_connection_string(self) -> None:
        backup = SQLBackup(
            server="sql-server", instance="", database="SupportDB",
            username="backup-user", password="part;with}symbols",
            use_windows_auth=False,
        )

        connection = backup.build_connection_string()

        self.assertIn("UID={backup-user}", connection)
        self.assertIn("PWD={part;with}}symbols}", connection)
        self.assertNotIn("Trusted_Connection", connection)

    def test_local_server_uses_sql_default_backup_directory_for_staging(self) -> None:
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = [r"C:\SQL\Backup"]
        with patch("modules.sql_backup.pyodbc.connect", return_value=connection):
            directory = self._backup(True).get_default_backup_directory()

        self.assertEqual(directory, Path(r"C:\SQL\Backup"))
        connection.cursor.return_value.execute.assert_called_once()
        connection.close.assert_called_once()

    def test_verified_staging_backup_is_moved_to_configured_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sql-default" / "SupportDB.bak"
            destination = root / "configured" / "Database" / "SupportDB.bak"
            source.parent.mkdir()
            source.write_bytes(b"verified backup")

            result = SQLBackup.move_backup_to_destination(source, destination)

            self.assertEqual(result, destination.resolve())
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"verified backup")

    def test_runner_uses_saved_sql_authentication_credentials(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["sql_server"].update({
            "server_name": "sql-server",
            "username": "backup-user",
            "password": "configured-secret",
            "use_windows_auth": False,
        })
        runner = ChecklistRunner(ConfigManager(data=data))

        runner.prepare_sql_backup()

        self.assertEqual(runner.sql_backup.username, "backup-user")
        self.assertEqual(runner.sql_backup.password, "configured-secret")
        self.assertFalse(runner.sql_backup.use_windows_auth)

    def test_completed_backup_can_be_archived_and_original_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_file = Path(temp_dir) / "SupportDB.bak"
            backup_file.write_bytes(b"database backup contents" * 100)

            archive_file = compress_file_to_zip(backup_file)

            self.assertFalse(backup_file.exists())
            self.assertEqual(archive_file.name, "SupportDB.bak.zip")
            with zipfile.ZipFile(archive_file, "r") as archive:
                self.assertEqual(archive.namelist(), ["SupportDB.bak"])
                self.assertEqual(archive.testzip(), None)

    def test_legacy_database_name_is_used_when_selection_list_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = temp_dir
            data["sql_server"]["database_name"] = "SupportDB"
            data["sql_server"]["databases"] = []
            runner = ChecklistRunner(ConfigManager(data=data))

            _, warnings = runner.get_preflight_messages()
            self.assertNotIn("No databases are selected. Database backup will be skipped.", warnings)
            with patch.object(runner, "prepare_sql_backup"), patch.object(runner, "backup_sql") as backup:
                result = runner.run_database_only()

            self.assertEqual(result["status"], "Completed")
            backup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
