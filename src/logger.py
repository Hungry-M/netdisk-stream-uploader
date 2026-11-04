# src/logger.py
"""
统一日志配置。

用法：
    from src.logger import get_logger, setup_logging

    setup_logging("INFO")          # 程序入口调用一次
    logger = get_logger(__name__)  # 各模块取自己的 logger
    logger.info("...")

约定：
- 所有模块通过 get_logger(__name__) 获取 logger，不再直接调 logging.getLogger；
- 日志格式集中在 _FORMAT，改格式只改这一处；
- setup_logging 幂等（force=True 会清空旧 handler），重复调用不会重复输出。
"""
import logging
import sys
from typing import Optional, Union

_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
_DATEFMT = None   # 交给 logging 用默认的 %Y-%m-%d %H:%M:%S,%f


def setup_logging(level: Union[str, int] = "INFO",
                  log_file: Optional[str] = None,
                  force: bool = True) -> None:
    """
    配置根 logger。

    Args:
        level: 日志级别，字符串（"DEBUG" / "INFO" / "WARNING" / "ERROR"）
               或 logging 的整数常量；非法值回退 INFO
        log_file: 若指定，同时写入该文件（UTF-8 编码）
        force: 是否清空已存在的 handler。默认 True，保证重复调用不产生重复行
    """
    handlers = [logging.StreamHandler(sys.stderr)]

    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=_parse_level(level),
        format=_FORMAT,
        datefmt=_DATEFMT,
        handlers=handlers,
        force=force,
    )


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。"""
    return logging.getLogger(name)


def set_level(level: Union[str, int]) -> None:
    """运行时调整根 logger 的级别（GUI 的“详细日志”开关会用）。"""
    logging.getLogger().setLevel(_parse_level(level))


def _parse_level(level: Union[str, int]) -> int:
    """把级别写法转成 logging 常量，无法识别时回退 INFO。"""
    if isinstance(level, int):
        return level
    value = getattr(logging, str(level).upper(), None)
    return value if isinstance(value, int) else logging.INFO
