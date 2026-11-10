import logging
import re


class SecretRedactionFilter(logging.Filter):
    PATTERNS = (
        re.compile(r"(?i)(password|passwd|token|secret)\s*[=:]\s*([^\s,;]+)"),
        re.compile(r"(?i)(ftp|sftp)://([^:@/]+):([^@/]+)@"),
    )

    def filter(self, record):
        message = record.getMessage()
        message = self.PATTERNS[0].sub(r"\1=***", message)
        message = self.PATTERNS[1].sub(r"\1://\2:***@", message)
        record.msg = message
        record.args = ()
        return True
