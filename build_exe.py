from __future__ import annotations

import argparse
import re
import subprocess
import sys
import shutil
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
COUNTER_FILE = APP_DIR / "michecklist_build.txt"
VERSION_FILE = APP_DIR / "michecklist_version.txt"
DEFAULT_VERSION = (1, 0, 0, 0)


def next_version() -> tuple[int, int, int, int]:
    try:
        text = VERSION_FILE.read_text(encoding="utf-8")
        match = re.search(r"filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text)
        if not match:
            raise ValueError("filevers entry not found")
        current_version = tuple(int(value) for value in match.groups())
        if current_version[:3] < DEFAULT_VERSION[:3]:
            current_version = DEFAULT_VERSION
    except (FileNotFoundError, ValueError):
        current_version = (*DEFAULT_VERSION[:3], DEFAULT_VERSION[3] - 1)

    version = (*current_version[:3], current_version[3] + 1)
    COUNTER_FILE.write_text(f"{version[3]}\n", encoding="utf-8")
    return version


def write_version_file(version: tuple[int, int, int, int]) -> str:
    display_version = ".".join(str(part) for part in version)
    VERSION_FILE.write_text(
        f"""# UTF-8
VSVersionInfo(
    ffi=FixedFileInfo(
        filevers={version},
        prodvers={version},
        mask=0x3f,
        flags=0x0,
        OS=0x4,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0)
    ),
    kids=[
        StringFileInfo([
            StringTable(
                '040904B0',
                [
                    StringStruct('CompanyName', 'MILLENSYS - Support Department'),
                    StringStruct('FileDescription', 'MiChecklist'),
                    StringStruct('FileVersion', '{display_version}'),
                    StringStruct('InternalName', 'MiChecklist'),
                    StringStruct('LegalCopyright', 'Copyright (c) Metwally Hammam - 01000766088'),
                    StringStruct('OriginalFilename', 'MiChecklist.exe'),
                    StringStruct('ProductName', 'MiChecklist'),
                    StringStruct('ProductVersion', '{display_version}'),
                    StringStruct('Author', 'Metwally Hammam')
                ]
            )
        ]),
        VarFileInfo([
            VarStruct('Translation', [1033, 1200])
        ])
    ]
)
""",
        encoding="utf-8",
    )
    (APP_DIR / "version.py").write_text(
        f'__version__ = "{display_version}"\nVERSION = {version!r}\n'
        'COPYRIGHT = "Copyright © 2026 Eng. Metwally Hammam. All rights reserved."\n',
        encoding="utf-8",
    )
    readme_path = APP_DIR / "README.md"
    if readme_path.exists():
        readme = readme_path.read_text(encoding="utf-8")
        readme = re.sub(
            r"Current product version: \*\*[^*]+\*\*\.",
            f"Current product version: **{display_version}**.",
            readme,
            count=1,
        )
        readme_path.write_text(readme, encoding="utf-8")
    return display_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MiChecklist with an incremented version.")
    parser.add_argument("--clean", action="store_true", help="Ask PyInstaller to clean cached build files.")
    args = parser.parse_args()

    metadata_paths = [COUNTER_FILE, VERSION_FILE, APP_DIR / "version.py", APP_DIR / "README.md"]
    snapshots = {
        path: path.read_bytes() if path.exists() else None for path in metadata_paths
    }
    published = False
    try:
        display_version = write_version_file(next_version())
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
        if args.clean:
            command.append("--clean")
        command.append("app.spec")

        print(f"Building MiChecklist.exe version {display_version}")
        result = subprocess.run(command, cwd=APP_DIR, check=False)
        if result.returncode:
            return result.returncode
        service_command = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
        if args.clean:
            service_command.append("--clean")
        service_command.append("service.spec")
        service_result = subprocess.run(service_command, cwd=APP_DIR, check=False)
        if service_result.returncode:
            return service_result.returncode
        package_root = APP_DIR / "dist" / f"MiChecklist-{display_version}-win64"
        if package_root.exists():
            shutil.rmtree(package_root)
        package_root.mkdir(parents=True)
        for name in ("MiChecklist.exe", "MiChecklistService.exe"):
            shutil.copy2(APP_DIR / "dist" / name, package_root / name)
        for name in ("README.md", "USER_GUIDE.md", "SERVICE_INSTALLATION.md"):
            shutil.copy2(APP_DIR / name, package_root / name)
        archive = shutil.make_archive(str(package_root), "zip", package_root)
        shutil.rmtree(package_root)
        print(f"MiChecklist deployment package: {archive}")
        published = True
        return 0
    finally:
        if not published:
            for path, content in snapshots.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)


if __name__ == "__main__":
    raise SystemExit(main())
