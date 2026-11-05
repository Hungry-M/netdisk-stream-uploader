# tests/test_uploader.py
"""分卷上传：跳过、重试、失败隔离。用真实的 LocalDirProvider，不是 mock。"""
from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.storage.base import ProviderError
from src.core.uploader import VolumeUploader, UploadResult
from src.database.task_db import TaskDB
from tests.conftest import RecordingProvider


def _make_uploader(tmp_path, db, task_id, provider, max_retries=3):
    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path)],
        max_upload_retries=max_retries,
        retry_backoff_seconds=0,      # 测试里不退避，避免拖慢用例
    )
    rm = ResourceManager(settings)
    return VolumeUploader(provider, rm, db=db, task_id=task_id)


def _fake_vol(tmp_path, name="fake.zip", content=b"PK\x03\x04fake"):
    f = tmp_path / name
    f.write_bytes(content)
    return open(f, "rb")


def test_skips_completed_volume(tmp_path):
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t1", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t1", 1, "/apps/arch.part0001.zip", "/tmp/1")
    db.update_volume_status("t1", 1, "done")

    provider = RecordingProvider(tmp_path / "cloud")
    uploader = _make_uploader(tmp_path, db, "t1", provider)

    vol = _fake_vol(tmp_path, "v1.zip")
    pn, ok, err, skipped = uploader.upload_volume(vol, "/apps", "arch", 1)

    assert ok is True
    assert pn == 1
    assert err is None
    assert skipped is True          # 命中的是"已完成、跳过"分支
    assert provider.calls == []     # 一次都没上传


def test_partial_failure_does_not_cancel(tmp_path):
    """一个分卷重试后仍失败，其他成功 → PARTIAL（不取消其余分卷）"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t2", "/src", ["a.txt"], "/apps", "arch", 1024)

    provider = RecordingProvider(tmp_path / "cloud")
    provider.fail("part0002", ProviderError("模拟网络错误", retryable=True), times=-1)

    uploader = _make_uploader(tmp_path, db, "t2", provider)

    vols = [_fake_vol(tmp_path, f"v{i}.zip", b"PK\x03\x04" + bytes([i])) for i in range(1, 4)]

    def vol_gen():
        for v in vols:
            yield v

    result, uploaded, failed, skipped = uploader.upload_all(vol_gen(), "/apps", "arch", "t2")

    assert result == UploadResult.PARTIAL
    assert len(uploaded) == 2
    assert len(failed) == 1
    assert skipped == []
    # 卷 1、卷 3 各 1 次，卷 2 尝试 3 次（1 次 + 2 次重试）
    assert len(provider.calls) == 5


def test_retry_recovers_from_transient_error(tmp_path):
    """第一次失败、第二次成功：最终算成功"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t3", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t3", 1, "/apps/arch.part0001.zip", "/tmp/1")

    provider = RecordingProvider(tmp_path / "cloud")
    provider.fail("part0001", ProviderError("连接被重置", retryable=True), times=1)

    uploader = _make_uploader(tmp_path, db, "t3", provider)
    pn, ok, err, skipped = uploader.upload_volume(
        _fake_vol(tmp_path, "v1.zip"), "/apps", "arch", 1
    )

    assert ok is True and err is None and skipped is False
    assert len(provider.calls) == 2                 # 首次 + 一次重试
    assert db.get_volume_status("t3", 1) == "done"


def test_retry_gives_up_after_max_attempts(tmp_path):
    """重试次数用尽后标记 failed"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t4", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t4", 1, "/apps/arch.part0001.zip", "/tmp/1")

    provider = RecordingProvider(tmp_path / "cloud")
    provider.fail("part0001", ProviderError("持续失败", retryable=True), times=-1)

    uploader = _make_uploader(tmp_path, db, "t4", provider, max_retries=2)
    pn, ok, err, skipped = uploader.upload_volume(
        _fake_vol(tmp_path, "v1.zip"), "/apps", "arch", 1
    )

    assert ok is False
    assert "持续失败" in err
    assert len(provider.calls) == 2                 # max_retries=2
    assert db.get_volume_status("t4", 1) == "failed"


def test_non_retryable_error_stops_immediately(tmp_path):
    """403 这类错误只尝试一次 —— 重试三次只是白等"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t5", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t5", 1, "/apps/arch.part0001.zip", "/tmp/1")

    provider = RecordingProvider(tmp_path / "cloud")
    provider.fail("part0001", ProviderError("无权限", retryable=False), times=-1)

    uploader = _make_uploader(tmp_path, db, "t5", provider, max_retries=3)
    pn, ok, err, skipped = uploader.upload_volume(
        _fake_vol(tmp_path, "v1.zip"), "/apps", "arch", 1
    )

    assert ok is False
    assert len(provider.calls) == 1
    assert db.get_volume_status("t5", 1) == "failed"


def test_uploaded_bytes_land_on_disk(tmp_path):
    """真实写入：文件内容必须和分卷一致（mock 测不出这个）"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t6", "/src", ["a.txt"], "/apps", "arch", 1024)

    provider = RecordingProvider(tmp_path / "cloud")
    uploader = _make_uploader(tmp_path, db, "t6", provider)

    payload = b"PK\x03\x04real-bytes" * 100
    uploader.upload_volume(_fake_vol(tmp_path, "v1.zip", payload), "/apps", "arch", 1)

    written = (tmp_path / "cloud" / "apps" / "arch.part0001.zip").read_bytes()
    assert written == payload
