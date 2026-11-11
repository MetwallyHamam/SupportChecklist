from __future__ import annotations

import os
import ntpath
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Any


BRAND_NAME = "Support Checklist"
COMPANY_NAME = "MILLENSYS"
PRODUCT_NAME = "MiChecklist"
BRAND_COLOR = "#102A43"
BRAND_ACCENT = "#00A6D6"
DEFAULT_SUBJECT_TEMPLATE = "[{company} {product}] {status} - {job_type}"


class _SubjectValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_subject(template: Any, values: dict[str, str]) -> str:
    candidate = str(template or "").strip() or DEFAULT_SUBJECT_TEMPLATE
    try:
        subject = candidate.format_map(_SubjectValues(values))
    except (ValueError, AttributeError):
        subject = DEFAULT_SUBJECT_TEMPLATE.format_map(_SubjectValues(values))
    # Subject headers cannot contain newlines. Keep them compact for mobile clients.
    return " ".join(subject.replace("\r", " ").replace("\n", " ").split())[:240]


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
    if normalized in {"pending", "queued", "waiting external", "waiting_external"}:
        return "Pending", "#175cd3", "#eff8ff"
    if "cancel" in normalized:
        return normalized.title(), "#b54708", "#fffaeb"
    return normalized.title(), "#b42318", "#fef3f2"


def _detail_rows(result: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
    """Return item, status, file, compression, and remaining detail columns."""
    rows: list[tuple[str, str, str, str, str]] = []
    for task in result.get("tasks", []) or []:
        if isinstance(task, dict) and str(task.get("status", "")).casefold() != "running":
            rows.append((
                _safe(task.get("task"), "Task"),
                _safe(task.get("status")),
                "",
                "",
                _safe(task.get("detail"), ""),
            ))
    for key, label in (
        ("cleanup_detail", "Cleanup"),
        ("databases", "Database"),
        ("services_detail", "Service"),
        ("iis_apps_detail", "IIS Child Application"),
    ):
        for item in result.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("database") or item.get("display_name") or label
            file_path = ""
            compression = ""
            if key == "databases":
                file_path = _safe(item.get("file"), "")
                compression = _safe(item.get("compression"), "")
                detail = item.get("detail") or ""
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
                if item.get("detail"):
                    parts.append(str(item["detail"]))
                detail = " | ".join(parts)
            elif key == "iis_apps_detail":
                parts = []
                if item.get("physical_path"):
                    parts.append(f"Physical path: {item['physical_path']}")
                if item.get("archive"):
                    parts.append(f"Archive: {item['archive']}")
                if item.get("detail"):
                    parts.append(str(item["detail"]))
                detail = " | ".join(parts)
            else:
                detail = item.get("detail") or item.get("message") or item.get("path") or ""
            rows.append((
                _safe(name, label),
                _safe(item.get("status"), "Completed"),
                file_path,
                compression,
                _safe(detail, ""),
            ))
    return rows


def _has_warnings(result: dict[str, Any]) -> bool:
    return any(
        str(item.get("status", "")).casefold() in {"partial", "warning", "completed with warnings"}
        for key in ("tasks", "cleanup_detail", "iis_apps_detail")
        for item in (result.get(key, []) or [])
        if isinstance(item, dict)
    )


def _email_phase_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only useful phase summaries; detailed operations have their own tables."""
    preferred = {"Cleanup", "IIS Child Applications", "Services", "Database", "Folders"}
    rows: list[dict[str, Any]] = []
    has_iis_application_summary = any(
        isinstance(task, dict) and task.get("task") == "IIS Child Applications"
        for task in result.get("tasks", []) or []
    )
    for task in result.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        name = str(task.get("task", ""))
        status = str(task.get("status", ""))
        if name == "IIS" and has_iis_application_summary:
            continue
        normalized_status = status.casefold()
        if normalized_status == "running":
            continue
        # Always retain failures and warnings, even for a normally hidden
        # diagnostic sub-step, so compacting the email never hides a problem.
        important_status = normalized_status not in {"completed", "skipped", "success", "successful"}
        if name not in preferred and not important_status:
            continue
        rows.append(task)
    return rows


def _compact_phase_detail(value: Any, status: Any = "") -> str:
    """Keep successful summaries concise without hiding warning/failure reasons."""
    text = str(value or "").strip()
    normalized = str(status or "").casefold()
    if any(token in normalized for token in ("fail", "partial", "warning")):
        return text
    return text.split(" | Failed:", 1)[0]


def _failure_reason(item: dict[str, Any]) -> str:
    """Return the most useful available diagnostic for a failed detail row."""
    for key in ("detail", "error", "message", "reason", "last_error"):
        value = str(item.get(key, "") or "").strip()
        if value:
            return value
    if "fail" in str(item.get("status", "")).casefold():
        return "No failure reason was recorded. Review the MiChecklist service log."
    return ""


def _short_file(path: Any) -> str:
    """Use a compact filename in email because the backup root is already shown above."""
    value = str(path or "").strip()
    return ntpath.basename(value) or value


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
        message["Subject"] = _format_subject(
            self.settings.get("subject_template"),
            {
                "company": COMPANY_NAME,
                "product": PRODUCT_NAME,
                "status": status_label,
                "job_type": job_type,
                "client": client_name,
                "branch": client_branch,
                "server": client_server or server,
                "job_id": job_id,
                "date": execution_date,
            },
        )
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
            for name, row_status, file_path, compression, row_detail in rows:
                parts = []
                if file_path:
                    parts.append(f"File: {file_path}")
                if compression:
                    parts.append(f"Compression: {compression}")
                if row_detail:
                    parts.append(row_detail)
                plain_lines.append(
                    f"- {name}: {row_status}" + (f" — {' | '.join(parts)}" if parts else "")
                )
        message.set_content("\n".join(plain_lines))

        metrics = [("Job type", job_type), ("Server", server), ("Executed", execution_date), ("Duration", duration), ("Backup size", backup_size), ("Backup path", backup_path)]
        cells = []
        for label, value in metrics:
            cells.append(
                '<td class="metric-cell" style="width:50%;padding:3px;vertical-align:top">'
                '<div style="padding:7px 9px;background:#f7fafd;border:1px solid #d9e2ec;border-radius:7px">'
                f'<div style="color:#829ab1;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">{escape(label)}</div>'
                f'<div style="color:#243b53;font-size:12px;font-weight:600;margin-top:4px;line-height:1.4;word-break:break-word">{escape(value)}</div>'
                '</div></td>'
            )
        metric_rows = "".join(f"<tr>{''.join(cells[i:i + 2])}</tr>" for i in range(0, len(cells), 2))

        def render_section(title: str, headers: list[str], section_rows: list[list[str]]) -> str:
            if not section_rows:
                return ""
            rendered_rows = []
            for values in section_rows:
                badge, color, background = _status_style(values[1])
                properties = []
                for header, value in zip(headers[2:], values[2:]):
                    properties.append(
                        '<span class="property" style="display:inline-block;margin:1px 14px 1px 0;line-height:1.35;max-width:100%">'
                        f'<span style="color:#829ab1;font-size:9px;font-weight:700;text-transform:uppercase;'
                        f'letter-spacing:.04em">{escape(header)}:</span> '
                        f'<span style="color:#334e68;font-size:11px;word-break:break-word;overflow-wrap:anywhere">'
                        f'{escape(value) if value else "&mdash;"}</span></span>'
                    )
                rendered_rows.append(
                    '<tr class="section-row">'
                    f'<td class="section-name" style="width:25%;padding:8px 10px;border-bottom:1px solid #edf2f7;'
                    f'color:#102a43;font-size:11px;font-weight:700;vertical-align:top">{escape(values[0])}</td>'
                    '<td class="section-status" style="width:120px;padding:8px 6px;border-bottom:1px solid #edf2f7;vertical-align:top">'
                    f'<span style="display:inline-block;padding:4px 9px;border-radius:14px;color:{color};'
                    f'background:{background};font-size:10px;font-weight:700;white-space:nowrap">{escape(badge)}</span>'
                    '</td>'
                    '<td class="section-detail" style="padding:7px 10px;border-bottom:1px solid #edf2f7;vertical-align:top;word-break:break-word;overflow-wrap:anywhere">'
                    + "".join(properties) + '</td></tr>'
                )
            return (
                '<table class="section-table" role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                'style="margin-top:14px;border:1px solid #d9e2ec;border-radius:9px;'
                'border-collapse:separate;border-spacing:0;background:#ffffff;table-layout:fixed">'
                f'<tr><td colspan="3" style="padding:7px 10px;background:#f2f7fb;border-left:4px solid {BRAND_ACCENT};'
                f'color:#102a43;font-size:12px;font-weight:800">{escape(title)}</td></tr>'
                + "".join(rendered_rows) + '</table>'
            )

        phase_rows = [
            [
                "IIS applications" if task.get("task") == "IIS Child Applications" else _safe(task.get("task")),
                _safe(task.get("status")),
                _compact_phase_detail(task.get("detail"), task.get("status")),
            ]
            for task in _email_phase_rows(result)
        ]
        database_rows = [
            [
                _safe(item.get("database"), "Database"),
                _safe(item.get("status"), "Completed"),
                _short_file(item.get("file")),
                _safe(item.get("compression"), ""),
                _failure_reason(item),
            ]
            for item in result.get("databases", []) or [] if isinstance(item, dict)
        ]
        service_rows = [
            [
                _safe(item.get("name") or item.get("display_name"), "Service"),
                _safe(item.get("status"), "Completed"),
                _safe(item.get("runtime_status"), ""),
                _short_file(item.get("path")),
                _short_file(item.get("archive")),
                _safe(item.get("detail"), ""),
            ]
            for item in result.get("services_detail", []) or [] if isinstance(item, dict)
        ]
        iis_rows = [
            [
                _safe(item.get("name"), "IIS application"),
                _safe(item.get("status"), "Completed"),
                _safe(item.get("physical_path"), ""),
                _short_file(item.get("archive")),
                _failure_reason(item),
            ]
            for item in result.get("iis_apps_detail", []) or [] if isinstance(item, dict)
        ]
        cleanup_rows = [
            [
                _safe(item.get("name"), "Cleanup"),
                _safe(item.get("status"), "Completed"),
                _safe(item.get("path"), ""),
                _safe(item.get("detail"), ""),
            ]
            for item in result.get("cleanup_detail", []) or [] if isinstance(item, dict)
        ]
        detail_html = "".join([
            render_section("Summary", ["Operation", "Status", "Result"], phase_rows),
            render_section("Databases", ["Database", "Status", "Archive", "Compression", "Details"], database_rows),
            render_section("Services", ["Service", "Status", "Runtime", "Executable", "Archive", "Details"], service_rows),
            render_section("IIS applications", ["Application", "Status", "Source", "Archive", "Reason"], iis_rows),
            render_section("Cleanup", ["Task", "Status", "Source", "Result"], cleanup_rows),
        ])
        error_html = ""
        if error_detail:
            error_html = f'<div style="margin:18px 0;padding:12px 14px;border-left:4px solid #f04438;background:#fef3f2;color:#912018;font-size:13px"><strong>Details:</strong> {escape(error_detail)}</div>'

        client_items = []
        for label, value in (("Client", client_name), ("Branch", client_branch), ("Server", client_server)):
            if value:
                client_items.append(
                    '<span style="display:inline-block;margin:4px 6px 0 0;padding:5px 9px;'
                    'border:1px solid rgba(255,255,255,.22);border-radius:14px;white-space:nowrap;'
                    f'font-size:11px"><span style="opacity:.7">{label}</span>&nbsp; <strong>{escape(value)}</strong></span>'
                )
        client_comment_html = (
            f'<div style="margin-top:9px;padding-top:9px;border-top:1px solid rgba(255,255,255,.22);font-size:13px"><span style="opacity:.72">Comment:</span> {escape(client_comment)}</div>'
            if client_comment else ""
        )
        client_header_html = (
            f'<div style="margin-top:10px;line-height:1.5">{"".join(client_items)}{client_comment_html}</div>'
            if client_items or client_comment else ""
        )

        html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
@media only screen and (max-width:600px) {{
  .shell-pad {{ padding:0!important; }} .container {{ border-radius:0!important; }}
  .content-pad {{ padding:10px 8px!important; }} .header-pad {{ padding:12px 10px!important; }}
  .header-report {{ display:none!important; }} .metric-cell {{ display:block!important;width:auto!important; }}
  .section-table {{ table-layout:auto!important; }} .section-row,.section-name,.section-status,.section-detail {{ display:block!important;width:auto!important;box-sizing:border-box!important; }}
  .section-name {{ padding:8px 9px 2px!important;border-bottom:0!important; }}
  .section-status {{ padding:3px 9px!important;border-bottom:0!important; }}
  .section-detail {{ padding:4px 9px 9px!important; }} .property {{ display:block!important;margin:2px 0!important; }}
  .footer-cell {{ display:block!important;text-align:left!important;padding-top:3px!important; }}
}}
</style></head><body style="margin:0;background:#eaf0f6;font-family:Segoe UI,Arial,sans-serif;color:#102a43">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eaf0f6"><tr><td class="shell-pad" align="center" style="padding:12px 8px">
<table class="container" role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 8px 28px rgba(16,42,67,.12)">
<tr><td style="height:5px;background:{BRAND_ACCENT};font-size:1px;line-height:1px">&nbsp;</td></tr>
<tr><td class="header-pad" style="padding:15px 18px;background:{BRAND_COLOR};color:#fff">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td style="width:50px;vertical-align:top"><div style="width:42px;height:42px;line-height:42px;text-align:center;border-radius:11px;background:{BRAND_ACCENT};color:#fff;font-size:22px;font-weight:800">M</div></td>
<td style="vertical-align:middle"><div style="font-size:19px;font-weight:800;letter-spacing:.08em">{COMPANY_NAME}</div><div style="font-size:11px;opacity:.72;margin-top:2px;letter-spacing:.04em">{PRODUCT_NAME} &nbsp;&bull;&nbsp; {BRAND_NAME}</div></td>
<td class="header-report" align="right" style="vertical-align:middle;color:#b9d7ea;font-size:11px">AUTOMATED OPERATIONS REPORT</td>
</tr></table>{client_header_html}</td></tr>
<tr><td class="content-pad" style="padding:14px 16px;background:#fbfdff">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #d9e2ec;border-radius:12px;background:#ffffff"><tr>
<td style="padding:11px 12px"><span style="display:inline-block;padding:4px 8px;border-radius:14px;color:{status_color};background:{status_background};font-size:10px;font-weight:700">{escape(status_label)}</span>
<h1 style="font-size:21px;line-height:1.3;margin:9px 0 3px;color:#102a43">{escape(job_type)} report</h1><div style="color:#829ab1;font-size:11px">Job ID &nbsp;{escape(job_id)}</div></td>
</tr></table>
{error_html}<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:8px;border-collapse:separate;border-spacing:0">{metric_rows}</table>
{detail_html}</td></tr>
<tr><td style="padding:11px 16px;background:{BRAND_COLOR};color:#b9d7ea;font-size:9px;border-top:3px solid {BRAND_ACCENT}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td class="footer-cell" style="color:#ffffff;font-weight:700;letter-spacing:.06em">{COMPANY_NAME} &nbsp;&bull;&nbsp; {PRODUCT_NAME}</td><td class="footer-cell" align="right">Generated automatically &nbsp;&bull;&nbsp; Please do not reply</td></tr></table>
</td></tr>
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
