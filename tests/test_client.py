# tests/test_client.py
"""客户端端点与错误处理（不联网，用假 session）。"""
from unittest.mock import MagicMock

import pytest

from src.baidu.client import BaiduClient, XPAN_API_BASE


def _client_with_response(payload):
    client = BaiduClient("id", "secret", token_file="ignored.json")
    client._get_access_token = lambda: "fake-token"          # 不碰真实令牌

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None

    session = MagicMock()
    session.request.return_value = resp
    client.session = session
    return client, session


def test_list_files_uses_xpan_endpoint():
    """老的 PCS 端点对 list 直接返回 404，必须走 xpan"""
    client, session = _client_with_response({"errno": 0, "list": []})
    client.list_files("/apps/StreamPack")

    args, kwargs = session.request.call_args
    assert XPAN_API_BASE in args[1]
    assert "/pcs/" not in args[1]
    assert kwargs["params"]["method"] == "list"
    assert kwargs["params"]["dir"] == "/apps/StreamPack"


def test_delete_file_uses_filemanager_with_body():
    """filelist 必须走请求体；放 query 里接口回 errno 2"""
    client, session = _client_with_response({"errno": 0, "info": []})
    client.delete_file("/apps/StreamPack/a.part0001.zip")

    args, kwargs = session.request.call_args
    assert XPAN_API_BASE in args[1]
    assert kwargs["params"]["method"] == "filemanager"
    assert kwargs["params"]["opera"] == "delete"
    assert "filelist" not in kwargs["params"]
    assert "a.part0001.zip" in kwargs["data"]["filelist"]


def test_delete_reports_per_file_failure():
    client, _ = _client_with_response(
        {"errno": 0, "info": [{"errno": 31066, "path": "/apps/x.zip"}]})
    with pytest.raises(Exception) as exc:
        client.delete_file("/apps/x.zip")
    assert "/apps/x.zip" in str(exc.value)


def test_xpan_errno_is_raised():
    client, _ = _client_with_response({"errno": 112, "errmsg": "目录不存在"})
    with pytest.raises(Exception) as exc:
        client.list_files("/apps/nope")
    assert "112" in str(exc.value)
    assert "目录不存在" in str(exc.value)


def test_pcs_error_code_is_raised():
    client, _ = _client_with_response({"error_code": 31066, "error_msg": "文件不存在"})
    with pytest.raises(Exception) as exc:
        client.list_files("/apps")
    assert "31066" in str(exc.value)


def test_success_payload_passes_through():
    client, _ = _client_with_response({"errno": 0, "list": [{"fs_id": 1}]})
    assert client.list_files("/apps")["list"] == [{"fs_id": 1}]
