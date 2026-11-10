from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from copy import copy
from pathlib import Path


def archive_folder(
    source: Path,
    destination_zip: Path,
    include_root: bool = True,
    compression: int = zipfile.ZIP_STORED,
) -> None:
    """Collect a folder into a ZIP, optionally without CPU-intensive compression."""
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"Source folder does not exist: {source}")

    destination_zip.parent.mkdir(parents=True, exist_ok=True)
    # ZIP's DOS timestamp cannot represent dates before 1980. Some restored,
    # migrated, or vendor-created files carry older timestamps; clamp only
    # those entries instead of aborting the entire backup.
    with zipfile.ZipFile(
        destination_zip, "w", compression=compression,
        strict_timestamps=False,
    ) as archive:
        root_path = source if include_root else source
        for file_path in source.rglob("*"):
            if file_path.is_file():
                archive_path = file_path.relative_to(root_path.parent if include_root else root_path)
                archive.write(file_path, arcname=str(archive_path))


def compress_existing_zip(archive_path: Path) -> None:
    """Atomically recompress an existing ZIP after its collection phase."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=archive_path.parent, prefix=f"{archive_path.stem}-compress-",
            suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with (
            zipfile.ZipFile(archive_path, "r") as source,
            zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as target,
        ):
            for member in source.infolist():
                target_member = copy(member)
                target_member.compress_type = zipfile.ZIP_DEFLATED
                with source.open(member, "r") as reader, target.open(target_member, "w") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
        os.replace(temporary_path, archive_path)
        temporary_path = None
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def compress_folder(source: Path, destination_zip: Path, include_root: bool = True) -> None:
    """Create a compressed ZIP in one call for callers that do not defer compression."""
    archive_folder(source, destination_zip, include_root, zipfile.ZIP_DEFLATED)


def compress_file_to_zip(source: Path, destination_zip: Path | None = None) -> Path:
    """Atomically archive one file, validate the ZIP, then remove the source."""
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")

    destination = destination_zip or source.with_suffix(source.suffix + ".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f"{destination.stem}-compress-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            archive.write(source, arcname=source.name)
        with zipfile.ZipFile(temporary_path, "r") as archive:
            if archive.namelist() != [source.name] or archive.testzip() is not None:
                raise RuntimeError(f"Compressed archive validation failed: {destination}")
        os.replace(temporary_path, destination)
        temporary_path = None
        source.unlink()
        return destination
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def extract_zip(archive_path: Path, destination: Path) -> None:
    """Extract a ZIP archive to the destination folder."""
    with zipfile.ZipFile(archive_path, "r") as archive:
        archive.extractall(destination)
