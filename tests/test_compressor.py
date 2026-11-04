# tests/test_compressor.py
import os
import hashlib
from pathlib import Path
from datetime import datetime

from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import (
    compress_and_split, compute_fingerprint, iter_entries,
)


def _make_rm(tmp_path, **overrides):
    kwargs = dict(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path / "temp")],
        temp_file_prefix="test_",
    )
    kwargs.update(overrides)
    return ResourceManager(ResourceSettings(**kwargs))


def _cleanup(volumes):
    for v in volumes:
        try:
            v.close()
        except Exception:
            pass
        try:
            os.unlink(v.name)
        except OSError:
            pass


def test_compress_and_split_creates_multiple_volumes(sample_files, tmp_path):
    source_dir, files = sample_files
    rm = _make_rm(tmp_path, disk_limit_bytes=100 * 1024 * 1024)

    # 150KB 随机数据 + 60KB 分卷 → 至少 2 卷
    volumes = list(compress_and_split(
        source_dir=source_dir,
        file_list=files,
        volume_size_bytes=60 * 1024,
        resource_manager=rm,
    ))

    try:
        assert len(volumes) >= 2
        for v in volumes:
            assert os.path.exists(v.name)
            assert os.path.getsize(v.name) > 0
    finally:
        _cleanup(volumes)


def test_compress_same_input_produces_identical_volumes(sample_files, tmp_path):
    """关键测试：固定时间戳 → 两次压缩产生字节一致的分卷"""
    source_dir, files = sample_files
    fixed_dt = datetime(2024, 1, 1, 12, 0, 0)
    rm = _make_rm(tmp_path)

    def run_and_hash():
        hashes = []
        vols = list(compress_and_split(
            source_dir=source_dir,
            file_list=files,
            volume_size_bytes=60 * 1024,
            resource_manager=rm,
            fixed_modified_at=fixed_dt,
        ))
        try:
            for v in vols:
                v.seek(0)
                hashes.append(hashlib.md5(v.read()).hexdigest())
        finally:
            _cleanup(vols)
        return hashes

    h1 = run_and_hash()
    h2 = run_and_hash()
    assert h1 == h2, "固定时间戳下两次压缩必须字节一致"


def test_volume_metadata_matches_real_bytes(sample_files, tmp_path):
    """每卷回调的 md5 / size / start_offset 必须与实际写出的字节吻合"""
    source_dir, files = sample_files
    rm = _make_rm(tmp_path)
    infos = []

    vols = list(compress_and_split(
        source_dir=source_dir,
        file_list=files,
        volume_size_bytes=60 * 1024,
        resource_manager=rm,
        on_volume_complete=infos.append,
    ))

    try:
        assert len(infos) == len(vols)

        for info, vol in zip(infos, vols):
            vol.seek(0)
            data = vol.read()
            assert info.md5 == hashlib.md5(data).hexdigest()
            assert info.size == len(data)
            assert info.temp_path == vol.name

        # 偏移必须首尾相接，中间不能有缝
        assert infos[0].start_offset == 0
        for prev, cur in zip(infos, infos[1:]):
            assert cur.start_offset == prev.start_offset + prev.size

        # 卷号连续
        assert [i.part_number for i in infos] == list(range(1, len(infos) + 1))
    finally:
        _cleanup(vols)


def test_fingerprint_is_stable(sample_files):
    source_dir, files = sample_files
    assert compute_fingerprint(source_dir, files) == compute_fingerprint(source_dir, files)


def test_fingerprint_changes_on_edit(sample_files):
    source_dir, files = sample_files
    target = Path(files[0])

    before = compute_fingerprint(source_dir, files)

    # 大小变了
    target.write_bytes(os.urandom(4096))
    size_changed = compute_fingerprint(source_dir, files)
    assert size_changed != before

    # 大小相同、仅 mtime 变了
    os.utime(target, (1_600_000_000, 1_600_000_000))
    mtime_changed = compute_fingerprint(source_dir, files)
    assert mtime_changed != size_changed


def test_fingerprint_changes_on_new_file(sample_files):
    source_dir, files = sample_files
    before = compute_fingerprint(source_dir, [source_dir])

    Path(source_dir, "extra.bin").write_bytes(b"x" * 128)
    after = compute_fingerprint(source_dir, [source_dir])
    assert after != before


def test_iter_entries_order_is_deterministic(sample_files):
    """同一输入两次枚举，顺序与内容必须一致（续传校验依赖它）"""
    source_dir, files = sample_files
    first = [(e.arcname, e.size) for e in iter_entries(source_dir, files)]
    second = [(e.arcname, e.size) for e in iter_entries(source_dir, files)]
    assert first == second
    assert len(first) == len(files)
