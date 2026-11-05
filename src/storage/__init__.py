# src/storage/__init__.py
"""
存储后端注册表。

用法：
    from src.storage import create_provider
    provider = create_provider("local", root="./_rehearsal")
    provider.authenticate(interactive=False)
    provider.upload_file("/backup/a.part0001.zip", fh, size=1024)
"""
from typing import Any

from src.storage.base import (
    Capabilities, ProviderError, RemoteFile, StorageProvider, normalize_remote_path,
)

__all__ = [
    "Capabilities",
    "ProviderError",
    "RemoteFile",
    "StorageProvider",
    "normalize_remote_path",
    "create_provider",
    "available_providers",
]

# 延迟导入：避免 import src.storage 就拉起 requests / 凭据读取
_REGISTRY = {
    "local": ("src.storage.local", "LocalDirProvider"),
    "baidu": ("src.storage.baidu", "BaiduProvider"),
}


def available_providers() -> list:
    return sorted(_REGISTRY)


def create_provider(name: str, **kwargs: Any) -> StorageProvider:
    """
    按名字构造后端。

    Args:
        name: "local" / "baidu"
        **kwargs: 透传给具体实现（local 需要 root，baidu 需要凭证）
    """
    key = (name or "baidu").strip().lower()
    if key not in _REGISTRY:
        raise ProviderError(
            f"未知的存储后端: {name!r}，可选: {', '.join(available_providers())}",
            retryable=False,
        )

    module_name, class_name = _REGISTRY[key]
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ImportError as e:
        raise ProviderError(
            f"后端 {key} 尚不可用: {e}", retryable=False
        ) from e

    provider_cls = getattr(module, class_name)
    return provider_cls(**kwargs)
