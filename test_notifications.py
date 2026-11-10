import unittest
from email.headerregistry import Address
from unittest.mock import patch

from core.notifications import EmailNotifier


class EmailNotifierTests(unittest.TestCase):
    def setUp(self):
        self.notifier = EmailNotifier({
            "enabled": True,
            "username": "notifications@example.com",
            "sender_name": "Support Checklist",
            "client_name": "Millensys Hospital",
            "client_branch": "Cairo",
            "client_server": "APP-01",
            "client_comment": "Nightly production backup",
            "to": ["support@example.com"],
            "cc": [],
        })

    def test_builds_branded_multipart_report_from_real_result(self):
        message = self.notifier.build_message(
            {"id": "job-42", "type": "checklist"},
            "completed",
            result={
                "server_name": "APP-01",
                "execution_date": "2026-10-04T02:00:00",
                "duration": "0h 4m 12s",
                "backup_size": "3.4 GB",
                "backup_path": r"D:\Backup\run-42",
                "tasks": [{"task": "Database", "status": "Completed", "detail": "4 databases"}],
            },
        )

        self.assertEqual(message["From"].addresses[0], Address("Support Checklist", "notifications", "example.com"))
        self.assertIn("Completed - Checklist", str(message["Subject"]))
        self.assertTrue(message.is_multipart())
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("APP-01", plain)
        self.assertIn("4 databases", plain)
        self.assertIn("Support Checklist", html)
        self.assertIn("Millensys Hospital", html)
        self.assertIn("Nightly production backup", html)
        self.assertIn("Client name: Millensys Hospital", plain)
        self.assertIn("&nbsp;&nbsp;•&nbsp;&nbsp;", html)
        self.assertIn("3.4 GB", html)
        self.assertIn("4 databases", html)

    def test_escapes_report_content_in_html(self):
        message = self.notifier.build_message(
            {"id": "<job>", "type": "cleanup"}, "failed", "Disk <full>",
            result={"tasks": [{"task": "<Cleanup>", "status": "Failed", "detail": "A & B"}]},
        )
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("&lt;job&gt;", html)
        self.assertIn("Disk &lt;full&gt;", html)
        self.assertIn("A &amp; B", html)

    def test_completed_backup_with_partial_cleanup_is_clearly_a_warning(self):
        message = self.notifier.build_message(
            {"id": "job-warning", "type": "checklist"},
            "completed",
            result={
                "status": "Completed",
                "tasks": [
                    {"task": "IIS", "status": "Running"},
                    {"task": "IIS", "status": "Completed"},
                    {"task": "Cleanup", "status": "Partial", "detail": "2 files failed"},
                ],
                "cleanup_detail": [{
                    "name": "Application logs", "status": "Partial",
                    "detail": "22 files deleted, 2 files failed",
                    "failed_items": [{"file": r"C:\logs\active.log", "error": "Access denied"}],
                }],
                "databases": [{
                    "database": "SupportDB", "file": r"E:\Backup\SupportDB.bak.zip",
                    "compression": "ZIP (SQL Server native + ZIP)", "sha256": "abc123",
                }],
            },
        )

        self.assertIn("Completed with Warnings - Checklist", str(message["Subject"]))
        plain = message.get_body(preferencelist=("plain",)).get_content()
        self.assertNotIn("IIS: Running", plain)
        self.assertIn("IIS: Completed", plain)
        self.assertIn(r"C:\logs\active.log: Access denied", plain)
        self.assertIn(r"E:\Backup\SupportDB.bak.zip", plain)
        self.assertIn("SHA256: abc123", plain)

    def test_service_row_uses_archive_status_and_keeps_runtime_state_in_details(self):
        message = self.notifier.build_message(
            {"id": "service-archive", "type": "checklist"},
            "completed",
            result={
                "services_detail": [{
                    "name": "MiAutoCache",
                    "status": "Completed",
                    "runtime_status": "Running",
                    "path": r"C:\MILLENSYS\VNA_Client\Dependancies\MiAutoChacheService.exe",
                    "archive": r"D:\Backup\Services\Dependancies.zip",
                }],
            },
        )

        plain = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("MiAutoCache: Completed", plain)
        self.assertIn("Runtime state: Running", plain)
        self.assertIn(r"Archive: D:\Backup\Services\Dependancies.zip", plain)

    def test_pytest_runtime_can_never_send_external_email(self):
        message = self.notifier.build_message(
            {"id": "test-recovery", "type": "checklist"},
            "interrupted and recovered",
        )
        with patch.dict("os.environ", {"PYTEST_CURRENT_TEST": "recovery test"}), \
                patch("core.notifications.smtplib.SMTP") as smtp:
            self.notifier.send_message(message)
        smtp.assert_not_called()

    def test_explicit_email_isolation_switch_blocks_delivery(self):
        message = self.notifier.build_message(
            {"id": "isolated-run", "type": "checklist"}, "failed",
        )
        with patch.dict(
            "os.environ", {"PYTEST_CURRENT_TEST": "", "SUPPORT_ASSISTANT_DISABLE_EMAIL": "1"},
            clear=False,
        ), patch("core.notifications.smtplib.SMTP") as smtp:
            self.notifier.send_message(message)
        smtp.assert_not_called()

    def test_notification_categories_can_be_controlled_independently(self):
        self.notifier.settings.update({
            "notify_success": False,
            "notify_warning": True,
            "notify_failure": False,
            "notify_recovery": False,
        })
        warning_result = {
            "tasks": [{"task": "Cleanup", "status": "Partial", "detail": "1 file failed"}],
        }
        with patch.object(self.notifier, "send_message") as send_message:
            self.notifier.notify_result({"id": "success", "type": "checklist"}, "completed")
            self.notifier.notify_result(
                {"id": "warning", "type": "checklist"}, "completed", result=warning_result,
            )
            self.notifier.notify_result({"id": "failed", "type": "checklist"}, "failed")
            self.notifier.notify_result(
                {"id": "recovery", "type": "checklist"}, "interrupted and recovered",
            )
        self.assertEqual(send_message.call_count, 1)
        self.assertIn("Completed with Warnings", str(send_message.call_args.args[0]["Subject"]))


if __name__ == "__main__":
    unittest.main()
