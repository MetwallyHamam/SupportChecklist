from __future__ import annotations

import re


def parse_addresses(text: str) -> list[str]:
    return [item.strip() for item in text.replace(";", ",").split(",") if item.strip()]


def invalid_millensys_addresses(text: str) -> list[str]:
    return [
        address for address in parse_addresses(text)
        if not re.fullmatch(r"[A-Z0-9._%+-]+@millensys\.com", address, re.IGNORECASE)
    ]
