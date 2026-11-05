# src/storage/base.py
"""
存储后端的最小接口。

设计原则：
1. **只保留真正被用到的能力**。分片上传、取上传域名这类目前没接上的方法，
   不进接口——接口是给调用方用的，不是给 provider 的完整 API 做镜像。
2. **错误要能分级**。调用方需要知道"重试有意义吗"，所以异常带 retryable。
3. **不泄露 provider 的原始结构**。返回 RemoteFile，调用方不需要知道百度
   把文件名叫 server_filename、把 id 叫 fs_id。
"""
from dataclasses import dataclass
from typing import BinaryIO, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteFile:
    """云端文件在调用方眼里的样子。"""
    path: str                       # 统一用 / 分隔的绝对路径
    size: int
    mtime: Optional[float] = None
    id: Optional[str] = None        # provider 内部的稳定标识（如百度 fs_id）


@dataclass(frozen=True)
class Capabilities:
    """后端能力声明，调用方据此决定走哪条分支。"""
    can_list: bool = True
    can_delete: bool = True
    max_file_size: int = 4 * 1024 ** 3     # 单文件上限
    needs_app_dir: bool = False            # 百度：可写目录固定在 /apps/{应用名}


class ProviderError(Exception):
    """
    所有后端异常都归一成它。

    retryable 决定上传层的重试策略：网络抖动、超时、5xx 值得重试；
    403 / 路径不存在这类重试多少次都一样。
    """

    def __init__(self, message: str, *, retryable: bool,
                 errno: Optional[int] = None,
                 provider: Optional[str] = None):
        super().__init__(message)
        self.retryable = retryable
        self.errno = errno
        self.provider = provider

    def __str__(self) -> str:
        prefix = f"[{self.provider}] " if self.provider else ""
        return f"{prefix}{super().__str__()}"


@runtime_checkable
class StorageProvider(Protocol):
    """后端协议。实现方只要满足这些方法即可被 VolumeUploader 使用。"""

    name: str
    capabilities: Capabilities
    default_root: str

    def authenticate(self, interactive: bool = True) -> None:
        """完成鉴权。interactive=False 时不得阻塞等待用户输入。"""

    def ensure_dir(self, remote_dir: str) -> None:
        """确保目录存在。"""

    def upload_file(self, remote_path: str, fileobj: BinaryIO,
                    size: int) -> None:
        """把 fileobj 的当前游标到 EOF 写入 remote_path。"""

    def list_dir(self, remote_dir: str) -> List[RemoteFile]:
        """列出目录下的文件（单层）。"""

    def delete_file(self, remote_path: str) -> None:
        """删除一个文件；不存在时抛 ProviderError(retryable=False)。"""


def normalize_remote_path(path: str) -> str:
    """统一成 /a/b/c 形式，并拒绝 .. 越界。"""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ProviderError(f"路径不允许包含 '..': {path}", retryable=False)
    return "/" + "/".join(parts)
