import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class CredentialStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(value: str) -> str:
    if os.name != "nt":
        raise CredentialStoreError("Secure credential storage requires Windows DPAPI")
    source, source_buffer = _blob(value.encode("utf-8"))
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "CopyMoveTool", None, None, None, 0, ctypes.byref(output)
    ):
        raise CredentialStoreError(f"DPAPI encryption failed: {ctypes.get_last_error()}")
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect(value: str) -> str:
    if os.name != "nt":
        raise CredentialStoreError("Secure credential storage requires Windows DPAPI")
    source, source_buffer = _blob(base64.b64decode(value))
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise CredentialStoreError(f"DPAPI decryption failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


class CredentialStore:
    def __init__(self, path="config/credentials.json"):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError(f"Cannot read credential store: {exc}") from exc

    def set(self, name: str, username: str, password: str):
        data = self._load()
        data[name] = {"username": username, "password": _protect(password)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def get(self, name: str):
        entry = self._load().get(name)
        if not entry:
            return None
        return entry["username"], _unprotect(entry["password"])

    def delete(self, name: str):
        data = self._load()
        removed = data.pop(name, None) is not None
        if removed:
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return removed

    def names(self):
        return sorted(self._load())
