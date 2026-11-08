# tests/test_gui_qt.py
"""
PySide6 视图层的冒烟测试。

用 offscreen 平台跑，不会弹出窗口。没装 PySide6 时整个文件跳过。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

from src.database.task_db import TaskDB, TaskStatus  # noqa: E402
from src.gui.viewmodel import TaskListViewModel      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    from src.gui.qt_app import MainWindow

    db = TaskDB(db_path=str(tmp_path / "gui.db"))
    db.create_task("t1", "/src", ["a.txt"], "/apps", "backup", 1024)
    db.update_task_progress("t1", 1, 4)
    db.update_task_status("t1", TaskStatus.INTERRUPTED)

    vm = TaskListViewModel(db=db)
    win = MainWindow(viewmodel=vm, refresh_ms=600000)   # 测试期间不要自动刷新
    yield win
    win.close()


def test_rows_are_rendered(window):
    assert window.model.rowCount() == 1
    assert window.model.columnCount() == 5
    assert window.model.item(0, 0).text() == "t1"
    assert window.model.item(0, 1).text() == "backup"
    assert window.model.item(0, 2).text() == "已中断"
    assert "1/4" in window.model.item(0, 3).text()


def test_status_bar_summary(window):
    assert "已中断 1" in window.status.text()


def test_refresh_picks_up_new_tasks(window):
    window.vm._db.create_task("t2", "/src", ["b.txt"], "/apps", "other", 1024)
    window.refresh()
    assert window.model.rowCount() == 2


def test_selection_returns_task_id(window):
    window.table.selectRow(0)
    assert window.selected_task_id() == "t1"


def test_no_selection_returns_none(window):
    window.table.clearSelection()
    assert window.selected_task_id() is None


def test_delete_without_selection_only_logs(window, monkeypatch):
    """没选中时只写日志，不弹确认框（避免测试卡在模态对话框）"""
    window.table.clearSelection()
    window.delete_selected()
    assert "先选一个任务" in window.log.toPlainText()


def test_show_volumes_handles_empty(window, monkeypatch):
    window.table.selectRow(0)
    captured = {}

    class FakeBox:
        @staticmethod
        def information(parent, title, text):
            captured["text"] = text

    monkeypatch.setattr("src.gui.qt_app.QMessageBox", FakeBox)
    window.show_volumes()
    assert "还没有分卷记录" in captured["text"]


def test_theme_applies_stylesheet(window):
    """主题必须在窗口构造时就生效，否则会先闪一下默认样式"""
    assert QApplication.instance().styleSheet()


def test_theme_tables_have_no_grid(window):
    assert window.table.showGrid() is False
    assert window.table.alternatingRowColors() is True
    assert window.table.verticalHeader().defaultSectionSize() == 32


def test_is_dark_returns_bool(qapp):
    from src.gui.theme import is_dark

    assert isinstance(is_dark(qapp), bool)


# ---------- 自签名菜单 ----------

def test_trust_menu_hidden_when_running_from_source(qapp, tmp_path):
    """源码运行时签名对象是 python.exe，没有意义，菜单应隐藏"""
    import sys

    from src.gui.qt_app import MainWindow
    from src.gui.viewmodel import TaskListViewModel
    from src.database.task_db import TaskDB

    monkeypatch_target = getattr(sys, "frozen", False)
    try:
        if hasattr(sys, "frozen"):
            del sys.frozen
        win = MainWindow(viewmodel=TaskListViewModel(db=TaskDB(db_path=str(tmp_path / "g.db"))),
                         refresh_ms=600000)
        try:
            assert win.action_trust is None
        finally:
            win.close()
    finally:
        if monkeypatch_target:
            sys.frozen = True


def test_trust_menu_visible_when_frozen(qapp, tmp_path, monkeypatch):
    """打包/安装后才出现菜单，文案要写明「仅本机」"""
    import sys

    from src.database.task_db import TaskDB
    from src.gui.qt_app import MainWindow
    from src.gui.viewmodel import TaskListViewModel

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    win = MainWindow(viewmodel=TaskListViewModel(db=TaskDB(db_path=str(tmp_path / "g2.db"))),
                     refresh_ms=600000)
    try:
        assert win.action_trust is not None
        text = win.action_trust.text()
        assert "信任" in text and "本机" in text
    finally:
        win.close()


def test_trust_and_sign_warns_when_helper_missing(qapp, tmp_path, monkeypatch):
    """找不到 trust-sign.ps1 时只提示，不能静默失败"""
    import sys

    from src.database.task_db import TaskDB
    from src.gui.qt_app import MainWindow
    from src.gui.viewmodel import TaskListViewModel

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "streampack-gui.exe"))

    captured = {}

    class FakeBox:
        @staticmethod
        def warning(parent, title, text):
            captured["text"] = text

        @staticmethod
        def question(parent, title, text):
            captured["question"] = True
            return 0

    monkeypatch.setattr("src.gui.qt_app.QMessageBox", FakeBox)

    win = MainWindow(viewmodel=TaskListViewModel(db=TaskDB(db_path=str(tmp_path / "g3.db"))),
                     refresh_ms=600000)
    try:
        win.trust_and_sign()
        assert "trust-sign.ps1" in captured["text"]
    finally:
        win.close()
