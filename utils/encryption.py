import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Optional

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None


class EncryptionError(Exception):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi_encrypt(plaintext: str) -> str:
    data_in = plaintext.encode("utf-16le")
    buffer_in = ctypes.create_string_buffer(data_in)
    blob_in = DATA_BLOB(len(data_in), ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()

    if ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x4,  # CRYPTPROTECT_LOCAL_MACHINE: GUI and LocalSystem service can decrypt.
        ctypes.byref(blob_out),
    ) == 0:
        raise EncryptionError("DPAPI encryption failed")

    encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(encrypted).decode("utf-8")


def _dpapi_decrypt(ciphertext: str) -> str:
    data_in = base64.b64decode(ciphertext)
    buffer_in = ctypes.create_string_buffer(data_in)
    blob_in = DATA_BLOB(len(data_in), ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()

    if ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ) == 0:
        raise EncryptionError("DPAPI decryption failed")

    decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return decrypted.decode("utf-16le")


def _fernet_encrypt(plaintext: str, key: Optional[bytes] = None) -> str:
    if Fernet is None:
        raise EncryptionError("cryptography is not installed")
    if key is None:
        key = Fernet.generate_key()
    token = Fernet(key).encrypt(plaintext.encode("utf-8"))
    return base64.b64encode(key + b"::" + token).decode("utf-8")


def _fernet_decrypt(ciphertext: str) -> str:
    if Fernet is None:
        raise EncryptionError("cryptography is not installed")
    data = base64.b64decode(ciphertext)
    key, token = data.split(b"::", 1)
    return Fernet(key).decrypt(token).decode("utf-8")


def encrypt_password(value: str) -> str:
    if sys.platform.startswith("win"):
        return _dpapi_encrypt(value)
    return _fernet_encrypt(value)


def decrypt_password(value: str) -> str:
    if sys.platform.startswith("win"):
        try:
            return _dpapi_decrypt(value)
        except EncryptionError:
            return _fernet_decrypt(value)
    return _fernet_decrypt(value)
