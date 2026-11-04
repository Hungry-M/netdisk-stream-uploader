# tests/test_task_db.py
import pytest
from src.database.task_db import TaskStatus, TaskDB


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


def test_reset_stale_running_tasks(temp_db):
    """残留的 running 应被复位成 interrupted，其它状态不受影响"""
    temp_db.create_task("r1", "/src", ["a.txt"], "/apps", "arch", 1024)
    temp_db.create_task("r2", "/src", ["a.txt"], "/apps", "arch", 1024)
    temp_db.create_task("r3", "/src", ["a.txt"], "/apps", "arch", 1024)

    temp_db.update_task_status("r1", TaskStatus.RUNNING)
    temp_db.update_task_status("r2", TaskStatus.RUNNING)
    temp_db.update_task_status("r3", TaskStatus.COMPLETED)

    assert temp_db.reset_stale_running_tasks() == 2

    assert temp_db.get_task("r1")["status"] == TaskStatus.INTERRUPTED.value
    assert temp_db.get_task("r2")["status"] == TaskStatus.INTERRUPTED.value
    # 已完成的任务不能被误改
    assert temp_db.get_task("r3")["status"] == TaskStatus.COMPLETED.value

    # 幂等：再次调用没有 running 可改
    assert temp_db.reset_stale_running_tasks() == 0


def test_upsert_volume_inserts_then_updates(temp_db):
    temp_db.create_task("v1", "/src", ["a.txt"], "/apps", "arch", 1024)

    temp_db.upsert_volume("v1", 1, "/apps/arch.part0001.zip", "/tmp/1",
                          md5="aaa", size_bytes=100, start_offset=0)
    row = temp_db.get_volume("v1", 1)
    assert row["md5"] == "aaa"
    assert row["size_bytes"] == 100
    assert row["start_offset"] == 0
    assert row["status"] == "pending"

    # 同一卷再写一次应当是更新，不是插入新行
    temp_db.upsert_volume("v1", 1, "/apps/arch.part0001.zip", "/tmp/1",
                          md5="bbb", size_bytes=120, start_offset=0)
    rows = temp_db.get_all_volumes("v1")
    assert len(rows) == 1
    assert rows[0]["md5"] == "bbb"
    assert rows[0]["size_bytes"] == 120


def test_upsert_volume_keeps_upload_status(temp_db):
    """元数据更新不能把已上传的状态冲掉"""
    temp_db.create_task("v2", "/src", ["a.txt"], "/apps", "arch", 1024)
    temp_db.upsert_volume("v2", 1, "/apps/arch.part0001.zip", "/tmp/1", md5="aaa")
    temp_db.update_volume_status("v2", 1, "done")

    temp_db.upsert_volume("v2", 1, "/apps/arch.part0001.zip", "/tmp/1",
                          md5="aaa", size_bytes=100, start_offset=0)

    row = temp_db.get_volume("v2", 1)
    assert row["status"] == "done"
    assert row["size_bytes"] == 100


def test_task_stores_fingerprint_and_format_version(temp_db):
    temp_db.create_task("f1", "/src", ["a.txt"], "/apps", "arch", 1024,
                        fingerprint="deadbeef", format_version=2)
    task = temp_db.get_task("f1")
    assert task["fingerprint"] == "deadbeef"
    assert task["format_version"] == 2


def test_migration_adds_columns_to_old_db(tmp_path):
    """老库（没有 fingerprint / size_bytes）打开后应自动补列"""
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, source_dir TEXT NOT NULL,
            file_list_json TEXT NOT NULL, remote_dir TEXT NOT NULL,
            base_name TEXT NOT NULL, volume_size_bytes INTEGER NOT NULL,
            remote_suffix TEXT NOT NULL DEFAULT '.zip',
            total_volumes INTEGER DEFAULT 0, completed_volumes INTEGER DEFAULT 0,
            status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            fixed_modified_at REAL, error_message TEXT);
        CREATE TABLE volumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            part_number INTEGER NOT NULL, remote_path TEXT NOT NULL,
            local_temp_path TEXT, md5 TEXT, status TEXT NOT NULL,
            uploaded_size INTEGER DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
    """)
    conn.commit()
    conn.close()

    db = TaskDB(db_path=str(db_path))
    db.create_task("m1", "/src", ["a.txt"], "/apps", "arch", 1024,
                   fingerprint="abc", format_version=2)
    db.upsert_volume("m1", 1, "/apps/arch.part0001.zip", "/tmp/1",
                     md5="x", size_bytes=10, start_offset=0)

    assert db.get_task("m1")["fingerprint"] == "abc"
    assert db.get_volume("m1", 1)["size_bytes"] == 10