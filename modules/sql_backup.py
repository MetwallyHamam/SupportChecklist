from __future__ import annotations

import pyodbc
from pathlib import Path
import logging
import os
import shutil
import socket
import time
from uuid import uuid4


UNSUPPORTED_BACKUP_DATABASES = {"tempdb"}


def is_backup_supported(database_name: str) -> bool:
    """Return whether SQL Server permits a regular backup for this database."""
    return database_name.strip().lower() not in UNSUPPORTED_BACKUP_DATABASES


class SQLBackup:
    """Handles SQL Server backup operations."""

    def __init__(
        self,
        server: str,
        instance: str,
        database: str,
        username: str,
        password: str,
        use_windows_auth: bool = True,
        compression_enabled: bool = True,
    ) -> None:
        self.server = server
        self.instance = instance
        self.database = database
        self.username = username
        self.password = password
        self.use_windows_auth = use_windows_auth
        self.compression_enabled = compression_enabled

    def build_server_address(self) -> str:
        if self.instance:
            return f"{self.server}\\{self.instance}"
        return self.server

    def build_connection_string(self) -> str:
        server_address = self.build_server_address()
        driver = "ODBC Driver 17 for SQL Server"
        if self.use_windows_auth:
            return f"DRIVER={{{driver}}};SERVER={server_address};DATABASE=master;Trusted_Connection=yes;"
        username = self._odbc_value(self.username)
        password = self._odbc_value(self.password)
        return f"DRIVER={{{driver}}};SERVER={server_address};DATABASE=master;UID={username};PWD={password};"

    @staticmethod
    def _odbc_value(value: str) -> str:
        """Quote an ODBC value so semicolons and closing braces remain data."""
        return "{" + str(value).replace("}", "}}") + "}"

    def test_connection(self) -> bool:
        connection_string = self.build_connection_string()
        with pyodbc.connect(connection_string, timeout=5) as conn:
            return conn is not None

    def _is_local_server(self) -> bool:
        server = self.server.strip().casefold()
        local_names = {
            ".", "(local)", "localhost", "127.0.0.1", "::1",
            socket.gethostname().casefold(), os.environ.get("COMPUTERNAME", "").casefold(),
        }
        return server in local_names

    def get_default_backup_directory(self) -> Path | None:
        """Return SQL Server's writable default backup directory when local."""
        if not self._is_local_server():
            return None
        conn = pyodbc.connect(self.build_connection_string(), autocommit=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000))")
            row = cursor.fetchone()
            value = str(row[0]).strip() if row and row[0] else ""
            if not value:
                cursor.execute(
                    "DECLARE @path nvarchar(4000); "
                    "EXEC master.dbo.xp_instance_regread "
                    "N'HKEY_LOCAL_MACHINE', N'Software\\Microsoft\\MSSQLServer\\MSSQLServer', "
                    "N'BackupDirectory', @path OUTPUT; SELECT @path"
                )
                row = cursor.fetchone()
                value = str(row[0]).strip() if row and row[0] else ""
            return Path(os.path.expandvars(value)) if value else None
        except pyodbc.Error:
            return None
        finally:
            conn.close()

    def backup_database(self, backup_file: Path) -> Path:
        if not is_backup_supported(self.database):
            raise ValueError(f"Database backup is not supported for {self.database}.")
        requested_path = backup_file.resolve()
        backup_path = requested_path
        staging_directory = self.get_default_backup_directory()
        if staging_directory and staging_directory.resolve() != requested_path.parent:
            backup_path = staging_directory / f"MiChecklist_{uuid4().hex}_{requested_path.name}"
        file_path = str(backup_path)
        sql = self.build_backup_command(backup_path)
        logger = logging.getLogger(__name__)
        logger.info("Executing SQL backup command for database %s -> %s", self.database, file_path)
        conn = pyodbc.connect(self.build_connection_string(), autocommit=True)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
            except pyodbc.Error as exc:
                raise RuntimeError(
                    f"SQL Server could not back up {self.database} to {file_path}. "
                    "Confirm the selected database exists, the login has BACKUP DATABASE "
                    "permission, and the SQL Server service account can write to the backup "
                    f"folder. SQL error: {exc}"
                ) from exc
            # Wait for the backup file to appear and stabilize on disk.
            stable = False
            for attempt in range(20):
                if backup_path.exists() and backup_path.is_file():
                    try:
                        size1 = backup_path.stat().st_size
                    except OSError:
                        size1 = -1
                    time.sleep(0.25)
                    try:
                        size2 = backup_path.stat().st_size
                    except OSError:
                        size2 = -1
                    if size1 == size2 and size1 > 0:
                        stable = True
                        logger.info("Backup file detected and stable: %s (%d bytes)", file_path, size2)
                        break
                time.sleep(0.25)
            if not stable:
                parent = backup_path.parent
                try:
                    contents = [p.name for p in parent.iterdir()]
                except Exception as e:
                    contents = [f"(error listing directory: {e})"]
                logger.error("Backup file not found or not stable after backup. Directory %s contents: %s", parent, contents)
                raise FileNotFoundError(f"Expected backup file not found or unstable: {file_path}")
        finally:
            conn.close()
        return backup_path

    @staticmethod
    def move_backup_to_destination(source: Path, destination: Path) -> Path:
        """Move a verified local SQL staging backup into the configured backup tree."""
        source = source.resolve()
        destination = destination.resolve()
        if source == destination:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if not destination.is_file():
            raise FileNotFoundError(f"SQL backup was not moved to its destination: {destination}")
        return destination

    def verify_backup(self, backup_file: Path) -> None:
        file_path = str(backup_file.resolve())
        escaped_file_path = file_path.replace("'", "''")
        sql = f"RESTORE VERIFYONLY FROM DISK = N'{escaped_file_path}'"
        logger = logging.getLogger(__name__)
        # Safety: verify_backup uses RESTORE VERIFYONLY which is non-destructive.
        logger.info("Executing RESTORE VERIFYONLY for backup file %s", file_path)
        conn = pyodbc.connect(self.build_connection_string(), autocommit=True)
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
        finally:
            conn.close()

    def list_databases(self) -> list[dict[str, str]]:
        """Return a list of databases on the server with basic metadata."""
        sql = "SELECT name, state_desc FROM sys.databases ORDER BY name"
        conn = pyodbc.connect(self.build_connection_string(), autocommit=True)
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            return [
                {"name": row[0], "state": row[1]}
                for row in rows
                if is_backup_supported(str(row[0]))
            ]
        finally:
            conn.close()

    def build_backup_command(self, backup_file: Path) -> str:
        """Build a SQL backup command using native SQL Server compression when enabled."""
        database_name = self.database.replace("]", "]]")
        file_path = str(backup_file.resolve()).replace("'", "''")
        options = ["CHECKSUM"]
        if self.compression_enabled:
            options.insert(0, "COMPRESSION")
        return f"BACKUP DATABASE [{database_name}] TO DISK = N'{file_path}' WITH {', '.join(options)}"
