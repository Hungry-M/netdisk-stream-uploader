# tests/test_uploader.py
from unittest.mock import MagicMock
from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.uploader import VolumeUploader, UploadResult
from src.database.task_db import TaskDB


def _make_uploader(tmp_path, db, task_id, fake_client, max_retries=3):
    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path)],
        max_upload_retries=max_retries,
        retry_backoff_seconds=0,      # 测试里不退避，避免拖慢用例
    )
    rm = ResourceManager(settings)
    return VolumeUploader(fake_client, rm, db=db, task_id=task_id)


def _fake_vol(tmp_path, name="fake.zip", content=b"PK\x03\x04fake"):
    f = tmp_path / name
    f.write_bytes(content)
    return open(f, "rb")


def test_skips_completed_volume(tmp_path):
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t1", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t1", 1, "/apps/arch.part0001.zip", "/tmp/1")
    db.update_volume_status("t1", 1, "done")

    fake_client = MagicMock()
    uploader = _make_uploader(tmp_path, db, "t1", fake_client)

    vol = _fake_vol(tmp_path, "v1.zip")
    pn, ok, err, skipped = uploader.upload_volume(vol, "/apps", "arch", 1)

    assert ok is True
    assert pn == 1
    assert err is None
    assert skipped is True          # 命中的是"已完成、跳过"分支
    fake_client.upload_file.assert_not_called()


def test_partial_failure_does_not_cancel(tmp_path):
    """一个分卷重试后仍失败，其他成功 → PARTIAL（不取消其余分卷）"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t2", "/src", ["a.txt"], "/apps", "arch", 1024)

    fake_client = MagicMock()
    call_count = {"n": 0}

    def side_effect(remote_path, local_file):
        call_count["n"] += 1
        # 只有第 2 卷稳定失败（每次重试都失败）
        if remote_path.endswith("part0002.zip"):
            raise Exception("模拟网络错误")

    fake_client.upload_file.side_effect = side_effect

    uploader = _make_uploader(tmp_path, db, "t2", fake_client)

    # 三个分卷
    vols = [_fake_vol(tmp_path, f"v{i}.zip", b"PK\x03\x04" + bytes([i])) for i in range(1, 4)]

    def vol_gen():
        for v in vols:
            yield v

    result, uploaded, failed, skipped = uploader.upload_all(vol_gen(), "/apps", "arch", "t2")

    assert result == UploadResult.PARTIAL
    assert len(uploaded) == 2
    assert len(failed) == 1
    assert skipped == []           # 本次无分卷命中"已完成"分支
    # 卷 1、卷 3 各 1 次，卷 2 尝试 3 次（1 次 + 2 次重试）
    assert call_count["n"] == 5


def test_retry_recovers_from_transient_error(tmp_path):
    """第一次失败、第二次成功：最终算成功，且只上传一份"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t3", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t3", 1, "/apps/arch.part0001.zip", "/tmp/1")

    fake_client = MagicMock()
    attempts = {"n": 0}

    def side_effect(remote_path, local_file):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Exception("连接被重置")

    fake_client.upload_file.side_effect = side_effect
    uploader = _make_uploader(tmp_path, db, "t3", fake_client)

    pn, ok, err, skipped = uploader.upload_volume(
        _fake_vol(tmp_path, "v1.zip"), "/apps", "arch", 1
    )

    assert ok is True and err is None and skipped is False
    assert attempts["n"] == 2                 # 首次 + 一次重试
    assert db.get_volume_status("t3", 1) == "done"


def test_retry_gives_up_after_max_attempts(tmp_path):
    """重试次数用尽后标记 failed，不无限重试"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t4", "/src", ["a.txt"], "/apps", "arch", 1024)
    db.add_volume("t4", 1, "/apps/arch.part0001.zip", "/tmp/1")

    fake_client = MagicMock()
    fake_client.upload_file.side_effect = Exception("持续失败")
    uploader = _make_uploader(tmp_path, db, "t4", fake_client, max_retries=2)

    pn, ok, err, skipped = uploader.upload_volume(
        _fake_vol(tmp_path, "v1.zip"), "/apps", "arch", 1
    )

    assert ok is False
    assert "持续失败" in err
    assert fake_client.upload_file.call_count == 2   # max_retries=2
    assert db.get_volume_status("t4", 1) == "failed"
