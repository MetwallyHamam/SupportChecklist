# MiChecklist User Guide

MiChecklist automates SQL Server, IIS, Windows service, folder, and cleanup
operations from one Windows application. A background Windows service processes
scheduled jobs even when nobody is logged in.

## 1. Requirements

- Windows Server or Windows 10/11 with administrator access.
- SQL Server ODBC Driver 17 when database backup is used.
- Sufficient free space for the backup and temporary compression files.
- Read access to all sources and write access to the backup destination.
- For scheduled jobs, the `SYSTEM` account must have the required access.
- SMTP credentials and network access to the configured mail server for alerts.

Run `MiChecklist.exe` as Administrator for installation and scheduling
changes.

## 2. Install or upgrade

Keep `MiChecklist.exe` and `MiChecklistService.exe` in the same
folder. The application creates its writable folders beside the executables.

### First installation from the GUI

1. Start `MiChecklist.exe` as Administrator.
2. Complete the settings in **Checklist Configuration**.
3. Select **Save, Validate and Install Service** at the bottom of the page.
4. Correct any reported configuration errors. Warnings can be reviewed before
   continuing.
5. The application installs and starts the Windows service and applies the
   checklist schedule when scheduling is enabled.
6. Confirm that Windows state is `RUNNING` and that a recent heartbeat appears
   in **Operations**.

During installation, MiChecklist detects and removes the former
`SupportAssistantService` Windows service before installing `MiChecklistService`.

The service is never installed before the complete configuration has been saved
and passed checklist preflight validation.

### Upgrade an existing installation - For Old Version Just replace two Exe ( Service and EXE)

1. In **Operations**, stop the Windows service.
2. Close the GUI.
3. Preserve the `config`, `queue`, `logs`, `reports`, `service`, and
   `history.json` items.
4. Replace both executable files with the new versions.
5. Start the GUI as Administrator and save the configuration once.
6. Start the service and confirm its version and heartbeat.

Do not replace a service executable while the service is running.

## 3. First-time configuration

On the first launch, the **MiChecklist First-Time Setup** guides you through the
complete configuration:

1. Backup destination.
2. SQL authentication, connection test, and database selection.
3. IIS website and child-application detection and selection.
4. Windows service detection and selection.
5. Optional additional backup folders and cleanup folders, including automatic
   detection of Logs, Exception, and Exceptions folders beneath selected IIS
   child applications.
6. Checklist frequency, day, and start time.
7. Safe retention, minimum valid backups, and maximum backup age.
8. Primary notification recipient and optional Cc recipients.
9. Final validation and Windows service installation.

Optional checklist areas can be disabled and skipped. The final **Finish** action
saves every selection, validates the checklist, removes the former service when
present, installs and starts `MiChecklistService`, and applies the enabled
schedule. If installation fails, the guide appears again on the next launch.

The detailed **Checklist Configuration** workspace remains available after setup
for later changes. **Save, Validate and Install Service** can be used to validate
and reactivate changed configuration.

### General

- **Backup Root Folder**: local folder or accessible UNC path where timestamped
  `Automatedbackup_YYYYMMDD_HHMMSS` folders are created.
- **Generate SHA256 Hashes**: records hashes for supported backup artifacts.

### External backup copy

The primary backup destination must be a permanent local folder or accessible
UNC path. A removable disk is configured separately with **Copy completed
backups to a removable external drive**.

MiChecklist always finishes and verifies the checklist locally first. It then
queues an independent `external_backup_copy` job. If the registered disk is not
connected, the local checklist remains successful and the copy waits without
blocking later schedules. The disk is matched by volume serial number, so a
changed drive letter is handled automatically.

When the disk returns, MiChecklist copies into a job-specific partial path,
verifies file sizes and SHA-256 hashes, and publishes the final copy atomically.
An interrupted partial copy is removed and only the copy is retried; SQL, IIS,
service, folder, and cleanup operations are not rerun. Email notifications are
sent after local completion, when the external copy starts, and after the copy
is verified and safe to remove.

With **Copy only the latest verified backup**, a newer local backup supersedes
older pending copy jobs. Local retention protects every backup still awaiting
external verification.

A SharePoint or OneDrive web URL is not a filesystem destination. Use a local
synchronized folder or a supported upload integration. Mapped drive letters are
not reliable for a service; use a UNC path instead.

### SQL Server

1. Enable database backup.
2. Enter the server and optional instance.
3. Choose Windows authentication or enter SQL credentials.
4. Test the connection.
5. Refresh the database list and select the required databases.
6. Choose the compression options:
   - **SQL Server native backup compression** adds `WITH COMPRESSION` to the SQL
     backup command. The output remains a `.bak` file.
   - **ZIP compression** verifies the completed `.bak`, creates a
     WinRAR-compatible `.bak.zip`, validates it, and only then removes the raw
     `.bak`.
7. Save SQL settings.

`tempdb` is excluded because SQL Server does not support backing it up normally.

### IIS

1. Enable IIS backup and automatic site detection.
2. Choose whether to include IIS configuration.
3. Refresh sites, child applications, and application pools.
4. Select only the sites and applications that are required.
5. Save IIS settings.

Large IIS folders can take considerable time to collect and compress.

### Windows services

1. Enable service backup.
2. Refresh the service list.
3. Select the services whose executable folders should be archived.
4. Save service settings.

### Additional folders

Use **Folders** to add other source directories. Each entry can be enabled or
disabled independently. Do not select a backup destination inside its source.

### Cleanup

Cleanup tasks delete files from configured paths. Review paths, extensions,
age rules, and detected IIS/service log locations carefully. Run cleanup-only
manually before enabling unattended cleanup.

## 4. Run a manual checklist

1. Open **Checklist Dashboard**.
2. Select **Start Checklist**.
3. Watch the current task and progress indicators.
4. Review the completion message, **Checklist Logs**, and **Backup History**.

**Run Cleanup Only** performs enabled cleanup tasks without creating a backup.
**Stop Checklist** requests a controlled stop; the current filesystem or SQL
operation may need time to reach a safe stopping point.

## 5. Configure the complete scheduled checklist

In **Checklist Configuration > Schedule**:

1. Enable automated checklist scheduling.
2. Select weekly, daily, every N days, or Windows startup.
3. Set the run time and any weekday or interval.
4. Enable missed-run execution if a powered-off server should catch up later.
5. Optionally enable deletion of previous automated backups.
6. Save the schedule and select **Apply Elevated Checklist Schedule**.
7. Use **Refresh Schedule Status** to verify the last result and next run.

The Windows scheduled task wakes the application and queues work. The Windows
service performs the actual backup. Both must therefore be installed and
healthy.

## 6. Transactional retention

Open **Checklist Configuration > Retention**.

- Set the maximum backup age.
- Set **Always preserve at least** to the minimum number of successful backup
  generations that must remain.
- **Keep only the latest valid scheduled backup** is enabled by default for daily,
  weekly, monthly, and interval schedules. It can be disabled or the minimum can
  be increased in Retention settings.

Retention is applied only after the new scheduled backup completes successfully.
The new valid backup is protected before the previous generation is removed. If
the new run fails, the existing valid backup is preserved. Incomplete folders are
handled separately by restart recovery.

## 7. Restart and incomplete-backup recovery

Every active automated backup contains a hidden incomplete marker. If Windows or
the service stops during the job, the service performs these actions at startup:

1. Detects the interrupted scheduled execution.
2. Deletes its proven incomplete backup folder.
3. Sends an **Interrupted and Recovered** email when recovery notifications are
   enabled.
4. Re-queues the same scheduled job from the beginning.
5. Updates `service/health.json` and writes the recovery to the service log.

The service cannot send email while the server is powered off. The recovery
notification is sent as soon as the service starts again. Manually queued
checklists are not automatically restarted; retry them from **Operations**.

## 8. Email notifications

In **Checklist Configuration > Notifications**:

1. Enable SMTP notifications.
2. Enter the SMTP server, port, username, and app password.
3. Enable STARTTLS when required.
4. Enter comma-separated To and optional Cc addresses.
5. Choose each notification category independently:
   - successful jobs;
   - jobs completed with warnings;
   - failed jobs;
   - interrupted jobs recovered and restarted after service startup.
6. Enter client, branch, and server information for report headers.
7. Save notification settings. The GUI and Windows service use the same choices.

Passwords are encrypted in `config/config.json` using machine-scoped Windows
DPAPI. Restrict the installation folder to Administrators and `SYSTEM`.

Failure notifications cover ordinary job errors and script failures. Restart
recovery alerts use their own notification option.

## 9. Script Scheduler

The **Script Scheduler** runs existing PowerShell, CMD, or BAT cleanup scripts
through the Windows service.

### Add a script schedule

1. Enter a descriptive schedule name.
2. Select **Browse Script** and choose a `.ps1`, `.cmd`, or `.bat` file.
3. Choose a frequency:
   - **Every N Minutes**
   - **Daily**
   - **Weekly**
   - **Monthly**
4. Configure the interval, time, weekday, or day of month as applicable.
5. Set an execution timeout.
6. Choose whether a missed occurrence should run when the server returns.
7. Select **Add Script Schedule**.

Use **Run Selected Now** to test the script through the service. The scheduled
execution captures its exit code, duration, and recent standard output/error.
A nonzero exit code or timeout marks the job failed and triggers a failure email.

Important considerations:

- Scripts run as Windows `SYSTEM`, not as the interactive user.
- Use absolute paths inside scripts.
- UNC resources must grant access to the server computer/service identity.
- Avoid mapped drives and user-profile-dependent commands.
- Test destructive cleanup scripts against a safe folder first.
- If a script launches another process, make it wait and return the correct exit
  code so MiChecklist can determine success accurately.

## 10. Operations and monitoring

Open **Operations** to view:

- Windows service installation and running state.
- Latest heartbeat, active job ID, elapsed running minutes, and startup recovery.
- Persistent queued, running, completed, failed, and interrupted executions.
- Independent scheduled jobs.

Use **Retry Selected Failed Job** only after correcting its reported cause.
Long-running backups refresh their health heartbeat at the configured service
interval. A stale heartbeat generally means the service stopped or the server is
unavailable.

Important operational files:

- `service/health.json`: latest service and running-job health.
- `queue/saved_jobs.json`: saved schedules.
- `queue/job_executions.json`: execution queue and results.
- `queue/execution_logs/YYYY-MM-DD/`: individual scheduled execution records.
- `logs/`: daily application and service logs.
- `reports/`: checklist JSON and Excel reports.
- `history.json`: successful checklist history displayed in the GUI.

## 11. Logs and history

Use **Checklist Logs** to filter by date, text, severity, or module. Logs can be
exported to CSV. Use **Backup History** for completed checklist dates, duration,
status, and backup size.

For service problems, inspect the latest
`logs/michecklist_service_YYYY-MM-DD.log` and the matching queue execution
record.

## 12. Restore workflow

In **Operations**, select **Restore Backup**:

1. Choose a backup folder or ZIP archive.
2. Choose a separate restore destination.
3. Review the validation and file count.
4. Confirm the restore.

Existing destination files are skipped. ZIP integrity and unsafe archive paths
are checked first.

This workflow extracts or copies backup artifacts; it does not automatically
restore a `.bak` into SQL Server. Database recovery must be performed with SQL
Server Management Studio or an approved SQL restore procedure. Regularly test a
full database restore on a non-production SQL instance.

## 13. Troubleshooting

### Scheduled job does not run

- Confirm the Windows service is installed and running.
- Confirm the Windows scheduled task is installed and has a next-run time.
- Check service heartbeat and queue status in Operations.
- Verify that the configuration and destination exist beside the running
  executables.

### Backup destination unavailable

- Use a local path or UNC path rather than a mapped drive.
- Grant `SYSTEM` or the service identity write permission.
- Confirm adequate free space for raw and compressed temporary files.

### SQL backup fails

- Test the SQL connection in the GUI.
- Confirm the SQL login has `BACKUP DATABASE` permission.
- Confirm the SQL Server service account can write to the backup location.
- Review the SQL error in the service log.

### Script works manually but fails in the service

- Run it under the same service identity or test with `SYSTEM` permissions.
- Replace mapped drives and relative paths with UNC/absolute paths.
- Ensure the script returns exit code `0` on success.
- Increase the schedule timeout if appropriate.

### No notification received

- Confirm SMTP notifications and the applicable success, warning, failure, or
  recovery option are enabled.
- Verify recipients, SMTP port, STARTTLS setting, and app password.
- Confirm outbound SMTP access from the server.
- Look for `email failed` messages in the service log.

## 14. Recommended operating practice

- Keep at least two successful generations when storage permits.
- Keep an additional off-server or immutable copy.
- Review failed and interrupted-job emails immediately.
- Check service heartbeat daily or integrate it with server monitoring.
- Test ZIP extraction and SQL database restore regularly.
- Document recovery time and recovery point objectives for each server.

An existing file is not sufficient proof of recovery. A backup is dependable
only after integrity verification and a successful restore test.
