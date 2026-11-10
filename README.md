# MiChecklist

Current product version: **1.0.0.8**.

MiChecklist is a Windows desktop and service application for automated
support checklists, backups, cleanup, monitoring, and recovery.

See [USER_GUIDE.md](USER_GUIDE.md) for installation, configuration,
scheduling, monitoring, recovery, script automation, and troubleshooting.

## Main workspaces

- **Checklist Dashboard** runs the support backup and cleanup checklist.
- **Checklist Configuration** manages IIS, SQL, service, folder, cleanup, and
  scheduling settings.
- **Checklist Logs** and **Backup History** provide operational visibility.

## Run from source

```powershell
pip install -r requirements.txt
python app.py
```

Python 3.10 or newer is required. Some checklist operations require Windows
administrator access and an installed SQL Server ODBC driver.

## Build the Windows executable

```powershell
python build_exe.py --clean
```

The executable is written to `dist\MiChecklist.exe`. Build metadata is
generated in `michecklist_version.txt` and the fourth version component is
incremented for every build.

Copyright © 2026 Eng. Metwally Hammam. All rights reserved.
