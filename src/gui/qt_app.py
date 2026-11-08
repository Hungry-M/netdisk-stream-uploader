# src/gui/qt_app.py
"""
PySide6 视图层。

只做三件事：把 viewmodel 的数据画成表格、接住按钮、把子进程的输出倒进日志框。

**上传动作通过子进程调用 CLI 完成**（`python main.py resume <id>`），而不是在
GUI 里再实现一遍：这样压缩/校验/重试只有一份实现，界面卡死的风险也没有。
"""
import os
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QStyle, QTableView, QVBoxLayout,
    QWidget,
)

from src.i18n import _
from src.gui.theme import apply_theme, apply_window_effects
from src.gui.viewmodel import TaskListViewModel
from src.version import __version__

COLUMNS = [_("任务 ID"), _("名称"), _("状态"), _("进度"), _("目标")]


class TaskTableModel(QStandardItemModel):
    def __init__(self):
        super().__init__(0, len(COLUMNS))
        self.setHorizontalHeaderLabels(COLUMNS)

    def load(self, rows) -> None:
        self.setRowCount(0)
        for row in rows:
            self.appendRow([
                QStandardItem(row.task_id),
                QStandardItem(row.base_name),
                QStandardItem(row.status_label),
                QStandardItem(f"{row.completed}/{row.total}  {row.progress:.0%}"),
                QStandardItem(row.target),
            ])


class MainWindow(QMainWindow):
    def __init__(self, viewmodel: Optional[TaskListViewModel] = None,
                 refresh_ms: int = 2000):
        super().__init__()
        self.vm = viewmodel or TaskListViewModel()
        self._process: Optional[QProcess] = None

        app = QApplication.instance()
        self._dark = apply_theme(app) if app else False

        self.setWindowTitle(f"StreamPack {__version__} — " + _("分卷流式网盘上传"))
        self.resize(920, 560)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.table = QTableView()
        self.model = TaskTableModel()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((150, 150, 90, 110)):
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda _: self.show_volumes())
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_refresh = QPushButton(_("刷新"))
        self.btn_resume = QPushButton(_("续传"))
        self.btn_volumes = QPushButton(_("查看分卷"))
        self.btn_delete = QPushButton(_("删除记录"))

        for btn, pixmap in (
            (self.btn_refresh, QStyle.StandardPixmap.SP_BrowserReload),
            (self.btn_resume, QStyle.StandardPixmap.SP_MediaPlay),
            (self.btn_volumes, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            (self.btn_delete, QStyle.StandardPixmap.SP_TrashIcon),
        ):
            btn.setIcon(self.style().standardIcon(pixmap))
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("statusBar")
        layout.addWidget(self.status)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

        self.setCentralWidget(central)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_resume.clicked.connect(self.resume_selected)
        self.btn_volumes.clicked.connect(self.show_volumes)
        self.btn_delete.clicked.connect(self.delete_selected)

        self._build_tools_menu()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)

        self.refresh()

    # ---------- 工具菜单 ----------

    def _build_tools_menu(self) -> None:
        """
        「本地信任并签名」只在打包/安装后的界面里出现。

        源码运行时签名对象是 python.exe，没有意义，所以隐藏。
        """
        self.action_trust = None
        if not getattr(sys, "frozen", False):
            return

        tools = self.menuBar().addMenu(_("工具"))
        self.action_trust = tools.addAction(_("本地信任并签名（仅本机）…"))
        self.action_trust.triggered.connect(self.trust_and_sign)

    def trust_and_sign(self) -> None:
        """
        启动 helper 给自己签名。

        自己不能改自己：把 PID 和路径交给 helper，本进程先退出，
        helper 等文件解锁后签名，再重新拉起程序。
        """
        helper = Path(sys.executable).parent / "trust-sign.ps1"
        if not helper.is_file():
            QMessageBox.warning(
                self, _("本地信任并签名"),
                _("找不到 trust-sign.ps1，无法签名。\n预计位置：{0}").format(helper),
            )
            return

        text = _(
            "将在当前 Windows 用户下执行：\n\n"
            "· 创建或复用一个本地根证书 CN=Hungry-M\n"
            "· 签发项目子证书 CN=StreamPack\n"
            "· 把根加入「受信任的根证书颁发机构」、子证书加入「受信任的发布者」\n"
            "· 给本程序签名，然后重新打开\n\n"
            "仅对本机当前用户有效，其他电脑不信任；\n"
            "这不是公共 CA 签名，不改变 SmartScreen 信誉。\n\n"
            "签名期间程序会关闭，完成后自动重新打开。"
        )
        if QMessageBox.question(self, _("本地信任并签名"), text) != QMessageBox.Yes:
            return

        args = [
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(helper),
            "-AppPath", sys.executable,
            "-WaitPid", str(os.getpid()),
            "-Relaunch",
        ]
        if not QProcess.startDetached("powershell", args):
            QMessageBox.warning(self, _("本地信任并签名"), _("无法启动签名 helper。"))
            return

        # 必须退出：helper 要等这个进程释放 exe 文件锁
        self.close()

    # ---------- 数据 ----------

    def refresh(self) -> None:
        rows = self.vm.refresh()
        self.model.load(rows)

        counts = self.vm.summary_labels()
        if counts:
            text = " · ".join(f"{label} {count}" for label, count in sorted(counts.items()))
        else:
            text = _("暂无任务")
        self.status.setText(text)

    def selected_task_id(self) -> Optional[str]:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            return None
        return self.model.item(indexes[0].row(), 0).text()

    # ---------- 动作 ----------

    def delete_selected(self) -> None:
        task_id = self.selected_task_id()
        if not task_id:
            self._append_log(_("先选一个任务"))
            return
        answer = QMessageBox.question(
            self, _("删除记录"),
            _("删除任务 {0} 的本地记录？\n（不影响已上传到网盘的文件）").format(task_id),
        )
        if answer != QMessageBox.Yes:
            return
        self.vm.delete(task_id)
        self.refresh()
        self._append_log(f"已删除任务记录 {task_id}")

    def show_volumes(self) -> None:
        task_id = self.selected_task_id()
        if not task_id:
            self._append_log(_("先选一个任务"))
            return
        volumes = self.vm.volumes(task_id)
        if not volumes:
            QMessageBox.information(self, _("分卷"), _("该任务还没有分卷记录"))
            return

        lines = [
            f"#{v.part_number:04d}  {v.status:<10} "
            f"{(v.size_bytes or 0) / 1024 / 1024:8.2f} MB  "
            f"{(v.md5 or '-')[:8]}"
            for v in volumes
        ]
        QMessageBox.information(self, _("分卷") + f" · {task_id}", "\n".join(lines[:40]))

    def resume_selected(self) -> None:
        task_id = self.selected_task_id()
        if not task_id:
            self._append_log(_("先选一个任务"))
            return
        if self._process is not None and self._process.state() != QProcess.NotRunning:
            self._append_log(_("已有任务在运行，等它结束"))
            return
        self._run_cli(["resume", task_id])

    # ---------- 子进程 ----------

    def _run_cli(self, args: List[str]) -> None:
        self._append_log("$ python main.py " + " ".join(args))
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._drain_output)
        self._process.finished.connect(self._on_finished)
        self._process.start(sys.executable, ["main.py"] + args)

    def _drain_output(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._append_log(data.rstrip())

    def _on_finished(self, code: int, _status) -> None:
        self._append_log(_("[进程结束，退出码 {0}]").format(code))
        self.refresh()

    def _append_log(self, text: str) -> None:
        if not text:
            return
        for line in text.splitlines():
            self.log.appendPlainText(line)


def run_gui(argv: Optional[List[str]] = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    window = MainWindow()
    window.show()
    apply_window_effects(window, window._dark)
    return app.exec()
