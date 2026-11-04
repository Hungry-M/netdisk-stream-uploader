# tests/test_encryption.py
"""AES 加密：盐必须确定，否则续传校验失效。"""
import io
import os
import zipfile
from datetime import datetime

import pytest

from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import compress_and_split, password_tag


def _rm(tmp_path):
    return ResourceManager(ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path / "temp")],
        temp_file_prefix="test_",
    ))


def _compress(sample_files, tmp_path, password, task_id="t1"):
    source_dir, files = sample_files
    return list(compress_and_split(
        source_dir=source_dir,
        file_list=files,
        volume_size_bytes=60 * 1024,
        resource_manager=_rm(tmp_path),
        fixed_modified_at=datetime(2024, 1, 1, 12, 0, 0),
        password=password,
        task_id=task_id,
    ))


def _bytes(vols) -> bytes:
    buf = io.BytesIO()
    for v in vols:
        v.seek(0)
        buf.write(v.read())
    return buf.getvalue()


def _cleanup(vols):
    for v in vols:
        try:
            v.close()
        except Exception:
            pass
        try:
            os.unlink(v.name)
        except OSError:
            pass


def test_encryption_is_deterministic(sample_files, tmp_path):
    """同样的密码 + task_id，两次压缩必须字节一致 —— 续传的前提"""
    a = _compress(sample_files, tmp_path, "s3cret")
    b = _compress(sample_files, tmp_path, "s3cret")
    try:
        assert _bytes(a) == _bytes(b)
    finally:
        _cleanup(a)
        _cleanup(b)


def test_salt_depends_on_task_id(sample_files, tmp_path):
    a = _compress(sample_files, tmp_path, "s3cret", task_id="t1")
    b = _compress(sample_files, tmp_path, "s3cret", task_id="t2")
    try:
        assert _bytes(a) != _bytes(b)
    finally:
        _cleanup(a)
        _cleanup(b)


def test_different_password_changes_bytes(sample_files, tmp_path):
    a = _compress(sample_files, tmp_path, "s3cret")
    b = _compress(sample_files, tmp_path, "other")
    try:
        assert _bytes(a) != _bytes(b)
    finally:
        _cleanup(a)
        _cleanup(b)


def test_encrypted_entries_cannot_be_read_without_password(sample_files, tmp_path):
    vols = _compress(sample_files, tmp_path, "s3cret")
    try:
        zf = zipfile.ZipFile(io.BytesIO(_bytes(vols)))
        # 目录本身不加密，能列出条目名
        assert zf.namelist()
        # 但内容读不出来（AES 是 method 99，标准库不支持）
        with pytest.raises(Exception):
            zf.read(zf.namelist()[0])
    finally:
        _cleanup(vols)


def test_password_required_for_encryption(sample_files, tmp_path):
    """没有 task_id 就派生不出盐，应当直接报错而不是静默用随机盐"""
    source_dir, files = sample_files
    gen = compress_and_split(
        source_dir=source_dir,
        file_list=files,
        volume_size_bytes=60 * 1024,
        resource_manager=_rm(tmp_path),
        password="s3cret",
        task_id=None,
    )
    with pytest.raises(ValueError):
        list(gen)


def test_password_tag_is_stable_and_bound(sample_files):
    t1 = password_tag("task-a", "pw")
    assert t1 == password_tag("task-a", "pw")
    assert t1 != password_tag("task-a", "pw2")
    assert t1 != password_tag("task-b", "pw")
    assert len(t1) == 64
