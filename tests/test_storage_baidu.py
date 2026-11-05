# tests/test_storage_baidu.py
"""BaiduProvider：适配与错误分级。用桩客户端，不联网。"""
import io
import types

import pytest
import requests

from src.storage.base import ProviderError, RemoteFile
from src.storage.baidu import BaiduProvider, _extract_code


class StubClient:
    """只记录调用、按需抛错的假客户端。"""

    def __init__(self, upload_error=None, list_data=None, list_error=None,
                 delete_error=None):
        self.calls = []
        self.upload_error = upload_error
        self.list_data = list_data if list_data is not None else {"errno": 0, "list": []}
        self.list_error = list_error
        self.delete_error = delete_error
        self.oauth = types.SimpleNamespace(
            get_valid_access_token=lambda interactive=True: "stub-token")

    def upload_file(self, remote_path, fileobj):
        self.calls.append(("upload", remote_path, fileobj.tell()))
        if self.upload_error:
            raise self.upload_error

    def list_files(self, remote_dir):
        self.calls.append(("list", remote_dir))
        if self.list_error:
            raise self.list_error
        return self.list_data

    def delete_file(self, remote_path):
        self.calls.append(("delete", remote_path))
        if self.delete_error:
            raise self.delete_error


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} Client Error", response=response)


def test_upload_normalizes_path_and_rewinds():
    client = StubClient()
    provider = BaiduProvider(client=client)

    fh = io.BytesIO(b"payload")
    fh.seek(5)
    provider.upload_file("backup/a.part0001.zip", fh, size=7)

    assert client.calls[0][0] == "upload"
    assert client.calls[0][1] == "/backup/a.part0001.zip"
    assert client.calls[0][2] == 0        # 上传前必须回到起点


def test_upload_403_is_not_retryable():
    provider = BaiduProvider(client=StubClient(upload_error=_http_error(403)))
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is False


def test_upload_500_is_retryable():
    provider = BaiduProvider(client=StubClient(upload_error=_http_error(503)))
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is True


def test_upload_timeout_is_retryable():
    provider = BaiduProvider(
        client=StubClient(upload_error=requests.exceptions.Timeout("slow")))
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is True


def test_baidu_error_code_31066_is_not_retryable():
    provider = BaiduProvider(
        client=StubClient(upload_error=Exception("百度 API 错误 (31066): 文件不存在")))
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is False
    assert exc.value.errno == 31066


def test_unknown_error_defaults_to_retryable():
    provider = BaiduProvider(
        client=StubClient(upload_error=Exception("某种未预期的问题")))
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is True


def test_list_dir_maps_to_remote_files():
    client = StubClient(list_data={"errno": 0, "list": [
        {"server_filename": "b.zip", "size": 20, "server_mtime": 100, "fs_id": 2},
        {"server_filename": "a.zip", "size": 10, "server_mtime": 50, "fs_id": 1},
        {"nosize": True},
    ]})
    provider = BaiduProvider(client=client)

    files = provider.list_dir("/apps/StreamPack")
    assert [f.path for f in files] == [
        "/apps/StreamPack/a.zip", "/apps/StreamPack/b.zip",
    ]
    assert all(isinstance(f, RemoteFile) for f in files)
    assert files[0].id == "1"


def test_ensure_dir_hints_at_app_directory():
    provider = BaiduProvider(
        client=StubClient(list_error=Exception("百度 API 错误 (2): 参数错误")))
    with pytest.raises(ProviderError) as exc:
        provider.ensure_dir("/apps/Wrong")
    assert exc.value.retryable is False
    assert "/apps/" in str(exc.value)


def test_authenticate_non_interactive_error_is_mapped():
    client = StubClient()
    client.oauth = types.SimpleNamespace(
        get_valid_access_token=lambda interactive=True: (_ for _ in ()).throw(
            RuntimeError("access_token 已失效且无法刷新，需要重新授权")))
    provider = BaiduProvider(client=client)

    with pytest.raises(ProviderError) as exc:
        provider.authenticate(interactive=False)
    assert exc.value.retryable is False


def test_delete_maps_errors():
    provider = BaiduProvider(
        client=StubClient(delete_error=Exception("百度 API 错误 (403): 无权限")))
    with pytest.raises(ProviderError) as exc:
        provider.delete_file("/apps/StreamPack/a.zip")
    assert exc.value.retryable is False


def test_extract_code():
    assert _extract_code("百度 API 错误 (31066): 文件不存在") == 31066
    assert _extract_code("no code here") is None
