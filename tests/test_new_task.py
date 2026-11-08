# tests/test_new_task.py
"""新建任务对话框里能单独测的部分：选文件、拼参数、密码传递。"""
import os
from pathlib import Path

import pytest

from src.gui.new_task_dialog import (
    build_env, build_upload_args, collect_files,
)


def test_collect_files_from_directory(tmp_path):
    root = tmp_path / "photos"
    (root / "sub").mkdir(parents=True)
    (root / "a.jpg").write_bytes(b"a")
    (root / "sub" / "b.jpg").write_bytes(b"b")

    source, files = collect_files([root])

    assert source == root
    assert files == ["a.jpg", "sub/b.jpg"]


def test_collect_files_from_multiple_files_uses_common_parent(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    f1 = tmp_path / "x" / "one.txt"
    f2 = tmp_path / "y" / "two.txt"
    f1.write_text("1")
    f2.write_text("2")

    source, files = collect_files([f1, f2])

    assert source == tmp_path
    assert sorted(files) == ["x/one.txt", "y/two.txt"]


def test_collect_files_empty_selection(tmp_path):
    assert collect_files([]) == (None, [])
    empty = tmp_path / "empty"
    empty.mkdir()
    assert collect_files([empty]) == (None, [])


def test_build_upload_args_minimal(tmp_path):
    args = build_upload_args(tmp_path, ["a.txt"], "backup")
    assert args[:6] == ["upload", "--source", str(tmp_path), "--files", "a.txt", "--name"]
    assert args[6] == "backup"
    assert "--resume" not in args


def test_build_upload_args_full(tmp_path):
    args = build_upload_args(
        tmp_path, ["a.txt", "b.txt"], "backup",
        remote="/apps/StreamPack", volume_size=512 * 1024 * 1024,
        provider="local", resume=True,
    )
    joined = " ".join(args)
    assert "--files a.txt,b.txt" in joined
    assert "--remote /apps/StreamPack" in joined
    assert f"--volume-size {512 * 1024 * 1024}" in joined
    assert "--provider local" in joined
    assert "--resume" in args


def test_build_env_passes_password_via_environment():
    env = build_env("s3cret")
    assert env["STREAMPACK_PASSWORD"] == "s3cret"
    # 不能污染当前进程
    assert os.environ.get("STREAMPACK_PASSWORD") is None


def test_build_env_without_password_does_not_set_it():
    env = build_env("")
    assert "STREAMPACK_PASSWORD" not in env


def test_dialog_prefills_from_dropped_paths(qapp, tmp_path):
    from src.gui.new_task_dialog import NewTaskDialog

    folder = tmp_path / "album"
    folder.mkdir()
    (folder / "1.jpg").write_bytes(b"1")

    dialog = NewTaskDialog(preset_paths=[folder])
    try:
        args = dialog.to_args()
        assert "--source" in args and str(folder) in args
        assert "--files" in args and "1.jpg" in args
        assert "album" in args                       # 名称默认取目录名
    finally:
        dialog.close()


def test_dialog_ok_disabled_without_files(qapp):
    from src.gui.new_task_dialog import NewTaskDialog
    from PySide6.QtWidgets import QDialogButtonBox

    dialog = NewTaskDialog()
    try:
        ok = dialog._buttons.button(QDialogButtonBox.Ok)
        assert ok.isEnabled() is False
    finally:
        dialog.close()
