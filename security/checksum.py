import hashlib
from pathlib import Path


def calculate_checksum(path, algorithm="sha256", chunk_size=1024 * 1024, control_cb=None):
    """Calculate a streaming checksum without loading the whole file into memory."""
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            if control_cb:
                control_cb()
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(source, destination, algorithm="sha256", control_cb=None):
    return calculate_checksum(source, algorithm, control_cb=control_cb) == calculate_checksum(
        destination, algorithm, control_cb=control_cb
    )
