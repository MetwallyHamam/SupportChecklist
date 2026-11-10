from __future__ import annotations

import hashlib
import time
from pathlib import Path


def compute_sha256(data: bytes) -> str:
    """Compute a SHA256 hash from raw bytes."""
    hash_obj = hashlib.sha256()
    hash_obj.update(data)
    return hash_obj.hexdigest()


def hash_file(path: Path, retries: int = 5, initial_delay: float = 0.1) -> str:
    """Compute a SHA256 hash for a file with retry logic for file lock handling.
    
    Args:
        path: Path to the file to hash
        retries: Number of retry attempts (default 5)
        initial_delay: Initial delay in seconds before first retry (default 0.1)
    
    Raises:
        FileNotFoundError: If file doesn't exist after all retries
        IOError: If file cannot be read after all retries
    """
    last_error = None
    delay = initial_delay
    
    for attempt in range(retries + 1):
        try:
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            
            hash_obj = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8192), b""):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()
        except (FileNotFoundError, PermissionError, IOError, OSError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise
    
    # Should not reach here, but raise last error if we do
    raise last_error
