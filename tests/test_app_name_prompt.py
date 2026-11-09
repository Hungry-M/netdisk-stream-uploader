# tests/test_app_name_prompt.py
"""首次运行问应用名：提示不留方括号，回车沿用当前值。"""
import pytest

from src.config import credentials


def _patch_prompt(monkeypatch, answer):
    calls = {"prompt": 0, "saved": []}

    def fake_prompt(text, default=None, show_default=True, **kwargs):
        calls["prompt"] += 1
        calls["text"] = text
        calls["default"] = default
        calls["show_default"] = show_default
        return answer

    monkeypatch.setattr("click.prompt", fake_prompt)
    monkeypatch.setattr(
        "src.config.user_config.build_settings",
        lambda: type("S", (), {"app_name": "StreamPack"})(),
    )
    monkeypatch.setattr(
        "src.config.user_config.set_value",
        lambda name, value: calls["saved"].append((name, value)),
    )
    return calls


def test_prompt_has_no_visible_default(monkeypatch):
    calls = _patch_prompt(monkeypatch, "")

    credentials._ask_app_name()

    assert calls["text"] == "应用名"
    assert calls["default"] == ""
    assert calls["show_default"] is False, "不要在提示里显示 [StreamPack]"


def test_empty_answer_keeps_current(monkeypatch):
    calls = _patch_prompt(monkeypatch, "")

    credentials._ask_app_name()

    assert calls["saved"] == [], "回车应当什么都不改"


def test_custom_name_is_saved(monkeypatch):
    calls = _patch_prompt(monkeypatch, "MyBackup")

    credentials._ask_app_name()

    assert calls["saved"] == [("app_name", "MyBackup")]
