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
from typing import List, Optional, Tuple

from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QStyle, QTableView, QVBoxLayout,
    QWidget,
)

from src.i18n import _
from src.config.user_config import config_path
from src.gui.new_task_dialog import NewTaskDialog, build_env
from src.gui.settings_dialog import SettingsDialog
from src.gui.theme import apply_theme, apply_window_effects
from src.gui.viewmodel import TaskListViewModel
from src.version import __version__

COLUMNS = [_("任务 ID"), _("名称"), _("状态"), _("进度"), _("目标")]


def _app_icon() -> Optional[QIcon]:
    """窗口图标。打包后在 _MEIPASS/assets，源码运行在仓库根 assets/。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    icon_path = base / "assets" / "streampack.ico"
    return QIcon(str(icon_path)) if icon_path.is_file() else None


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
        self._dark = apply_theme(app, _theme_preference()) if app else False

        self.setWindowTitle(f"StreamPack {__version__} — " + _("分卷流式网盘上传"))
        icon = _app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(920, 560)
        self.setAcceptDrops(True)          # 支持从资源管理器拖文件/文件夹进来

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
        self.btn_new = QPushButton(_("新建任务"))
        self.btn_refresh = QPushButton(_("刷新"))
        self.btn_resume = QPushButton(_("续传"))
        self.btn_volumes = QPushButton(_("查看分卷"))
        self.btn_delete = QPushButton(_("删除记录"))

        for btn, pixmap in (
            (self.btn_new, QStyle.StandardPixmap.SP_FileDialogNewFolder),
            (self.btn_refresh, QStyle.StandardPixmap.SP_BrowserReload),
            (self.btn_resume, QStyle.StandardPixmap.SP_MediaPlay),
            (self.btn_volumes, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            (self.btn_delete, QStyle.StandardPixmap.SP_TrashIcon),
        ):
            btn.setIcon(self.style().standardIcon(pixmap))
            buttons.addWidget(btn)
        self.btn_new.clicked.connect(lambda: self.new_task())
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

    # ---------- 菜单 ----------

    def _build_tools_menu(self) -> None:
        """
        「设置」菜单：新建任务、配置入口、以及仅打包后出现的自签名。

        自签名只在打包/安装后的界面里出现——源码运行时签的是 python.exe，没有意义。
        """
        menu = self.menuBar().addMenu(_("设置"))

        self.action_new = menu.addAction(_("新建上传任务…"))
        self.action_new.setShortcut("Ctrl+N")
        self.action_new.triggered.connect(self.new_task)

        menu.addSeparator()

        self.action_settings = menu.addAction(_("设置…"))
        self.action_settings.triggered.connect(self.open_settings)

        self.action_config = menu.addAction(_("打开配置文件"))
        self.action_config.triggered.connect(lambda: self._open_path(config_path()))

        self.action_data = menu.addAction(_("打开数据目录"))
        self.action_data.triggered.connect(
            lambda: self._open_path(Path.home() / ".netdisk_uploader")
        )

        self.action_trust = None
        if getattr(sys, "frozen", False):
            menu.addSeparator()
            self.action_trust = menu.addAction(_("本地信任并签名（仅本机）…"))
            self.action_trust.triggered.connect(self.trust_and_sign)

        menu.addSeparator()
        self.action_about = menu.addAction(_("关于"))
        self.action_about.triggered.connect(self.show_about)

    @staticmethod
    def _open_path(path: Path) -> None:
        path = Path(path)
        if not path.exists():
            path = path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_about(self) -> None:
        QMessageBox.information(
            self, _("关于"),
            _f("StreamPack {0}\n\n分卷流式压缩并上传网盘。\n"
               "数据目录：{1}", __version__, Path.home() / ".netdisk_uploader"),
        )

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == dialog.Accepted:
            QMessageBox.information(
                self, _("设置"),
                _("已保存。语言和主题的改动在下次启动时生效。"),
            )

    # ---------- 新建任务 ----------

    def new_task(self, preset_paths=None) -> None:
        """打开新建任务对话框；也可以从拖放带入路径。"""
        if self._process is not None and self._process.state() != QProcess.NotRunning:
            QMessageBox.information(self, _("新建上传任务"), _("已有任务在运行，等它结束"))
            return

        dialog = NewTaskDialog(self, preset_paths=preset_paths)
        if dialog.exec() != dialog.Accepted:
            return

        values = dialog.values()
        self._remember_remote(str(values.get("app_name") or ""),
                              str(values.get("remote_subdir") or ""))
        self._run_cli(dialog.to_args(), env=build_env(str(values.get("password") or "")))

    @staticmethod
    def _remember_remote(app_name: str, subdir: str) -> None:
        """用户在对话框里改过应用名/子目录就记进配置，下次不用重填。"""
        if not app_name:
            return
        try:
            from src.config.user_config import build_settings, set_value

            current = build_settings()
            if app_name != current.app_name:
                set_value("app_name", app_name)
            if subdir != current.remote_subdir:
                set_value("remote_subdir", subdir)
        except Exception:
            pass          # 记不住不影响这次上传

    # ---------- 拖放 ----------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.new_task(preset_paths=paths)

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

    def _cli_invocation(self) -> Optional[Tuple[str, List[str]]]:
        """
        打包后调同目录的 streampack.exe；源码运行用 python main.py。

        之前直接写死 `sys.executable + ["main.py"]`，打包后等于让 GUI exe
        去执行一个叫 main.py 的参数，续传按钮会静默失败。
        """
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).parent / "streampack.exe"
            return (str(exe), []) if exe.is_file() else None
        return (sys.executable, ["main.py"])

    def _run_cli(self, args: List[str], env: Optional[dict] = None) -> None:
        invocation = self._cli_invocation()
        if invocation is None:
            self._append_log(_("找不到 streampack.exe，无法执行命令行任务"))
            return

        program, prefix = invocation
        self._append_log("$ " + " ".join([Path(program).name] + prefix + args))

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        if env is not None:
            from PySide6.QtCore import QProcessEnvironment

            qenv = QProcessEnvironment()
            for key, value in env.items():
                qenv.insert(key, value)
            self._process.setProcessEnvironment(qenv)

        self._process.readyReadStandardOutput.connect(self._drain_output)
        self._process.finished.connect(self._on_finished)
        self._process.start(program, prefix + args)

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


def _theme_preference() -> str:
    """读配置里的主题偏好；配置坏了也不能挡住界面启动。"""
    try:
        from src.config.user_config import build_settings

        return str(build_settings().theme or "auto")
    except Exception:
        return "auto"


def run_gui(argv: Optional[List[str]] = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    window = MainWindow()
    window.show()
    apply_window_effects(window, window._dark)
    return app.exec()
