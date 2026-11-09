# tests/test_index.py
"""self-contained 任务的索引文件：哪一卷里有哪些文件。"""
import json

import pytest


class FakeProvider:
    name = "fake"
    default_root = "/apps/Test"

    def __init__(self):
        self.uploads = {}

    def upload_file(self, remote_path, fileobj, size=None):
        self.uploads[remote_path] = fileobj.read()


class FakeUploader:
    remote_suffix = ".zip"


def test_index_lists_files_per_volume():
    from src.cli import _upload_index

    provider = FakeProvider()
    task = {
        "base_name": "backup",
        "remote_dir": "/apps/Test/daily",
        "volume_size_bytes": 1024 * 1024,
    }
    volume_files = {1: ["photos/a.jpg", "photos/b.jpg"], 2: ["docs/c.pdf"]}

    _upload_index(provider, FakeUploader(), task, "task-1",
                  volume_files, "self-contained", "folder")

    remote = "/apps/Test/daily/backup.index.json"
    assert remote in provider.uploads

    payload = json.loads(provider.uploads[remote])
    assert payload["layout"] == "self-contained"
    assert payload["pack_by"] == "folder"
    assert [v["part"] for v in payload["volumes"]] == [1, 2]
    assert payload["volumes"][0]["files"] == ["photos/a.jpg", "photos/b.jpg"]
    assert payload["volumes"][0]["remote_path"] == "/apps/Test/daily/backup.part0001.zip"


def test_index_skipped_for_continuous_layout():
    from src.cli import _upload_index

    provider = FakeProvider()
    task = {"base_name": "b", "remote_dir": "/r", "volume_size_bytes": 1}
    _upload_index(provider, FakeUploader(), task, "t", {1: ["a"]}, "continuous", "size")
    assert provider.uploads == {}


def test_index_failure_does_not_raise():
    """索引是附加信息，上传失败不能影响已完成的任务"""
    from src.cli import _upload_index

    class Broken(FakeProvider):
        def upload_file(self, remote_path, fileobj, size=None):
            raise OSError("network down")

    task = {"base_name": "b", "remote_dir": "/r", "volume_size_bytes": 1}
    _upload_index(Broken(), FakeUploader(), task, "t", {1: ["a"]}, "self-contained", "size")


def test_gui_dialog_blocks_oversized_file(qapp, tmp_path):
    """GUI 侧也要前置检查，不能等子进程跑起来再报错"""
    from PySide6.QtWidgets import QMessageBox

    from src.gui.new_task_dialog import NewTaskDialog

    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "huge.bin").write_bytes(b"x" * (3 * 1024 * 1024))

    dialog = NewTaskDialog(preset_paths=[folder])
    captured = {}

    try:
        dialog.layout_combo.setCurrentIndex(
            dialog.layout_combo.findData("self-contained"))
        dialog.volume_spin.setValue(1)              # 1 MB，装不下 3 MB

        original = QMessageBox.warning

        def fake_warning(parent, title, text, *a, **k):
            captured["text"] = text

        QMessageBox.warning = staticmethod(fake_warning)
        try:
            dialog._on_accept()
        finally:
            QMessageBox.warning = original

        assert "_accepted" not in captured
        assert "huge.bin" in captured["text"]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
