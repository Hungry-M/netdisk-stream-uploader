# src/gui/new_task_dialog.py
"""
新建上传任务的对话框，以及把"选了哪些文件"翻译成 CLI 参数。

拆成两半是因为参数拼装必须有测试——拼错一个 `--resume` 就会静默跑成新任务。
"""
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from src.config.settings import build_remote_root
from src.config.user_config import build_settings
from src.core.compressor import (
    LAYOUT_CONTINUOUS, LAYOUT_SELF_CONTAINED, PACK_BY_FOLDER, PACK_BY_SIZE,
    effective_volume_bytes,
)
from src.i18n import _, _f

DEFAULT_VOLUME_SIZE = 1024 ** 3          # 与 CLI 保持一致：1 GB


# ---------- 纯逻辑 ----------

def collect_files(paths: Iterable[Path]) -> Tuple[Optional[Path], List[str]]:
    """
    把选中的文件/文件夹收敛成 CLI 需要的 (source, files)。

    source 取所有文件的最长公共父目录，files 是相对 source 的 posix 路径。
    目录会递归展开；返回空列表表示没选到任何文件。
    """
    files: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        elif p.is_file():
            files.append(p)

    if not files:
        return None, []

    parents = [str(f.parent) for f in files]
    common = Path(os.path.commonpath(parents)) if len(parents) > 1 else Path(parents[0])
    rel = [f.relative_to(common).as_posix() for f in files]
    return common, rel


def build_upload_args(
    source: Path,
    files: Sequence[str],
    name: str,
    remote: Optional[str] = None,
    volume_size: Optional[int] = None,
    provider: Optional[str] = None,
    resume: bool = False,
    ext: Optional[str] = None,
    layout: Optional[str] = None,
    pack_by: Optional[str] = None,
) -> List[str]:
    """拼 `upload` 子命令的参数。密码不在这里，走环境变量，避免出现在命令行里。"""
    args = ["upload", "--source", str(source), "--files", ",".join(files), "--name", name]
    if remote:
        args += ["--remote", remote]
    if volume_size:
        args += ["--volume-size", str(int(volume_size))]
    if ext and ext != ".zip":
        args += ["--ext", ext]
    if layout and layout != LAYOUT_CONTINUOUS:
        args += ["--layout", layout]
    if pack_by and pack_by != PACK_BY_SIZE:
        args += ["--pack-by", pack_by]
    if provider:
        args += ["--provider", provider]
    if resume:
        args += ["--resume"]
    return args


def build_env(password: str = "") -> Dict[str, str]:
    """密码通过环境变量传给子进程，不出现在命令行/进程列表里。"""
    env = dict(os.environ)
    if password:
        env["STREAMPACK_PASSWORD"] = password
    return env


# ---------- 对话框 ----------

class NewTaskDialog(QDialog):
    """选文件 → 填参数 → 返回 CLI 参数。"""

    def __init__(self, parent: Optional[QWidget] = None, preset_paths: Optional[Sequence[Path]] = None):
        super().__init__(parent)
        self.setWindowTitle(_("新建上传任务"))
        self.setMinimumWidth(520)

        self._source: Optional[Path] = None
        self._files: List[str] = []

        settings = build_settings()

        self.source_label = QLabel(_("还没有选择文件"))
        self.source_label.setWordWrap(True)

        # 系统自带的文件选择器不能同时选文件夹和文件，所以这里收成一个按钮、
        # 两个菜单项；拖放不受这个限制，可以混着拖。
        self.add_button = QToolButton()
        self.add_button.setText(_("添加内容"))
        self.add_button.setPopupMode(QToolButton.InstantPopup)
        add_menu = QMenu(self.add_button)
        add_menu.addAction(_("选择文件夹…"), self._pick_dir)
        add_menu.addAction(_("选择文件…"), self._pick_files)
        self.add_button.setMenu(add_menu)

        pick_row = QHBoxLayout()
        pick_row.addWidget(self.source_label, 1)
        pick_row.addWidget(self.add_button)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("压缩包名称（不含扩展名）"))

        self.app_name_edit = QLineEdit(settings.app_name or "StreamPack")
        self.subdir_edit = QLineEdit(settings.remote_subdir or "")
        self.subdir_edit.setPlaceholderText(_("可留空"))
        self.remote_preview = QLabel()
        self.remote_preview.setEnabled(False)
        self.app_name_edit.textChanged.connect(self._update_remote_preview)
        self.subdir_edit.textChanged.connect(self._update_remote_preview)
        self._update_remote_preview()

        remote_row = QHBoxLayout()
        remote_row.setContentsMargins(0, 0, 0, 0)
        remote_row.addWidget(QLabel(_("应用名")))
        remote_row.addWidget(self.app_name_edit, 1)
        remote_row.addWidget(QLabel(_("子目录")))
        remote_row.addWidget(self.subdir_edit, 1)

        remote_box = QVBoxLayout()
        remote_box.setContentsMargins(0, 0, 0, 0)
        remote_box.setSpacing(2)
        remote_box.addLayout(remote_row)
        remote_box.addWidget(self.remote_preview)
        remote_widget = QWidget()
        remote_widget.setLayout(remote_box)

        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(1, 1024 * 200)             # 单位 MB
        self.volume_spin.setValue(DEFAULT_VOLUME_SIZE // (1024 * 1024))
        self.volume_spin.setSuffix(" MB")

        self.ext_edit = QLineEdit(".zip")
        self.ext_edit.setMaximumWidth(120)
        self.ext_hint = QLabel(_("如 .zip / .zi123p；这只是后缀，内容仍是 ZIP"))
        self.ext_hint.setEnabled(False)

        ext_box = QHBoxLayout()
        ext_box.setContentsMargins(0, 0, 0, 0)
        ext_box.addWidget(self.ext_edit)
        ext_box.addWidget(self.ext_hint, 1)
        ext_widget = QWidget()
        ext_widget.setLayout(ext_box)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText(_("留空则不加密"))

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["baidu", "local"])
        index = self.provider_combo.findText(settings.provider)
        self.provider_combo.setCurrentIndex(index if index >= 0 else 0)

        self.resume_check = QCheckBox(_("同名任务已存在时续传"))
        self.resume_check.setChecked(True)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem(_("连续流（需拼接）"), LAYOUT_CONTINUOUS)
        self.layout_combo.addItem(_("自包含（每卷可单独解压）"), LAYOUT_SELF_CONTAINED)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)

        self.pack_by_combo = QComboBox()
        self.pack_by_combo.addItem(_("按大小装满"), PACK_BY_SIZE)
        self.pack_by_combo.addItem(_("优先整文件夹"), PACK_BY_FOLDER)
        self.pack_by_combo.setEnabled(False)

        self.layout_hint = QLabel(_("连续流体积利用率最高，但任何一卷都要拼接后才能打开"))
        self.layout_hint.setWordWrap(True)
        self.layout_hint.setEnabled(False)

        layout_box = QVBoxLayout()
        layout_box.setContentsMargins(0, 0, 0, 0)
        layout_box.setSpacing(2)
        layout_box.addWidget(self.layout_combo)
        layout_box.addWidget(self.pack_by_combo)
        layout_box.addWidget(self.layout_hint)
        layout_widget = QWidget()
        layout_widget.setLayout(layout_box)

        form = QFormLayout()
        form.addRow(_("内容"), pick_row)
        form.addRow(_("名称"), self.name_edit)
        form.addRow(_("网盘目录"), remote_widget)
        form.addRow(_("分卷大小"), self.volume_spin)
        form.addRow(_("扩展名"), ext_widget)
        form.addRow(_("加密口令"), self.password_edit)
        form.addRow(_("存储后端"), self.provider_combo)
        form.addRow(_("分卷布局"), layout_widget)
        form.addRow("", self.resume_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if preset_paths:
            self.set_paths(preset_paths)
        self._update_ok()

    # ---- 选择 ----

    def _update_remote_preview(self) -> None:
        path = build_remote_root(self.app_name_edit.text(), self.subdir_edit.text())
        self.remote_preview.setText(_f("将上传到 {0}", path))

    def _on_layout_changed(self) -> None:
        self_contained = self.layout_combo.currentData() == LAYOUT_SELF_CONTAINED
        self.pack_by_combo.setEnabled(self_contained)
        self.layout_hint.setText(
            _("每卷是独立 ZIP，可单独解压；单文件不能超过分卷大小")
            if self_contained else
            _("连续流体积利用率最高，但任何一卷都要拼接后才能打开")
        )

    def oversized_files(self) -> List[Path]:
        """self-contained 模式下，哪些文件单独一个就装不进一卷。"""
        if self.layout_combo.currentData() != LAYOUT_SELF_CONTAINED or self._source is None:
            return []
        limit = effective_volume_bytes(self.volume_spin.value() * 1024 * 1024)
        result = []
        for arcname in self._files:
            path = self._source / arcname
            try:
                if path.stat().st_size > limit:
                    result.append(path)
            except OSError:
                continue
        return result

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("选择要打包的文件夹"))
        if path:
            self.set_paths([Path(path)])

    def _pick_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, _("选择要打包的文件"))
        if paths:
            self.set_paths([Path(p) for p in paths])

    def set_paths(self, paths: Sequence[Path]) -> None:
        source, files = collect_files(paths)
        self._source, self._files = source, files
        if source is None:
            self.source_label.setText(_("没有找到可打包的文件"))
        else:
            self.source_label.setText(
                _f("{0} 个文件，源目录 {1}", len(files), source)
            )
            if not self.name_edit.text().strip():
                self.name_edit.setText(source.name or "backup")
        self._update_ok()

    def _update_ok(self) -> None:
        ok = bool(self._files) and bool(self.name_edit.text().strip())
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _on_accept(self) -> None:
        if not self._files:
            QMessageBox.warning(self, _("新建上传任务"), _("还没有选择要打包的内容"))
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, _("新建上传任务"), _("请填写压缩包名称"))
            return

        oversized = self.oversized_files()
        if oversized:
            names = "\n".join(
                f"· {p.relative_to(self._source).as_posix()}"
                f"（{p.stat().st_size / 1024 ** 3:.2f} GB）"
                for p in oversized[:5]
            )
            if len(oversized) > 5:
                names += _f("\n… 另有 {0} 个", len(oversized) - 5)
            QMessageBox.warning(
                self, _("新建上传任务"),
                _f("自包含布局要求每个文件完整落在一卷内，"
                   "以下文件超过分卷大小（{0} MB）：\n\n{1}\n\n"
                   "请调大分卷大小，或改用连续流。",
                   self.volume_spin.value(), names),
            )
            return

        self.accept()

    # ---- 结果 ----

    def values(self) -> Dict[str, object]:
        app_name = self.app_name_edit.text().strip() or "StreamPack"
        subdir = self.subdir_edit.text().strip()
        return {
            "source": self._source,
            "files": list(self._files),
            "name": self.name_edit.text().strip(),
            "app_name": app_name,
            "remote_subdir": subdir,
            "remote": build_remote_root(app_name, subdir),
            "volume_size": self.volume_spin.value() * 1024 * 1024,
            "ext": self.ext_edit.text().strip() or ".zip",
            "provider": self.provider_combo.currentText(),
            "layout": self.layout_combo.currentData(),
            "pack_by": self.pack_by_combo.currentData(),
            "resume": self.resume_check.isChecked(),
            "password": self.password_edit.text(),
        }

    def to_args(self) -> List[str]:
        v = self.values()
        return build_upload_args(
            v["source"], v["files"], v["name"],
            remote=v["remote"], volume_size=v["volume_size"],
            provider=v["provider"], resume=v["resume"], ext=v["ext"],
            layout=v["layout"], pack_by=v["pack_by"],
        )
