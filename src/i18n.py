# src/i18n.py
"""
多语言支持（gettext）。

为什么用 gettext 而不是 Qt 的 tr()/ts：
    tr() 只能覆盖界面，CLI 的提示语要另做一套；gettext 是标准库自带的，
    一个目录同时服务命令行和图形界面。

为什么源字符串用中文：
    代码里的提示语本来就是中文，改成英文 msgid 意味着全量重写一遍，
    收益只有"翻译者更顺眼"。这里选择代价最小的路线：
    中文是源语言（缺目录时原样显示），英文提供 locale/en/LC_MESSAGES/streampack.mo。
"""
import gettext
import locale
import os
from pathlib import Path
from typing import List, Optional

from src.logger import get_logger

logger = get_logger(__name__)

DOMAIN = "streampack"
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
ENV_LANG = "STREAMPACK_LANG"

# 目前支持的语言；"zh" 不需要翻译目录（源语言）
SUPPORTED = ("zh", "en")

_translation: gettext.NullTranslations = gettext.NullTranslations()
_current_language: str = "zh"


def _system_language() -> str:
    """从系统区域推断语言，识别不了就当中文。"""
    candidates = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            candidates.append(value)
    try:
        candidates.append(locale.getlocale()[0] or "")
    except (ValueError, TypeError):
        pass

    for item in candidates:
        lowered = item.lower()
        if lowered.startswith("zh"):
            return "zh"
        if lowered.startswith("en"):
            return "en"
    return "zh"


def available_languages() -> List[str]:
    """
    支持的语言。

    "zh" 是源语言，不需要翻译目录也能工作，所以一律列出；
    其它语言只在真的有 .mo 时才算可用。
    """
    installed = set()
    if LOCALE_DIR.is_dir():
        for d in LOCALE_DIR.iterdir():
            if d.is_dir() and (d / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file():
                installed.add(d.name)

    result = []
    for lang in SUPPORTED:
        if lang == "zh" or lang in installed:
            result.append(lang)
    return result


def set_language(lang: Optional[str] = None) -> str:
    """
    设置当前语言，返回实际生效的语言。

    优先级：显式参数 > 环境变量 STREAMPACK_LANG > 系统区域。
    传 "auto" 或留空表示走自动判断。
    """
    global _translation, _current_language

    requested = (lang or os.environ.get(ENV_LANG) or "auto").strip().lower()
    if requested in ("auto", "", "system"):
        requested = _system_language()
    if requested not in SUPPORTED:
        logger.warning(f"不支持的语言 {requested!r}，回退到中文")
        requested = "zh"

    _translation = gettext.translation(
        DOMAIN, localedir=str(LOCALE_DIR), languages=[requested], fallback=True
    )
    _current_language = requested
    return requested


def current_language() -> str:
    return _current_language


def _(message: str) -> str:
    """翻译一条文案；没有对应目录时原样返回。"""
    return _translation.gettext(message)


def _f(template: str, *args, **kwargs) -> str:
    """
    先翻译模板，再格式化。

    用法：_f("[OK] 任务 {0} 完成", task_id)
    把变量留在 .format 里，而不是写 f-string —— 否则 msgid 每次都不同，
    翻译表永远匹配不上。
    """
    return _(template).format(*args, **kwargs)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)
