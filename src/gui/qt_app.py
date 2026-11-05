# src/gui/qt_app.py
"""
PySide6 视图层。

只做三件事：把 viewmodel 的数据画成表格、接住按钮、把子进程的输出倒进日志框。

**上传动作通过子进程调用 CLI 完成**（`python main.py resume <id>`），而不是在
GUI 里再实现一遍：这样压缩/校验/重试只有一份实现，界面卡死的风险也没有。
"""
import sys
from typing import List, Optional

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableView, QVBoxLayout, QWidget,
)

from src.gui.viewmodel import TaskListViewModel

COLUMNS = ["任务 ID", "名称", "状态", "进度", "目标"]


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

        self.setWindowTitle("StreamPack — 分卷流式网盘上传")
        self.resize(920, 560)

        central = QWidget()
        layout = QVBoxLayout(central)

        self.table = QTableView()
        self.model = TaskTableModel()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _: self.show_volumes())
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_resume = QPushButton("续传")
        self.btn_volumes = QPushButton("查看分卷")
        self.btn_delete = QPushButton("删除记录")
        for btn in (self.btn_refresh, self.btn_resume, self.btn_volumes, self.btn_delete):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status = QLabel("")
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

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)

        self.refresh()

    # ---------- 数据 ----------

    def refresh(self) -> None:
        rows = self.vm.refresh()
        self.model.load(rows)

        counts = self.vm.summary()
        if counts:
            text = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        else:
            text = "暂无任务"
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
            self._append_log("先选一个任务")
            return
        answer = QMessageBox.question(
            self, "删除记录",
            f"删除任务 {task_id} 的本地记录？\n（不影响已上传到网盘的文件）",
        )
        if answer != QMessageBox.Yes:
            return
        self.vm.delete(task_id)
        self.refresh()
        self._append_log(f"已删除任务记录 {task_id}")

    def show_volumes(self) -> None:
        task_id = self.selected_task_id()
        if not task_id:
            self._append_log("先选一个任务")
            return
        volumes = self.vm.volumes(task_id)
        if not volumes:
            QMessageBox.information(self, "分卷", "该任务还没有分卷记录")
            return

        lines = [
            f"#{v.part_number:04d}  {v.status:<10} "
            f"{(v.size_bytes or 0) / 1024 / 1024:8.2f} MB  "
            f"{(v.md5 or '-')[:8]}"
            for v in volumes
        ]
        QMessageBox.information(self, f"分卷 · {task_id}", "\n".join(lines[:40]))

    def resume_selected(self) -> None:
        task_id = self.selected_task_id()
        if not task_id:
            self._append_log("先选一个任务")
            return
        if self._process is not None and self._process.state() != QProcess.NotRunning:
            self._append_log("已有任务在运行，等它结束")
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
        self._append_log(f"[进程结束，退出码 {code}]")
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
    return app.exec()
