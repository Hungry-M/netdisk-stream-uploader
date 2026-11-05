# src/gui/__init__.py
"""
图形界面。

逻辑层（viewmodel）不依赖任何 UI 库，可直接测试；视图层在 qt_app 里，
需要 PySide6。没装的时候 `main.py --gui` 会给出安装提示，而不是抛栈。
"""
import sys

from src.gui.viewmodel import TaskListViewModel, TaskRow, VolumeRow

__all__ = ["TaskListViewModel", "TaskRow", "VolumeRow", "run_gui"]


def run_gui() -> int:
    try:
        from src.gui.qt_app import run_gui as _run_gui
    except ImportError as e:
        print(
            f"图形界面需要 PySide6：pip install PySide6\n"
            f"（导入失败：{e}）",
            file=sys.stderr,
        )
        return 1
    return _run_gui()
