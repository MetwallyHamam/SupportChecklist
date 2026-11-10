from __future__ import annotations

from pathlib import Path
from typing import List
import hashlib
import json
import shutil
import logging

from utils.compression import archive_folder, compress_existing_zip
from utils.paths import get_app_dir


class FolderBackup:
    """Handles backup of configured folders."""

    def __init__(self, incremental: bool = True):
        self.incremental = incremental
        self.cache_root = get_app_dir() / "config" / "incremental_cache"

    def _fingerprint(self, folder_path: Path) -> str:
        digest = hashlib.sha256()
        for item in sorted((p for p in folder_path.rglob("*") if p.is_file()), key=lambda p: str(p).casefold()):
            stat = item.stat()
            digest.update(str(item.relative_to(folder_path)).encode("utf-8", errors="surrogatepass"))
            digest.update(f"|{stat.st_size}|{stat.st_mtime_ns}".encode("ascii"))
        return digest.hexdigest()

    def backup_folders(self, folders: List[dict[str, str]], destination: Path) -> list[Path]:
        backup_paths: list[Path] = []
        pending_compression: list[tuple[Path, Path, Path, str, Path]] = []
        logger = logging.getLogger(__name__)
        for folder in folders:
            if not folder.get("enabled", False):
                continue

            folder_name = folder.get("name", "backup")
            folder_path = Path(folder.get("path", ""))
            zip_path = destination / f"{folder_name}.zip"
            fingerprint = self._fingerprint(folder_path) if self.incremental else ""
            cache_key = hashlib.sha256(str(folder_path.resolve()).casefold().encode("utf-8")).hexdigest()
            cache_zip = self.cache_root / f"{cache_key}.zip"
            cache_meta = self.cache_root / f"{cache_key}.json"
            if self.incremental and cache_zip.exists() and cache_meta.exists():
                try:
                    metadata = json.loads(cache_meta.read_text(encoding="utf-8"))
                    if metadata.get("fingerprint") == fingerprint:
                        zip_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(cache_zip, zip_path)
                        backup_paths.append(zip_path)
                        continue
                except (OSError, json.JSONDecodeError):
                    pass
            logger.info("Collecting folder into uncompressed archive: %s -> %s", folder_path, zip_path)
            archive_folder(folder_path, zip_path)
            pending_compression.append((zip_path, cache_zip, cache_meta, fingerprint, folder_path))
            backup_paths.append(zip_path)

        if pending_compression:
            logger.info(
                "Folder collection completed; starting deferred compression for %d archive(s)",
                len(pending_compression),
            )
        for zip_path, cache_zip, cache_meta, fingerprint, folder_path in pending_compression:
            logger.info("Compressing collected folder archive: %s", zip_path)
            compress_existing_zip(zip_path)
            if self.incremental:
                self.cache_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(zip_path, cache_zip)
                cache_meta.write_text(json.dumps({
                    "source": str(folder_path.resolve()),
                    "fingerprint": fingerprint,
                }, indent=2), encoding="utf-8")

        return backup_paths
