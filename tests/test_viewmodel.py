# tests/test_viewmodel.py
"""GUI 逻辑层：与 UI 无关，可直接单测。"""
import pytest

from src.database.task_db import TaskDB, TaskStatus
from src.gui.viewmodel import TaskListViewModel, TaskRow


@pytest.fixture
def vm(tmp_path):
    db = TaskDB(db_path=str(tmp_path / "vm.db"))
    return TaskListViewModel(db=db), db


def _add(db, task_id, name="arch", volumes=3, completed=0, status=TaskStatus.PENDING):
    db.create_task(task_id, "/src", ["a.txt"], "/apps", name, 1024)
    for i in range(1, volumes + 1):
        db.add_volume(task_id, i, f"/apps/{name}.part{i:04d}.zip")
    db.update_task_progress(task_id, completed, volumes)
    db.update_task_status(task_id, status)


def test_refresh_builds_rows(vm):
    model, db = vm
    _add(db, "t1", "backup", volumes=4, completed=2)

    rows = model.refresh()
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, TaskRow)
    assert row.task_id == "t1"
    assert row.target == "/apps/backup.zip"
    assert (row.completed, row.total) == (2, 4)
    assert row.progress == pytest.approx(0.5)


def test_progress_is_zero_without_total(vm):
    model, db = vm
    _add(db, "t1", volumes=0)
    assert model.refresh()[0].progress == 0.0


def test_resumable_excludes_completed(vm):
    model, db = vm
    _add(db, "done", "a", status=TaskStatus.COMPLETED)
    _add(db, "halted", "b", status=TaskStatus.INTERRUPTED)

    rows = {r.task_id: r for r in model.refresh()}
    assert rows["done"].resumable is False
    assert rows["halted"].resumable is True


def test_status_label_is_localized(vm):
    model, db = vm
    _add(db, "t1", status=TaskStatus.INTERRUPTED)
    assert model.refresh()[0].status_label == "已中断"


def test_refresh_resets_stale_running(vm):
    """界面不能一直显示"进行中"—— 启动时把 running 复位"""
    model, db = vm
    _add(db, "t1", status=TaskStatus.RUNNING)

    rows = model.refresh(reset_stale=True)
    assert rows[0].status == TaskStatus.INTERRUPTED.value


def test_refresh_can_skip_reset(vm):
    model, db = vm
    _add(db, "t1", status=TaskStatus.RUNNING)

    rows = model.refresh(reset_stale=False)
    assert rows[0].status == TaskStatus.RUNNING.value


def test_volumes_listing(vm):
    model, db = vm
    _add(db, "t1", volumes=2)
    db.upsert_volume("t1", 1, "/apps/arch.part0001.zip", "/tmp/1",
                     md5="abc", size_bytes=100, start_offset=0)

    vols = model.volumes("t1")
    assert [v.part_number for v in vols] == [1, 2]
    assert vols[0].md5 == "abc"
    assert vols[0].size_bytes == 100
    assert vols[1].md5 is None


def test_summary_counts(vm):
    model, db = vm
    _add(db, "a", "x", status=TaskStatus.COMPLETED)
    _add(db, "b", "y", status=TaskStatus.INTERRUPTED)
    _add(db, "c", "z", status=TaskStatus.INTERRUPTED)

    assert model.summary() == {"completed": 1, "interrupted": 2}


def test_summary_labels_are_localized(vm):
    model, db = vm
    _add(db, "a", "x", status=TaskStatus.COMPLETED)
    _add(db, "b", "y", status=TaskStatus.FAILED)

    assert model.summary_labels() == {"已完成": 1, "失败": 1}


def test_label_for_unknown_status_falls_back(vm):
    from src.gui.viewmodel import label_for

    assert label_for("some_new_state") == "some_new_state"


def test_delete_removes_task_and_volumes(vm):
    model, db = vm
    _add(db, "t1", volumes=2)

    model.delete("t1")

    assert model.refresh() == []
    assert db.get_all_volumes("t1") == []


def test_empty_database(vm):
    model, _ = vm
    assert model.refresh() == []
    assert model.summary() == {}
