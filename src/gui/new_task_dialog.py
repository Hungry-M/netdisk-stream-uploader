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
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QWidget,
)

from src.config.user_config import build_settings
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
) -> List[str]:
    """拼 `upload` 子命令的参数。密码不在这里，走环境变量，避免出现在命令行里。"""
    args = ["upload", "--source", str(source), "--files", ",".join(files), "--name", name]
    if remote:
        args += ["--remote", remote]
    if volume_size:
        args += ["--volume-size", str(int(volume_size))]
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

        btn_dir = QPushButton(_("选择文件夹…"))
        btn_files = QPushButton(_("选择文件…"))
        btn_dir.clicked.connect(self._pick_dir)
        btn_files.clicked.connect(self._pick_files)

        pick_row = QHBoxLayout()
        pick_row.addWidget(self.source_label, 1)
        pick_row.addWidget(btn_dir)
        pick_row.addWidget(btn_files)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("压缩包名称（不含扩展名）"))

        self.remote_edit = QLineEdit(settings.remote_root or "/apps/StreamPack")

        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(1, 1024 * 200)             # 单位 MB
        self.volume_spin.setValue(DEFAULT_VOLUME_SIZE // (1024 * 1024))
        self.volume_spin.setSuffix(" MB")

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText(_("留空则不加密"))

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["baidu", "local"])
        index = self.provider_combo.findText(settings.provider)
        self.provider_combo.setCurrentIndex(index if index >= 0 else 0)

        self.resume_check = QCheckBox(_("同名任务已存在时续传"))
        self.resume_check.setChecked(True)

        form = QFormLayout()
        form.addRow(_("内容"), pick_row)
        form.addRow(_("名称"), self.name_edit)
        form.addRow(_("网盘目录"), self.remote_edit)
        form.addRow(_("分卷大小"), self.volume_spin)
        form.addRow(_("加密口令"), self.password_edit)
        form.addRow(_("存储后端"), self.provider_combo)
        form.addRow("", self.resume_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons

        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if preset_paths:
            self.set_paths(preset_paths)
        self._update_ok()

    # ---- 选择 ----

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
        self.accept()

    # ---- 结果 ----

    def values(self) -> Dict[str, object]:
        return {
            "source": self._source,
            "files": list(self._files),
            "name": self.name_edit.text().strip(),
            "remote": self.remote_edit.text().strip() or None,
            "volume_size": self.volume_spin.value() * 1024 * 1024,
            "provider": self.provider_combo.currentText(),
            "resume": self.resume_check.isChecked(),
            "password": self.password_edit.text(),
        }

    def to_args(self) -> List[str]:
        v = self.values()
        return build_upload_args(
            v["source"], v["files"], v["name"],
            remote=v["remote"], volume_size=v["volume_size"],
            provider=v["provider"], resume=v["resume"],
        )
