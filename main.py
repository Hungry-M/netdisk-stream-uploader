# main.py
import os
import sys
import time
import logging
from typing import List, Optional

import click

from src.logger import setup_logging


def _run_cli(argv: List[str]) -> int:
    from src.cli import cli
    try:
        cli.main(args=argv, standalone_mode=False)
        return 0
    except click.exceptions.Abort:
        # Click 将 KeyboardInterrupt 转成 Abort
        _exit_with("[STOP] 已中止", 130)
    except KeyboardInterrupt:
        _exit_with("[STOP] 已中止", 130)
    except SystemExit as e:
        return int(e.code) if e.code else 0
    return 0


def _exit_with(message: str, code: int) -> None:
    """先打印消息，flush，短暂延迟让控制台驱动处理，再强制退出。"""
    try:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        logging.shutdown()
    except Exception:
        pass
    time.sleep(0.1)
    os._exit(code)


def _run_gui() -> int:
    try:
        from src.gui import run_gui
    except ImportError:
        print(
            "GUI 尚未实现。使用 `python main.py --help` 查看命令行用法。",
            file=sys.stderr,
        )
        return 1
    return run_gui()


def _parse_global_args(argv: List[str]):
    """
    摘出 main 自己的全局开关，剩下的交给 click。

    支持：--debug / --quiet / --gui / --log-file <路径> / --log-file=<路径>
    """
    debug = False
    quiet = False
    mode = "cli"
    log_file: Optional[str] = None
    remaining: List[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--debug":
            debug = True
        elif arg == "--quiet":
            quiet = True
        elif arg == "--gui":
            mode = "gui"
        elif arg == "--log-file":
            i += 1
            if i >= len(argv):
                print("--log-file 缺少路径参数", file=sys.stderr)
                return None
            log_file = argv[i]
        elif arg.startswith("--log-file="):
            log_file = arg.split("=", 1)[1]
        else:
            remaining.append(arg)
        i += 1

    # --debug 优先于 --quiet
    level = "DEBUG" if debug else ("WARNING" if quiet else "INFO")
    return mode, level, log_file, remaining


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parsed = _parse_global_args(argv)
    if parsed is None:
        return 2
    mode, level, log_file, remaining = parsed

    setup_logging(level, log_file=log_file)

    if mode == "gui":
        return _run_gui()
    return _run_cli(remaining)


if __name__ == "__main__":
    sys.exit(main())
