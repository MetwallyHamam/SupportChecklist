from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path


class RestoreManager:
    def validate(self, backup: Path) -> list[str]:
        errors: list[str] = []
        if not backup.exists():
            return [f"Backup does not exist: {backup}"]
        if backup.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(backup) as archive:
                    bad = archive.testzip()
                    if bad:
                        errors.append(f"Archive contains a corrupt member: {bad}")
            except (OSError, zipfile.BadZipFile) as exc:
                errors.append(str(exc))
        return errors

    def preview(self, backup: Path, destination: Path) -> dict:
        errors = self.validate(backup)
        if errors:
            return {"valid": False, "errors": errors, "files": 0}
        if backup.is_dir():
            count = sum(item.is_file() for item in backup.rglob("*"))
        elif backup.suffix.lower() == ".zip":
            with zipfile.ZipFile(backup) as archive:
                count = sum(not item.is_dir() for item in archive.infolist())
        else:
            count = 1
        return {"valid": True, "errors": [], "files": count, "destination": str(destination)}

    def restore(self, backup: Path, destination: Path, overwrite: bool = False) -> dict:
        preview = self.preview(backup, destination)
        if not preview["valid"]:
            raise ValueError("; ".join(preview["errors"]))
        destination.mkdir(parents=True, exist_ok=True)
        restored = 0
        if backup.is_dir():
            for source in backup.rglob("*"):
                if not source.is_file():
                    continue
                target = destination / source.relative_to(backup)
                if target.exists() and not overwrite:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                restored += 1
        elif backup.suffix.lower() == ".zip":
            with zipfile.ZipFile(backup) as archive:
                for member in archive.infolist():
                    target = (destination / member.filename).resolve()
                    try:
                        target.relative_to(destination.resolve())
                    except ValueError as exc:
                        raise ValueError(f"Unsafe archive member: {member.filename}") from exc
                archive.extractall(destination)
                restored = sum(not member.is_dir() for member in archive.infolist())
        else:
            target = destination / backup.name
            if overwrite or not target.exists():
                shutil.copy2(backup, target)
                restored = 1
        return {"restored_files": restored, "destination": str(destination)}
