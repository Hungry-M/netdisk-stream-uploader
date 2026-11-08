# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：产出一个目录，里面两个 exe。

    dist/StreamPack/
        streampack.exe        命令行版（保留控制台，看得到日志）
        streampack-gui.exe    图形版（无控制台窗口）
        _internal/            两者的共同依赖

用法（在项目根目录）：
    python -m PyInstaller packaging/streampack.spec --noconfirm --clean

注意：PyInstaller 6 起 EXE() 不再接收 Analysis 对象，要传 PYZ + 各部分产物，
目录模式还要 exclude_binaries=True，依赖由 COLLECT 收拢。
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent   # SPECPATH 是 packaging/ 本身

# 界面翻译必须在包里，否则打包后只剩中文；图标给窗口用
DATAS = []
if (ROOT / "locale").is_dir():
    DATAS.append((str(ROOT / "locale"), "locale"))
if (ROOT / "assets").is_dir():
    DATAS.append((str(ROOT / "assets"), "assets"))

ICON = str(ROOT / "assets" / "streampack.ico")
if not Path(ICON).is_file():
    ICON = None

COMMON = dict(
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    # shiboken6 在运行时才 import __future__，PyInstaller 静态分析抓不到，
    # 不显式声明会导致 GUI 启动即崩（ModuleNotFoundError: __future__）
    hiddenimports=["__future__"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore"],
)

cli_analysis = Analysis([str(ROOT / "main.py")], **COMMON)
gui_analysis = Analysis([str(ROOT / "gui_main.py")], **COMMON)

cli_pyz = PYZ(cli_analysis.pure)
gui_pyz = PYZ(gui_analysis.pure)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="streampack",
    console=True,          # 命令行版必须留控制台
    icon=ICON,
    upx=False,
    strip=False,
)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="streampack-gui",
    console=False,         # 图形版不要黑框
    icon=ICON,
    upx=False,
    strip=False,
)

COLLECT(
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    name="StreamPack",
    upx=False,
    strip=False,
)
