# src/storage/baidu.py
"""
百度网盘后端：把 BaiduClient 适配成 StorageProvider。

这里做的最重要的一件事不是"包一层"，而是**把百度错误码翻译成 retryable**。
上传层据此决定要不要重试——403 重试三次只是白等。
"""
import re
from typing import BinaryIO, List, Optional

import requests

from src.logger import get_logger
from src.storage.base import (
    Capabilities, ProviderError, RemoteFile, normalize_remote_path,
)

logger = get_logger(__name__)

# 这些错误重试没有意义：权限、路径、参数、token 问题
_NON_RETRYABLE_CODES = {
    2,      # 参数错误
    110,    # token 失效
    111,    # token 过期
    112,    # token 无效
    403,
    404,
    31064,  # 文件不存在
    31066,  # 文件不存在
}

_CODE_RE = re.compile(r"\((\d+)\)")


def _extract_code(message: str) -> Optional[int]:
    match = _CODE_RE.search(message)
    return int(match.group(1)) if match else None


def _map_error(exc: Exception, action: str) -> ProviderError:
    """把百度/requests 的异常翻译成带 retryable 的 ProviderError。"""
    if isinstance(exc, ProviderError):
        return exc

    message = str(exc)
    code = _extract_code(message)

    # 代理坏了：重试同一个代理没有意义，直接给可操作的提示
    from src.net import proxy_hint

    hint = proxy_hint(exc)
    if hint:
        return ProviderError(f"{action}失败: {hint}", retryable=False,
                             errno=code, provider="baidu")

    # 网络层：值得重试
    if isinstance(exc, (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout)):
        return ProviderError(f"{action}失败（网络）: {message}",
                             retryable=True, errno=code, provider="baidu")

    # HTTP 层：4xx 基本都不该重试（429 除外）
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(exc.response, "status_code", None)
        retryable = status is None or status >= 500 or status == 429
        return ProviderError(f"{action}失败（HTTP {status}）: {message}",
                             retryable=retryable, errno=status, provider="baidu")

    if code is not None and code in _NON_RETRYABLE_CODES:
        return ProviderError(f"{action}失败: {message}",
                             retryable=False, errno=code, provider="baidu")

    # 其余按瞬时错误处理：宁可多试一次，也不要因为一次抖动放弃整个分卷
    return ProviderError(f"{action}失败: {message}",
                         retryable=True, errno=code, provider="baidu")


class BaiduProvider:
    name = "baidu"
    capabilities = Capabilities(
        can_list=True,
        can_delete=True,
        max_file_size=4 * 1024 ** 3,   # PCS 单次上传上限约 4 GB
        needs_app_dir=True,
    )
    default_root = "/apps/StreamPack"

    def __init__(self, client=None, remote_root: str = "/apps/StreamPack"):
        self._client = client
        self.default_root = remote_root

    # ---------- 内部 ----------

    def _ensure_client(self, interactive: bool = False):
        """
        拿到 BaiduClient。

        interactive=True 时才允许交互式输入 AppKey/SecretKey——首次运行就是这条路。
        以前这里写死 prompt_if_missing=False，导致首次上传直接抛"未找到凭证"，
        用户根本走不到输入提示。
        """
        if self._client is not None:
            return self._client

        from src.config.credentials import load_credentials
        from src.baidu.client import BaiduClient

        creds = load_credentials(prompt_if_missing=interactive)
        self._client = BaiduClient(creds.client_id, creds.client_secret,
                                   creds.token_file)
        return self._client

    @property
    def client(self):
        return self._ensure_client(interactive=False)

    def _call(self, action: str, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:                      # noqa: BLE001 —— 统一翻译
            raise _map_error(e, action) from e

    # ---------- StorageProvider ----------

    def authenticate(self, interactive: bool = True) -> None:
        client = self._call("加载凭证", lambda: self._ensure_client(interactive))
        try:
            client.oauth.get_valid_access_token(interactive=interactive)
        except RuntimeError as e:
            raise ProviderError(str(e), retryable=False, provider=self.name) from e
        except Exception as e:                      # noqa: BLE001
            raise _map_error(e, "鉴权") from e

    def ensure_dir(self, remote_dir: str) -> None:
        """
        百度没有"建目录"接口：/apps/{应用名} 由控制台决定，
        这里只能确认它可列——不可列说明路径不对。
        """
        remote_dir = normalize_remote_path(remote_dir)
        try:
            self._call("检查目录", self.client.list_files, remote_dir)
        except ProviderError as e:
            raise ProviderError(
                f"目标目录不可用: {remote_dir}（{e}）。"
                f"新版控制台的可写目录固定为 /apps/{{应用名}}",
                retryable=False, errno=e.errno, provider=self.name,
            ) from e

    def upload_file(self, remote_path: str, fileobj: BinaryIO,
                    size: Optional[int] = None) -> None:
        remote_path = normalize_remote_path(remote_path)
        fileobj.seek(0)
        self._call("上传", self.client.upload_file, remote_path, fileobj)

    def list_dir(self, remote_dir: str) -> List[RemoteFile]:
        remote_dir = normalize_remote_path(remote_dir)
        data = self._call("列目录", self.client.list_files, remote_dir)
        files = []
        for item in data.get("list", []) or []:
            name = item.get("server_filename")
            if not name:
                continue
            files.append(RemoteFile(
                path=f"{remote_dir.rstrip('/')}/{name}",
                size=int(item.get("size", 0)),
                mtime=item.get("server_mtime"),
                id=str(item.get("fs_id")) if item.get("fs_id") is not None else None,
            ))
        return sorted(files, key=lambda f: f.path)

    def delete_file(self, remote_path: str) -> None:
        remote_path = normalize_remote_path(remote_path)
        self._call("删除", self.client.delete_file, remote_path)
