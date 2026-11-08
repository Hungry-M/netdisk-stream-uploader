# src/gui/settings_dialog.py
"""
设置对话框：只编辑常用的几项，其余参数仍由 `config set` 管。

保存策略：值等于代码默认值时就把这一项从配置文件里删掉，
这样以后升级改了默认值能自动生效——和 `config unset` 是同一个道理。
"""
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from src.config.settings import ResourceSettings
from src.config.user_config import (
    build_settings, config_path, effective_values, set_value, sources, unset_value,
)
from src.i18n import _, _f

LANGUAGES = ["auto", "zh", "en"]
THEMES = ["auto", "light", "dark"]


class SettingsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(_("设置"))
        self.setMinimumWidth(460)

        self._settings = build_settings()
        self._defaults = ResourceSettings()
        self._effective = effective_values()
        self._sources = sources()

        self.lang_combo = QComboBox()
        self.lang_combo.addItems(LANGUAGES)
        self._select(self.lang_combo, str(self._effective.get("language", "auto")))

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES)
        self._select(self.theme_combo, str(self._effective.get("theme", "auto")))

        self.remote_edit = QLineEdit(str(self._effective.get("remote_root", "")))

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(1, 20)
        self.retries_spin.setValue(int(self._effective.get("max_upload_retries", 3)))

        form = QFormLayout()
        form.addRow(_("界面语言"), self.lang_combo)
        form.addRow(_("界面主题"), self.theme_combo)
        form.addRow(_("默认网盘目录"), self.remote_edit)
        form.addRow(_("单卷重试次数"), self.retries_spin)

        path_hint = QLabel(_f("配置文件：{0}", config_path()))
        path_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_hint.setWordWrap(True)

        buttons_row = QHBoxLayout()
        btn_open_cfg = QPushButton(_("打开配置文件"))
        btn_open_cfg.clicked.connect(lambda: self._open(config_path()))
        btn_open_data = QPushButton(_("打开数据目录"))
        btn_open_data.clicked.connect(lambda: self._open(Path.home() / ".netdisk_uploader"))
        buttons_row.addWidget(btn_open_cfg)
        buttons_row.addWidget(btn_open_data)
        buttons_row.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(path_hint)
        layout.addLayout(buttons_row)
        layout.addWidget(box)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _open(path: Path) -> None:
        path = Path(path)
        if not path.exists():
            # 配置可能还没生成：打开所在目录更有用
            path = path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def changes(self) -> dict:
        """要写入的项。等于默认值的项返回 None，表示应当 unset。"""
        candidates = {
            "language": self.lang_combo.currentText(),
            "theme": self.theme_combo.currentText(),
            "remote_root": self.remote_edit.text().strip(),
            "max_upload_retries": self.retries_spin.value(),
        }
        result = {}
        for name, value in candidates.items():
            result[name] = None if value == getattr(self._defaults, name) else value
        return result

    def _save(self) -> None:
        for name, value in self.changes().items():
            if self._sources.get(name) == "env":
                continue          # 环境变量优先级更高，写文件也不会生效
            if value is None:
                unset_value(name)
            else:
                set_value(name, value)
        self.accept()
