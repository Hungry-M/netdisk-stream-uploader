# src/paths.py
"""
程序数据目录的唯一入口。

默认 `~/.netdisk_uploader`；设置环境变量 `STREAMPACK_HOME` 可以整体搬走。
自动化测试靠它把凭证、令牌、任务库都关进一个沙箱目录里，不碰开发机的真实状态。
"""
import os
from pathlib import Path

ENV_HOME = "STREAMPACK_HOME"
DEFAULT_DIRNAME = ".netdisk_uploader"


def data_dir(create: bool = True) -> Path:
    """数据目录：凭证、令牌、任务库、配置文件都在这。"""
    override = os.environ.get(ENV_HOME)
    path = Path(override).expanduser() if override else Path.home() / DEFAULT_DIRNAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def data_file(*parts: str, create_dir: bool = True) -> Path:
    return data_dir(create=create_dir).joinpath(*parts)
