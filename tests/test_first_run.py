# tests/test_first_run.py
"""首次运行：没凭证时应该提示输入，而不是抛 traceback。"""
from types import SimpleNamespace

import pytest

from src.storage.baidu import BaiduProvider


def _patch_client(monkeypatch, calls):
    class FakeOAuth:
        def get_valid_access_token(self, interactive=True):
            calls["interactive"] = interactive

    class FakeClient:
        def __init__(self, client_id, client_secret, token_file=None):
            calls["client_id"] = client_id
            self.oauth = FakeOAuth()

    def fake_load(prompt_if_missing=True):
        calls["prompt"] = prompt_if_missing
        return SimpleNamespace(client_id="key", client_secret="secret",
                               token_file=None)

    monkeypatch.setattr("src.config.credentials.load_credentials", fake_load)
    monkeypatch.setattr("src.baidu.client.BaiduClient", FakeClient)


def test_authenticate_allows_prompt_when_interactive(monkeypatch):
    calls = {}
    _patch_client(monkeypatch, calls)

    BaiduProvider().authenticate(interactive=True)

    assert calls["prompt"] is True, "交互模式下必须允许提示输入 AppKey"
    assert calls["interactive"] is True
    assert calls["client_id"] == "key"


def test_authenticate_does_not_prompt_when_non_interactive(monkeypatch):
    calls = {}
    _patch_client(monkeypatch, calls)

    BaiduProvider().authenticate(interactive=False)

    assert calls["prompt"] is False
    assert calls["interactive"] is False


def test_missing_credentials_prints_one_line(tmp_path, monkeypatch):
    """CLI 首次运行不能把 traceback 甩给用户"""
    from click.testing import CliRunner

    import src.cli as cli_module

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.bin").write_bytes(b"x" * 1024)

    monkeypatch.setenv("STREAMPACK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STREAMPACK_CONFIG", str(tmp_path / "home" / "config.json"))
    monkeypatch.delenv("BAIDU_CLIENT_ID", raising=False)
    monkeypatch.delenv("BAIDU_CLIENT_SECRET", raising=False)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, [
        "upload", "-s", str(src_dir), "-f", "a.bin", "-n", "t",
        "--volume-size", "1048576", "--provider", "baidu",
    ])

    assert result.exit_code == 1
    assert "[ERR]" in result.output
    assert "Traceback" not in result.output
