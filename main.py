# main.py
import os
import sys
import time
import logging
from typing import List, Optional

import click


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s',
        force=True,
    )


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


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    debug = False
    mode = "cli"
    remaining: List[str] = []
    for arg in argv:
        if arg == "--debug":
            debug = True
        elif arg == "--gui":
            mode = "gui"
        else:
            remaining.append(arg)

    _setup_logging("DEBUG" if debug else "INFO")

    if mode == "gui":
        return _run_gui()
    return _run_cli(remaining)


if __name__ == "__main__":
    sys.exit(main())