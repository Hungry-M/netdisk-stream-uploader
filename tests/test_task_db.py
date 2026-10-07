# tests/test_task_db.py
import pytest
from src.database.task_db import TaskStatus


def test_create_and_get_task(temp_db):
    temp_db.create_task(
        task_id="t1", source_dir="/src", file_list=["a.txt", "b.txt"],
        remote_dir="/apps/backup", base_name="archive", volume_size_bytes=1024,
    )
    task = temp_db.get_task("t1")
    assert task is not None
    assert task["file_list"] == ["a.txt", "b.txt"]
    assert task["status"] == TaskStatus.PENDING.value
    assert task["fixed_modified_at"] is not None


def test_volume_lifecycle_and_resume(temp_db):
    temp_db.create_task("t2", "/src", ["a.txt"], "/apps", "arch", 1024)
    for i in range(1, 4):
        temp_db.add_volume("t2", i, f"/apps/arch.part{i:04d}.zip", f"/tmp/{i}")

    temp_db.update_volume_status("t2", 1, "done")
    temp_db.update_volume_status("t2", 2, "done")
    temp_db.update_volume_status("t2", 3, "failed")

    assert temp_db.get_completed_part_numbers("t2") == [1, 2]
    assert temp_db.get_failed_part_numbers("t2") == [3]
    assert temp_db.get_volume_status("t2", 3) == "failed"


def test_delete_task_cascades_volumes(temp_db):
    """验证外键级联删除生效"""
    temp_db.create_task("t3", "/src", ["a.txt"], "/apps", "arch", 1024)
    temp_db.add_volume("t3", 1, "/apps/arch.part0001.zip")
    temp_db.add_volume("t3", 2, "/apps/arch.part0002.zip")
    assert len(temp_db.get_all_volumes("t3")) == 2

    temp_db.delete_task("t3")
    assert temp_db.get_task("t3") is None
    assert temp_db.get_all_volumes("t3") == []