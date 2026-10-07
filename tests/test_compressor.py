# tests/test_compressor.py
import os
from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import compress_and_split


def test_compress_and_split_creates_multiple_volumes(sample_files, tmp_path):
    source_dir, files = sample_files

    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        disk_limit_bytes=100 * 1024 * 1024,
        preferred_temp_dirs=[str(tmp_path / "temp")],
        temp_file_prefix="test_",
    )
    rm = ResourceManager(settings)

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
        for v in volumes:
            try:
                v.close()
            except Exception:
                pass
            try:
                os.unlink(v.name)
            except OSError:
                pass


def test_compress_same_input_produces_identical_volumes(sample_files, tmp_path):
    """关键测试：固定时间戳 → 两次压缩产生字节一致的分卷"""
    import hashlib
    from datetime import datetime

    source_dir, files = sample_files
    fixed_dt = datetime(2024, 1, 1, 12, 0, 0)

    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path / "temp")],
    )
    rm = ResourceManager(settings)

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
            for v in vols:
                try:
                    v.close()
                except Exception:
                    pass
                try:
                    os.unlink(v.name)
                except OSError:
                    pass
        return hashes

    h1 = run_and_hash()
    h2 = run_and_hash()
    assert h1 == h2, "固定时间戳下两次压缩必须字节一致"