# src/gui/theme.py
"""
界面主题：Fusion 风格 + Fluent 风格 QSS，可选 Win11 Mica 背景。

参考对象是 Windows 11 的「设置」、资源管理器那套 Fluent 语言：
- 控件圆角 4px，卡片 8px
- 用分隔线而不是满边框
- 表格行高 32px，无网格线，选中用 accent 色
- 文字对比度分三档：主文本 / 次要文本 / 禁用

不做的：渐变、色块、自绘标题栏 —— 那会把"现代"做成"花哨"。
"""
import sys
from typing import Optional

from src.logger import get_logger

logger = get_logger(__name__)

_FONT_FAMILY = '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif'
_MONO_FAMILY = 'Consolas, "Cascadia Mono", monospace'

LIGHT_QSS = f"""
QWidget {{
    font-family: {_FONT_FAMILY};
    font-size: 9pt;
    color: #1B1B1B;
}}
QMainWindow, QWidget {{
    background: #FAFAFA;
}}
QTableView {{
    background: #FFFFFF;
    border: 1px solid #E5E5E5;
    border-radius: 4px;
    gridline-color: transparent;
    alternate-background-color: #FBFBFB;
    selection-background-color: #0078D4;
    selection-color: #FFFFFF;
}}
QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid #E5E5E5;
    padding: 6px 8px;
    color: #4A4A4A;
    font-weight: 600;
}}
QPushButton {{
    background: #FFFFFF;
    border: 1px solid #D6D6D6;
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 20px;
}}
QPushButton:hover {{ background: #F3F3F3; }}
QPushButton:pressed {{ background: #EDEDED; }}
QPushButton:disabled {{ color: #9A9A9A; border-color: #E5E5E5; }}
QPlainTextEdit {{
    background: #FFFFFF;
    border: 1px solid #E5E5E5;
    border-radius: 4px;
    font-family: {_MONO_FAMILY};
    font-size: 8.5pt;
    color: #3A3A3A;
}}
QLabel#statusBar {{ color: #605E5C; padding: 2px 2px 6px 2px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #C8C8C8; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #A8A8A8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""

DARK_QSS = f"""
QWidget {{
    font-family: {_FONT_FAMILY};
    font-size: 9pt;
    color: #EDEDED;
}}
QMainWindow, QWidget {{
    background: #202020;
}}
QTableView {{
    background: #272727;
    border: 1px solid #3A3A3A;
    border-radius: 4px;
    gridline-color: transparent;
    alternate-background-color: #2B2B2B;
    selection-background-color: #2564CF;
    selection-color: #FFFFFF;
}}
QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid #3A3A3A;
    padding: 6px 8px;
    color: #C8C8C8;
    font-weight: 600;
}}
QPushButton {{
    background: #2D2D2D;
    border: 1px solid #3F3F3F;
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 20px;
}}
QPushButton:hover {{ background: #353535; }}
QPushButton:pressed {{ background: #3C3C3C; }}
QPushButton:disabled {{ color: #6E6E6E; border-color: #333333; }}
QPlainTextEdit {{
    background: #1B1B1B;
    border: 1px solid #3A3A3A;
    border-radius: 4px;
    font-family: {_MONO_FAMILY};
    font-size: 8.5pt;
    color: #D0D0D0;
}}
QLabel#statusBar {{ color: #A0A0A0; padding: 2px 2px 6px 2px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4A4A4A; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #5E5E5E; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""


def is_dark(app) -> bool:
    """跟随系统深浅色；取不到就当浅色。"""
    try:
        scheme = app.styleHints().colorScheme()
        return getattr(scheme, "name", "") == "Dark" or str(scheme).endswith("Dark")
    except Exception:
        return False


def apply_theme(app) -> bool:
    """应用 Fusion + QSS，返回是否为深色。"""
    dark = is_dark(app)
    try:
        app.setStyle("Fusion")
        app.setStyleSheet(DARK_QSS if dark else LIGHT_QSS)
    except Exception as e:                      # 主题失败不该拖垮界面
        logger.warning(f"应用主题失败: {e}")
        return dark
    return dark


def apply_window_effects(window, dark: Optional[bool] = None) -> None:
    """
    可选的 Win11 窗口效果（Mica 背景 + 深色标题栏）。

    pywinstyles 是纯 ctypes 封装，很小；没装就静默跳过，不影响功能。
    """
    try:
        import pywinstyles  # type: ignore
    except ImportError:
        return

    try:
        if sys.platform == "win32":
            pywinstyles.apply_style(window, "mica")
            if dark:
                pywinstyles.apply_style(window, "dark")
    except Exception as e:
        logger.debug(f"窗口效果不可用: {e}")
