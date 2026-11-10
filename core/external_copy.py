from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from core.backup_lifecycle import INCOMPLETE_MARKER
from utils.hashing import hash_file


EXTERNAL_PARTIAL_MARKER = ".michecklist_external_copy_incomplete"


def _manifest(root: Path) -> dict[str, tuple[int, str]]:
    if root.is_file():
        return {root.name: (root.stat().st_size, hash_file(root))}
    manifest: dict[str, tuple[int, str]] = {}
    for item in sorted(path for path in root.rglob("*") if path.is_file()):
        if item.name in {INCOMPLETE_MARKER, EXTERNAL_PARTIAL_MARKER}:
            continue
        manifest[item.relative_to(root).as_posix()] = (item.stat().st_size, hash_file(item))
    return manifest


def copy_verified_backup(source: Path, destination_root: Path, job_id: str) -> dict[str, Any]:
    """Atomically copy and verify one completed local backup to removable storage."""
    source = source.expanduser().resolve()
    destination_root = destination_root.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Local backup awaiting external copy is unavailable: {source}")
    if source.is_dir() and (source / INCOMPLETE_MARKER).exists():
        raise RuntimeError(f"Local backup is not complete and cannot be copied: {source}")
    destination_root.mkdir(parents=True, exist_ok=True)
    final = (destination_root / source.name).resolve()
    partial = (destination_root / f".{source.name}.partial-{job_id}").resolve()
    if final.parent != destination_root or partial.parent != destination_root:
        raise ValueError("External copy destination escaped the configured folder.")

    source_manifest = _manifest(source)
    if not source_manifest:
        raise RuntimeError(f"Local backup contains no verified files: {source}")
    if final.exists():
        if _manifest(final) == source_manifest:
            return {
                "status": "Completed", "source": str(source), "destination": str(final),
                "files": len(source_manifest),
                "bytes": sum(size for size, _digest in source_manifest.values()),
                "already_present": True,
            }
        raise RuntimeError(
            f"External destination already exists but does not match the local backup: {final}"
        )

    if partial.exists():
        if partial.is_dir():
            shutil.rmtree(partial)
        else:
            partial.unlink()

    try:
        if source.is_dir():
            partial.mkdir(parents=True)
            (partial / EXTERNAL_PARTIAL_MARKER).write_text(job_id, encoding="utf-8")
            for relative in source_manifest:
                source_file = source / Path(relative)
                target_file = partial / Path(relative)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)
            marker = partial / EXTERNAL_PARTIAL_MARKER
            if marker.exists():
                marker.unlink()
        else:
            shutil.copy2(source, partial)

        copied_manifest = _manifest(partial)
        if copied_manifest != source_manifest:
            raise RuntimeError("External copy verification failed: file size or SHA-256 mismatch.")
        os.replace(partial, final)
    except Exception:
        try:
            if partial.is_dir():
                shutil.rmtree(partial)
            elif partial.exists():
                partial.unlink()
        except OSError:
            # The drive may have been disconnected. The next retry removes the
            # exact job-specific partial item before copying again.
            pass
        raise

    return {
        "status": "Completed", "source": str(source), "destination": str(final),
        "files": len(source_manifest),
        "bytes": sum(size for size, _digest in source_manifest.values()),
        "already_present": False,
    }
