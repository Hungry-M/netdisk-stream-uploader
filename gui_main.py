# gui_main.py
"""
图形界面入口。

单独一个文件是为了打包：命令行入口要保留控制台（输出要看得到），
图形入口则不要黑框，PyInstaller 对这两者要分别设置 console=True/False。
"""
import sys

from src.gui import run_gui

if __name__ == "__main__":
    sys.exit(run_gui())
