# tests/test_version.py
"""版本号单一来源：代码里一处，tag 对得上。"""
import re

import pytest

from src.version import __version__


def test_version_looks_like_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_cli_prints_version(capsys):
    from main import main

    assert main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == f"StreamPack {__version__}"


def test_version_flag_wins_over_other_args(capsys):
    """--version 和别的参数混在一起时也应该只打印版本"""
    from main import main

    assert main(["--quiet", "--version", "list"]) == 0
    assert capsys.readouterr().out.strip() == f"StreamPack {__version__}"


def test_gui_title_contains_version():
    """界面标题带版本，用户报问题时能直接说出来"""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.qt_app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(refresh_ms=600000)
    try:
        assert __version__ in window.windowTitle()
    finally:
        window.close()
