from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Any


BRAND_NAME = "Support Checklist"
BRAND_COLOR = "#155eef"


def _external_delivery_disabled() -> bool:
    """Prevent automated tests and explicitly isolated runs from sending email."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return os.environ.get("SUPPORT_ASSISTANT_DISABLE_EMAIL", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _safe(value: Any, default: str = "-") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _status_style(status: str) -> tuple[str, str, str]:
    normalized = status.lower().replace("_", " ")
    if "warning" in normalized or "partial" in normalized:
        return "Completed with Warnings", "#b54708", "#fffaeb"
    if normalized in {"completed", "success", "successful"}:
        return "Completed", "#067647", "#ecfdf3"
    if "skip" in normalized:
        return normalized.title(), "#344054", "#f2f4f7"
    if "cancel" in normalized:
        return normalized.title(), "#b54708", "#fffaeb"
    return normalized.title(), "#b42318", "#fef3f2"


def _detail_rows(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for task in result.get("tasks", []) or []:
        if isinstance(task, dict) and str(task.get("status", "")).casefold() != "running":
            rows.append((_safe(task.get("task"), "Task"), _safe(task.get("status")), _safe(task.get("detail"), "")))
    for key, label in (("cleanup_detail", "Cleanup"), ("databases", "Database"), ("services_detail", "Service")):
        for item in result.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("database") or item.get("display_name") or label
            if key == "databases":
                parts = [
                    f"File: {item.get('file', '-')}",
                    f"Compression: {item.get('compression', '-')}",
                ]
                if item.get("sha256"):
                    parts.append(f"SHA256: {item['sha256']}")
                detail = " | ".join(parts)
            elif key == "cleanup_detail" and item.get("failed_items"):
                failures = "; ".join(
                    f"{failure.get('file', '-')}: {failure.get('error', 'Unable to delete')}"
                    for failure in item.get("failed_items", [])
                )
                detail = f"{item.get('detail', '')} | Failed: {failures}"
            elif key == "services_detail":
                parts = []
                if item.get("runtime_status"):
                    parts.append(f"Runtime state: {item['runtime_status']}")
                if item.get("path"):
                    parts.append(f"Executable: {item['path']}")
                if item.get("archive"):
                    parts.append(f"Archive: {item['archive']}")
                detail = " | ".join(parts)
            else:
                detail = item.get("detail") or item.get("message") or item.get("path") or ""
            rows.append((_safe(name, label), _safe(item.get("status"), "Completed"), _safe(detail, "")))
    return rows


def _has_warnings(result: dict[str, Any]) -> bool:
    return any(
        str(item.get("status", "")).casefold() in {"partial", "warning", "completed with warnings"}
        for key in ("tasks", "cleanup_detail")
        for item in (result.get(key, []) or [])
        if isinstance(item, dict)
    )


class EmailNotifier:
    def __init__(self, settings: dict):
        self.settings = settings

    def build_message(self, job: dict, status: str, detail: str = "", result: dict[str, Any] | None = None) -> EmailMessage:
        result = result or {}
        sender_address = _safe(self.settings.get("username"), "")
        sender_name = _safe(self.settings.get("sender_name"), BRAND_NAME).replace("\r", " ").replace("\n", " ")
        recipients = list(self.settings.get("to", []))
        cc = list(self.settings.get("cc", []))
        completed_with_warnings = status.casefold() == "completed" and _has_warnings(result)
        display_status = "completed with warnings" if completed_with_warnings else status
        status_label, status_color, status_background = _status_style(display_status)
        job_type = _safe(job.get("type")).replace("_", " ").title()
        job_id = _safe(job.get("id"))
        server = _safe(result.get("server_name") or result.get("computer_name"))
        execution_date = _safe(result.get("execution_date") or result.get("started_at"))
        duration = _safe(result.get("duration"))
        backup_size = _safe(result.get("backup_size"))
        backup_path = _safe(result.get("backup_path"))
        error_detail = detail or _safe(result.get("error"), "")
        if completed_with_warnings and not error_detail:
            error_detail = (
                "The backup completed and passed final checks, but one or more "
                "non-backup operations require attention. Review the warning rows below."
            )
        rows = _detail_rows(result)
        client_name = _safe(self.settings.get("client_name"), "")
        client_branch = _safe(self.settings.get("client_branch"), "")
        client_server = _safe(self.settings.get("client_server"), "")
        client_comment = _safe(self.settings.get("client_comment"), "")

        message = EmailMessage()
        message["Subject"] = f"[{BRAND_NAME}] {status_label} - {job_type}"
        message["From"] = formataddr((sender_name, sender_address))
        message["To"] = ", ".join(recipients)
        if cc:
            message["Cc"] = ", ".join(cc)

        plain_lines = [
            f"{BRAND_NAME} job report", "",
            f"Client name: {client_name or '-'}", f"Branch: {client_branch or '-'}",
            f"Client server: {client_server or '-'}", f"Comment: {client_comment or '-'}", "",
            f"Status: {status_label}", f"Job: {job_type}",
            f"Job ID: {job_id}", f"Server: {server}", f"Executed: {execution_date}",
            f"Duration: {duration}", f"Backup size: {backup_size}", f"Backup path: {backup_path}",
        ]
        if error_detail:
            plain_lines.extend(["", f"Details: {error_detail}"])
        if rows:
            plain_lines.extend(["", "Report details:"])
            plain_lines.extend(f"- {name}: {row_status}" + (f" — {row_detail}" if row_detail else "") for name, row_status, row_detail in rows)
        message.set_content("\n".join(plain_lines))

        metrics = [("Job type", job_type), ("Server", server), ("Executed", execution_date), ("Duration", duration), ("Backup size", backup_size), ("Backup path", backup_path)]
        cells = []
        for label, value in metrics:
            cells.append(
                f'<td style="width:50%;padding:9px 12px;border-bottom:1px solid #eaecf0;vertical-align:top">'
                f'<div style="color:#667085;font-size:12px;text-transform:uppercase;letter-spacing:.04em">{escape(label)}</div>'
                f'<div style="color:#101828;font-size:14px;margin-top:3px;word-break:break-word">{escape(value)}</div></td>'
            )
        metric_rows = "".join(f"<tr>{''.join(cells[i:i + 2])}</tr>" for i in range(0, len(cells), 2))

        detail_html = ""
        if rows:
            table_rows = []
            for name, row_status, row_detail in rows:
                label, color, background = _status_style(row_status)
                table_rows.append(
                    '<tr>'
                    f'<td style="padding:11px 12px;border-bottom:1px solid #eaecf0;color:#101828;font-size:13px">{escape(name)}</td>'
                    f'<td style="padding:11px 12px;border-bottom:1px solid #eaecf0;font-size:12px"><span style="display:inline-block;padding:3px 8px;border-radius:12px;color:{color};background:{background};font-weight:600">{escape(label)}</span></td>'
                    f'<td style="padding:11px 12px;border-bottom:1px solid #eaecf0;color:#475467;font-size:13px;word-break:break-word">{escape(row_detail) if row_detail else "—"}</td>'
                    '</tr>'
                )
            detail_html = (
                '<h2 style="font-size:16px;color:#101828;margin:24px 0 10px">Report details</h2>'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #eaecf0;border-radius:8px;border-collapse:separate;border-spacing:0">'
                '<tr style="background:#f9fafb"><th align="left" style="padding:9px 12px;color:#475467;font-size:12px">Item</th><th align="left" style="padding:9px 12px;color:#475467;font-size:12px">Status</th><th align="left" style="padding:9px 12px;color:#475467;font-size:12px">Details</th></tr>'
                + "".join(table_rows) + '</table>'
            )
        error_html = ""
        if error_detail:
            error_html = f'<div style="margin:18px 0;padding:12px 14px;border-left:4px solid #f04438;background:#fef3f2;color:#912018;font-size:13px"><strong>Details:</strong> {escape(error_detail)}</div>'

        client_items = []
        for label, value in (("Client", client_name), ("Branch", client_branch), ("Server", client_server)):
            if value:
                client_items.append(
                    f'<span style="white-space:nowrap"><span style="opacity:.72">{label}:</span> <strong>{escape(value)}</strong></span>'
                )
        client_comment_html = (
            f'<div style="margin-top:9px;padding-top:9px;border-top:1px solid rgba(255,255,255,.22);font-size:13px"><span style="opacity:.72">Comment:</span> {escape(client_comment)}</div>'
            if client_comment else ""
        )
        client_header_html = (
            f'<div style="margin-top:13px;font-size:13px;line-height:1.65">{" &nbsp;&nbsp;•&nbsp;&nbsp; ".join(client_items)}{client_comment_html}</div>'
            if client_items or client_comment else ""
        )

        html = f'''<!doctype html><html><body style="margin:0;background:#f2f4f7;font-family:Segoe UI,Arial,sans-serif;color:#101828">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f7;padding:28px 12px"><tr><td align="center">
<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(16,24,40,.08)">
<tr><td style="padding:24px 28px;background:{BRAND_COLOR};color:#fff"><div style="font-size:22px;font-weight:700">{BRAND_NAME}</div><div style="font-size:13px;opacity:.85;margin-top:4px">Automated operations report</div>{client_header_html}</td></tr>
<tr><td style="padding:26px 28px"><span style="display:inline-block;padding:5px 10px;border-radius:14px;color:{status_color};background:{status_background};font-size:13px;font-weight:700">{escape(status_label)}</span>
<h1 style="font-size:21px;line-height:1.3;margin:12px 0 4px">{escape(job_type)} report</h1><div style="color:#667085;font-size:13px">Job ID: {escape(job_id)}</div>
{error_html}<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:18px;border:1px solid #eaecf0;border-radius:8px;border-collapse:separate;border-spacing:0">{metric_rows}</table>
{detail_html}</td></tr>
<tr><td style="padding:17px 28px;background:#f9fafb;color:#667085;font-size:12px;border-top:1px solid #eaecf0">Generated automatically by {BRAND_NAME}. Please do not reply to this message.</td></tr>
</table></td></tr></table></body></html>'''
        message.add_alternative(html, subtype="html")
        return message

    def send_message(self, message: EmailMessage) -> None:
        # Test configurations intentionally resemble production so recovery and
        # scheduling can be exercised.  They must never contact the real SMTP
        # server, even when a test forgets to mock this method.
        if _external_delivery_disabled():
            return
        recipients = list(self.settings.get("to", []))
        cc = list(self.settings.get("cc", []))
        if not recipients:
            raise ValueError("Email notifications require at least one To address")
        host = str(self.settings.get("smtp_server", "")).strip()
        port = int(self.settings.get("smtp_port", 587))
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if self.settings.get("use_tls", True):
                smtp.starttls()
                smtp.ehlo()
            smtp.login(self.settings.get("username", ""), self.settings.get("password", ""))
            smtp.send_message(message, to_addrs=recipients + cc)

    def send(self, subject: str, body: str) -> None:
        """Send a simple message for callers that use the original notifier API."""
        if not self.settings.get("enabled", False):
            return
        sender_address = _safe(self.settings.get("username"), "")
        sender_name = _safe(self.settings.get("sender_name"), BRAND_NAME).replace("\r", " ").replace("\n", " ")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((sender_name, sender_address))
        message["To"] = ", ".join(self.settings.get("to", []))
        cc = list(self.settings.get("cc", []))
        if cc:
            message["Cc"] = ", ".join(cc)
        message.set_content(body)
        self.send_message(message)

    def notify_result(self, job: dict, status: str, detail: str = "", result: dict[str, Any] | None = None) -> None:
        normalized_status = status.strip().casefold().replace("_", " ")
        if normalized_status == "completed":
            enabled_key = "notify_warning" if _has_warnings(result or {}) else "notify_success"
        elif normalized_status == "interrupted and recovered":
            enabled_key = "notify_recovery"
        else:
            enabled_key = "notify_failure"
        if not self.settings.get("enabled", False) or not self.settings.get(enabled_key, True):
            return
        self.send_message(self.build_message(job, status, detail, result))
