# src/storage/local.py
"""
把"云端"换成本地目录的后端。

用途有三个：
1. 离线预演：不联网、不占配额，完整跑一遍压缩 → 分卷 → 上传 → 清理；
2. 故障注入：配合测试里的包装器，可以精确控制"第几卷失败、失败几次"；
3. 验证抽象：它是 StorageProvider 的第二个实现，能暴露接口里混进来的
   百度特有假设。
"""
import shutil
from pathlib import Path
from typing import BinaryIO, List, Optional

from src.logger import get_logger
from src.storage.base import (
    Capabilities, ProviderError, RemoteFile, normalize_remote_path,
)

logger = get_logger(__name__)

_COPY_CHUNK = 1024 * 1024


class LocalDirProvider:
    """把 remote_path 映射到 root 下的真实文件。"""

    name = "local"
    capabilities = Capabilities(
        can_list=True,
        can_delete=True,
        max_file_size=1 << 62,       # 本地不设限
        needs_app_dir=False,
    )
    default_root = "/backup"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.default_root = "/backup"

    # ---------- 内部 ----------

    def _to_path(self, remote_path: str) -> Path:
        normalized = normalize_remote_path(remote_path)
        relative = normalized.lstrip("/")
        target = (self.root / relative).resolve()

        # normalize 已拦掉 ..，这里再确认一次没有跑出 root（软链接等）
        if target != self.root and self.root not in target.parents:
            raise ProviderError(f"路径越界: {remote_path}", retryable=False,
                                provider=self.name)
        return target

    # ---------- StorageProvider ----------

    def authenticate(self, interactive: bool = True) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_dir(self, remote_dir: str) -> None:
        try:
            self._to_path(remote_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ProviderError(f"创建目录失败: {e}", retryable=True,
                                errno=e.errno, provider=self.name)

    def upload_file(self, remote_path: str, fileobj: BinaryIO,
                    size: Optional[int] = None) -> None:
        target = self._to_path(remote_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免中断留下半截文件被误认为已完成
            tmp = target.with_suffix(target.suffix + ".part")
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(fileobj, fh, _COPY_CHUNK)
            written = tmp.stat().st_size
            if size is not None and written != size:
                tmp.unlink(missing_ok=True)
                raise ProviderError(
                    f"写入字节数不符: 期望 {size}，实际 {written}",
                    retryable=True, provider=self.name,
                )
            tmp.replace(target)
        except ProviderError:
            raise
        except OSError as e:
            raise ProviderError(f"写入失败: {e}", retryable=True,
                                errno=e.errno, provider=self.name)

    def list_dir(self, remote_dir: str) -> List[RemoteFile]:
        directory = self._to_path(remote_dir)
        if not directory.is_dir():
            raise ProviderError(f"目录不存在: {remote_dir}", retryable=False,
                                errno=404, provider=self.name)

        result = []
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if not entry.is_file():
                continue
            stat = entry.stat()
            result.append(RemoteFile(
                path=normalize_remote_path(
                    "/" + entry.relative_to(self.root).as_posix()),
                size=stat.st_size,
                mtime=stat.st_mtime,
                id=None,
            ))
        return result

    def delete_file(self, remote_path: str) -> None:
        target = self._to_path(remote_path)
        if not target.is_file():
            raise ProviderError(f"文件不存在: {remote_path}", retryable=False,
                                errno=404, provider=self.name)
        try:
            target.unlink()
        except OSError as e:
            raise ProviderError(f"删除失败: {e}", retryable=False,
                                errno=e.errno, provider=self.name)
