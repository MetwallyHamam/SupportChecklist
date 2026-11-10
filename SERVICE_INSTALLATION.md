# MiChecklist Windows Service

Build the application and service executables:

```powershell
python build_exe.py --clean
```

From an elevated PowerShell window in the `dist` directory:

MiChecklist automatically stops and removes the former
`SupportAssistantService` before installing `MiChecklistService` through the
application setup flow.

```powershell
.\MiChecklistService.exe --startup auto install
.\MiChecklistService.exe start
```

Service status and removal:

```powershell
.\MiChecklistService.exe stop
.\MiChecklistService.exe remove
```

The service runs without an interactive login. Saved job definitions are stored
in `queue/saved_jobs.json`, while queued and completed executions are stored in
`queue/job_executions.json`. Every completed scheduled execution also gets its
own dated JSON log under `queue/execution_logs/YYYY-MM-DD/`, named with the
execution time, job ID, and attempt number. It writes `service/health.json` and processes jobs
queued from the Operations tab. Existing `schedules.json` and `jobs.json` files
are migrated automatically on first use. Ensure
the service account has access to SQL Server, IIS, source folders, and backup
destinations. SMTP and SQL secrets are protected with Windows DPAPI.

After upgrading from an earlier version, open the GUI and use **Save All
Configuration** once. This migrates existing user-scoped DPAPI secrets to
machine-scoped DPAPI so the LocalSystem service can decrypt them. Restrict the
application directory to Administrators and SYSTEM because machine-scoped
secrets are decryptable by authorized accounts on the same computer.

For unattended SMTP provisioning, set `MICHECKLIST_SMTP_PASSWORD` only in
the elevated deployment process before launching MiChecklist once. The
value is encrypted into `config/config.json` with machine-scoped DPAPI; it is
not included in the deployment ZIP or source code.
