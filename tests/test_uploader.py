# tests/test_uploader.py
from unittest.mock import MagicMock
from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.uploader import VolumeUploader, UploadResult
from src.database.task_db import TaskDB


def _make_uploader(tmp_path, db, task_id, fake_client):
    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        preferred_temp_dirs=[str(tmp_path)],
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
    pn, ok, err = uploader.upload_volume(vol, "/apps", "arch", 1)

    assert ok is True
    assert pn == 1
    fake_client.upload_file.assert_not_called()


def test_partial_failure_does_not_cancel(tmp_path):
    """一个分卷失败，其他成功 → PARTIAL"""
    db = TaskDB(db_path=str(tmp_path / "t.db"))
    db.create_task("t2", "/src", ["a.txt"], "/apps", "arch", 1024)

    fake_client = MagicMock()
    call_count = {"n": 0}

    def side_effect(remote_path, local_file):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise Exception("模拟网络错误")

    fake_client.upload_file.side_effect = side_effect

    uploader = _make_uploader(tmp_path, db, "t2", fake_client)

    # 三个分卷
    vols = [_fake_vol(tmp_path, f"v{i}.zip", b"PK\x03\x04" + bytes([i])) for i in range(1, 4)]

    def vol_gen():
        for v in vols:
            yield v

    result, uploaded, failed = uploader.upload_all(vol_gen(), "/apps", "arch", "t2")

    assert result == UploadResult.PARTIAL
    assert len(uploaded) == 2
    assert len(failed) == 1
    assert call_count["n"] == 3  # 三次都被调用（没被取消）