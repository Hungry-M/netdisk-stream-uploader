# 为 StreamPack 增加一个网盘后端

本文写给想通过 issue 或 pull request 帮忙接入其他网盘的人。目标是把"加一个后端"变成一件边界清楚、能独立验证的事。

## 先说清楚边界

存储后端只负责**把一段字节写进远端**，不关心压缩、分卷、续传、加密。这些都在 `src/core/` 里，对所有后端通用。

所以一个新后端要回答的问题只有四个：

1. 怎么鉴权？
2. 怎么把本地文件写上去？
3. 怎么列目录 / 删文件？
4. 哪些错误值得重试？

## 接口

```python
# src/storage/base.py
class StorageProvider(Protocol):
    name: str
    capabilities: Capabilities
    default_root: str

    def authenticate(self, interactive: bool = True) -> None: ...
    def ensure_dir(self, remote_dir: str) -> None: ...
    def upload_file(self, remote_path: str, fileobj: BinaryIO, size: int) -> None: ...
    def list_dir(self, remote_dir: str) -> List[RemoteFile]: ...
    def delete_file(self, remote_path: str) -> None: ...
```

只保留这五个方法是有意的：`BaiduClient` 里还有分片上传、动态域名等能力，但**调用方用不到**，所以不进接口。

### 路径

- 一律 `/a/b/c` 形式，provider 内部自己处理前缀（例如百度的可写目录固定在 `/apps/{应用名}`）
- 用 `normalize_remote_path()` 归一化，它会拒绝 `..`
- `default_root` 暴露给调用方作为默认目标目录

### 错误：`ProviderError.retryable`

```python
raise ProviderError("上传失败（HTTP 403）", retryable=False, errno=403, provider="mycloud")
```

- `retryable=True`：网络抖动、超时、5xx、限流 —— 上传层会按指数退避重试
- `retryable=False`：权限、路径不存在、参数错误、token 失效 —— 重试没有意义，立即失败

**这条最重要**。把所有异常都标成可重试，等于让用户对着 403 干等 6 秒；全都不可重试，则一次抖动就废掉一个分卷。

百度后端的映射可以参照 `src/storage/baidu.py` 里的 `_map_error()`。

### 能力声明

```python
capabilities = Capabilities(
    can_list=True,
    can_delete=True,
    max_file_size=4 * 1024 ** 3,
    needs_app_dir=False,
)
```

`max_file_size` 要如实填：分卷大小如果超过它，用户需要自己调小 `--volume-size`。

## 骨架

```python
# src/storage/mycloud.py
from src.storage.base import Capabilities, ProviderError, RemoteFile, normalize_remote_path


class MyCloudProvider:
    name = "mycloud"
    capabilities = Capabilities(max_file_size=2 * 1024 ** 3)
    default_root = "/"

    def __init__(self, token: str = "", remote_root: str = "/"):
        self.token = token
        self.default_root = remote_root

    def authenticate(self, interactive: bool = True) -> None:
        if self.token:
            return
        if not interactive:
            raise ProviderError("需要 token", retryable=False, provider=self.name)
        self.token = input("token: ").strip()

    def ensure_dir(self, remote_dir: str) -> None:
        remote_dir = normalize_remote_path(remote_dir)
        # 没有建目录接口就列一次，失败即路径不可用
        self.list_dir(remote_dir)

    def upload_file(self, remote_path, fileobj, size=None):
        remote_path = normalize_remote_path(remote_path)
        fileobj.seek(0)
        try:
            ...  # 调用 SDK 或 HTTP API
        except TimeoutError as e:
            raise ProviderError(f"上传超时: {e}", retryable=True, provider=self.name)

    def list_dir(self, remote_dir) -> list[RemoteFile]:
        ...

    def delete_file(self, remote_path) -> None:
        ...
```

注册到工厂（`src/storage/__init__.py`）：

```python
_REGISTRY = {
    "local": ("src.storage.local", "LocalDirProvider"),
    "baidu": ("src.storage.baidu", "BaiduProvider"),
    "mycloud": ("src.storage.mycloud", "MyCloudProvider"),
}
```

再把它加进 `src/config/settings.py` 的 `provider` 选项说明，`--provider mycloud` 就能用。

## 怎么测

**不要只写 mock**。仓库里有现成的两个参照：

- `LocalDirProvider`：真实落盘，能验证"写进去的字节对不对"
- `tests/conftest.py` 的 `RecordingProvider`：包一层 + 注入失败，用来验证重试策略

最小要求：

1. `upload_file` 写入后，`list_dir` 能读回正确大小
2. 路径归一化（含 `..` 被拒）
3. 至少一个 `retryable=True` 和一个 `retryable=False` 的错误映射
4. 没网时测试能跳过（用 `pytest.importorskip` 或环境变量）

参考 `tests/test_storage_baidu.py`（桩客户端，不联网）和 `tests/test_storage_local.py`。

## 提 issue 时请带上

- 网盘名字 + 官方 API 文档链接
- 鉴权方式（OAuth / token / 账号密码），以及是否需要备案或开发者审核
- **单文件上传上限**和是否支持分片
- 错误码表：哪些是限流、哪些是权限问题
- 你是否有可用的测试账号（有的话，PR 里可以跑真机验证；没有的话，只能靠桩）

用 `.github/ISSUE_TEMPLATE/provider-support.md` 模板会省很多来回。

## 提 PR 时请保持

- 只改 `src/storage/<name>.py` + 注册表 + 测试，**不要动 `src/core/`**
- 不把 token / 账号 / 私钥写进代码或测试
- 中文文案走 `_()`，否则切英文时会漏
- 跑一遍 `python -m pytest -q`，并在 PR 描述里贴结果
