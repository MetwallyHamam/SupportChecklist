import os
import zipfile
from datetime import datetime

from utils.compression import archive_folder, compress_existing_zip, compress_folder


def test_compression_clamps_pre_1980_timestamp(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    old_file = source / "legacy.txt"
    old_file.write_text("legacy", encoding="utf-8")
    pre_1980 = datetime(1975, 1, 1).timestamp()
    os.utime(old_file, (pre_1980, pre_1980))
    archive_path = tmp_path / "backup.zip"

    compress_folder(source, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        member = archive.getinfo("source/legacy.txt")
        assert member.date_time[:3] == (1980, 1, 1)
        assert archive.read(member) == b"legacy"


def test_deferred_compression_preserves_archive_contents(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.txt").write_text("repeated-data-" * 10000, encoding="utf-8")
    archive_path = tmp_path / "backup.zip"

    archive_folder(source, archive_path)
    uncompressed_size = archive_path.stat().st_size
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.getinfo("source/large.txt").compress_type == zipfile.ZIP_STORED

    compress_existing_zip(archive_path)

    assert archive_path.stat().st_size < uncompressed_size
    with zipfile.ZipFile(archive_path) as archive:
        member = archive.getinfo("source/large.txt")
        assert member.compress_type == zipfile.ZIP_DEFLATED
        assert archive.read(member).startswith(b"repeated-data-")
