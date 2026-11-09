# tests/test_find.py
"""分卷索引的本地查询：find 命令与 find_files 查询。"""
import pytest

from src.database.task_db import TaskDB


@pytest.fixture
def db(tmp_path):
    return TaskDB(db_path=str(tmp_path / "find.db"))


def seed(db: TaskDB):
    db.create_task("t1", "/src", ["photos", "docs"], "/apps/Test", "backup",
                   1024, format_version=2)
    db.upsert_volume("t1", 1, "/apps/Test/backup.part0001.zip",
                     files=["src/photos/a.jpg", "src/photos/b.jpg"])
    db.upsert_volume("t1", 2, "/apps/Test/backup.part0002.zip",
                     files=["src/docs/c.pdf"])


def test_find_files_is_case_insensitive(db):
    seed(db)
    hits = db.find_files("PHOTOS")
    assert len(hits) == 2
    assert {h["part_number"] for h in hits} == {1}
    assert hits[0]["remote_path"].endswith("part0001.zip")


def test_find_files_matches_single_file(db):
    seed(db)
    hits = db.find_files("c.pdf")
    assert len(hits) == 1
    assert hits[0]["part_number"] == 2
    assert hits[0]["base_name"] == "backup"


def test_find_files_can_scope_to_task(db):
    seed(db)
    db.create_task("t2", "/other", ["x"], "/apps/Test", "other", 1024,
                   format_version=2)
    db.upsert_volume("t2", 1, "/apps/Test/other.part0001.zip",
                     files=["x/photos/z.jpg"])

    assert len(db.find_files("photos")) == 3
    assert len(db.find_files("photos", task_id="t2")) == 1


def test_find_files_ignores_volumes_without_mapping(db):
    """连续流布局不记录文件映射，不该出现在结果里"""
    db.create_task("t3", "/src", ["a"], "/r", "continuous-task", 1024,
                   format_version=2)
    db.upsert_volume("t3", 1, "/r/continuous-task.part0001.zip")

    assert db.find_files("anything") == []


def test_find_files_respects_limit(db):
    db.create_task("t4", "/src", ["d"], "/r", "many", 1024, format_version=2)
    files = [f"d/file{i:03d}.bin" for i in range(50)]
    db.upsert_volume("t4", 1, "/r/many.part0001.zip", files=files)

    assert len(db.find_files("file", limit=10)) == 10


def test_cli_find_prints_volume(db, monkeypatch):
    from click.testing import CliRunner

    import src.cli as cli_module

    seed(db)
    monkeypatch.setattr(cli_module, "TaskDB", lambda *a, **k: db)

    result = CliRunner().invoke(cli_module.cli, ["find", "photo"])

    assert result.exit_code == 0
    assert "第 1 卷" in result.output
    assert "part0001.zip" in result.output


def test_cli_find_reports_miss(monkeypatch, tmp_path):
    from click.testing import CliRunner

    import src.cli as cli_module

    empty = TaskDB(db_path=str(tmp_path / "empty.db"))
    monkeypatch.setattr(cli_module, "TaskDB", lambda *a, **k: empty)

    result = CliRunner().invoke(cli_module.cli, ["find", "nope"])

    assert result.exit_code == 0
    assert "没有找到" in result.output


def test_gui_has_find_action(qapp, tmp_path):
    from src.database.task_db import TaskDB as RealDB
    from src.gui.qt_app import MainWindow
    from src.gui.viewmodel import TaskListViewModel

    win = MainWindow(viewmodel=TaskListViewModel(db=RealDB(db_path=str(tmp_path / "g.db"))),
                     refresh_ms=600000)
    try:
        assert win.action_find is not None
        assert win.action_find.shortcut().toString() == "Ctrl+F"
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
