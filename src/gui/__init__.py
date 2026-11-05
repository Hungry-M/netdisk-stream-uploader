# src/gui/__init__.py
"""
图形界面。

目前只提供与 UI 库无关的逻辑层（viewmodel），视图层待接入 PySide6。
main.py 里的 `from src.gui import run_gui` 会因缺少该名字而走 ImportError 分支，
打印"GUI 尚未实现"的提示。
"""
from src.gui.viewmodel import TaskListViewModel, TaskRow, VolumeRow

__all__ = ["TaskListViewModel", "TaskRow", "VolumeRow"]
