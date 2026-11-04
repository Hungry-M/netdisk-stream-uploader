# tests/test_manifest.py
"""归档自描述：manifest 必须是第一个条目、内容稳定、且不影响老任务。"""
import io
import json
import os
import zipfile
from datetime import datetime

from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import compress_and_split, MANIFEST_NAME


def _rm(tmp_path):
    return ResourceManager(ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path / "temp")],
        temp_file_prefix="test_",
    ))


def _compress(sample_files, tmp_path, manifest=None, volume_size=60 * 1024):
    source_dir, files = sample_files
    vols = list(compress_and_split(
        source_dir=source_dir,
        file_list=files,
        volume_size_bytes=volume_size,
        resource_manager=_rm(tmp_path),
        fixed_modified_at=datetime(2024, 1, 1, 12, 0, 0),   # 复现的前提
        manifest=manifest,
    ))
    return vols


def _joined_bytes(vols) -> bytes:
    """分卷是整条 ZIP 流的切片，拼起来才是一个完整 zip。"""
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


MANIFEST = {
    "format": 2,
    "tool": "netdisk-stream-uploader",
    "task_id": "abc123",
    "files": [["source/file_0.bin", 51200, 1600000000000000000]],
}


def test_manifest_is_the_first_entry(sample_files, tmp_path):
    vols = _compress(sample_files, tmp_path, manifest=MANIFEST)
    try:
        data = _joined_bytes(vols)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.namelist()[0] == MANIFEST_NAME
            assert json.loads(z.read(MANIFEST_NAME)) == MANIFEST
            # manifest + 3 个源文件
            assert len(z.namelist()) == 4
    finally:
        _cleanup(vols)


def test_manifest_is_byte_stable(sample_files, tmp_path):
    v1 = _compress(sample_files, tmp_path, manifest=MANIFEST)
    v2 = _compress(sample_files, tmp_path, manifest=MANIFEST)
    try:
        assert _joined_bytes(v1) == _joined_bytes(v2)
    finally:
        _cleanup(v1)
        _cleanup(v2)


def test_no_manifest_for_legacy_tasks(sample_files, tmp_path):
    """format 1 的任务不能凭空多出条目，否则历史分卷全部错位"""
    vols = _compress(sample_files, tmp_path, manifest=None)
    try:
        with zipfile.ZipFile(io.BytesIO(_joined_bytes(vols))) as z:
            assert MANIFEST_NAME not in z.namelist()
            assert len(z.namelist()) == 3
    finally:
        _cleanup(vols)


def test_manifest_reproducible_from_task_fields(tmp_path):
    """manifest 里只放任务参数，同样的参数必须生成同样的字节"""
    from src.cli import _build_manifest

    task = {
        "format_version": 2,
        "base_name": "arch",
        "remote_dir": "/apps/StreamPack",
        "volume_size_bytes": 1024,
        "created_at": 1793786843.5,
        "fixed_modified_at": 1793786800.0,
        "entries_json": json.dumps([["a.bin", 10, 123]]),
    }
    m1 = _build_manifest("t1", task)
    m2 = _build_manifest("t1", task)
    assert m1 == m2
    assert m1["files"] == [["a.bin", 10, 123]]
    assert m1["format"] == 2
