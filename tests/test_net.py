# tests/test_net.py
"""代理相关：错误翻译成人话、--no-proxy 直连开关。"""
import requests

from src import net


def teardown_function():
    net.set_direct(False)


def test_proxy_error_gets_actionable_hint():
    exc = requests.exceptions.ProxyError("Unable to connect to proxy")
    hint = net.proxy_hint(exc)
    assert "代理" in hint
    assert "--no-proxy" in hint


def test_non_proxy_error_has_no_hint():
    assert net.proxy_hint(ValueError("boom")) == ""


def test_session_trusts_env_by_default():
    net.set_direct(False)
    session = net.new_session()
    try:
        assert session.trust_env is True
    finally:
        session.close()


def test_direct_mode_ignores_proxy_env():
    net.set_direct(True)
    session = net.new_session()
    try:
        assert session.trust_env is False, "直连模式不应读取代理环境变量"
    finally:
        session.close()


def test_baidu_mapping_marks_proxy_failure_non_retryable():
    from src.storage.baidu import _map_error

    exc = requests.exceptions.ProxyError("Unable to connect to proxy")
    err = _map_error(exc, "鉴权")

    assert err.retryable is False, "代理坏了重试没意义"
    assert "--no-proxy" in str(err)


def test_cli_flag_sets_direct_mode(monkeypatch):
    """--no-proxy 是全局开关，必须真的生效"""
    import main

    captured = {}
    monkeypatch.setattr("src.net.set_direct", lambda v: captured.setdefault("v", v))
    monkeypatch.setattr(main, "_run_cli", lambda remaining: 0)

    assert main.main(["--no-proxy", "list"]) == 0
    assert captured["v"] is True
