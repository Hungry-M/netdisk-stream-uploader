# tests/test_settings_dialog.py
"""设置对话框：只写改过的项，等于默认值就 unset。"""
import json

import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("STREAMPACK_CONFIG", str(path))
    return path


def dismiss(dialog, app):
    """关闭并让 Qt 真正回收，否则进程退出时会撞上析构竞态（0xC0000005）"""
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_defaults_produce_no_changes(qapp, isolated_config):
    from src.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        changes = dialog.changes()
    finally:
        dismiss(dialog, qapp)

    # 全是默认值 => 每项都应是 None（表示 unset）
    assert set(changes.values()) == {None}


def test_changed_values_are_written(qapp, isolated_config):
    from src.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        dialog.theme_combo.setCurrentText("dark")
        dialog.retries_spin.setValue(7)
        dialog.remote_edit.setText("/apps/Other")
        dialog._save()
    finally:
        dismiss(dialog, qapp)

    saved = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"
    assert saved["max_upload_retries"] == 7
    assert saved["remote_root"] == "/apps/Other"


def test_reverting_to_default_removes_key(qapp, isolated_config):
    from src.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        dialog.theme_combo.setCurrentText("dark")
        dialog._save()
    finally:
        dismiss(dialog, qapp)

    assert "theme" in json.loads(isolated_config.read_text(encoding="utf-8"))

    dialog = SettingsDialog()
    try:
        dialog.theme_combo.setCurrentText("auto")     # auto 是默认值
        dialog._save()
    finally:
        dismiss(dialog, qapp)

    assert "theme" not in json.loads(isolated_config.read_text(encoding="utf-8"))


def test_env_override_is_not_overwritten(qapp, isolated_config, monkeypatch):
    """环境变量优先级更高，写文件也不会生效，所以不该写"""
    monkeypatch.setenv("STREAMPACK_REMOTE_ROOT", "/apps/FromEnv")

    from src.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        dialog.remote_edit.setText("/apps/FromDialog")
        dialog._save()
    finally:
        dismiss(dialog, qapp)

    assert not isolated_config.exists() or "remote_root" not in json.loads(
        isolated_config.read_text(encoding="utf-8")
    )
