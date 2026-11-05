# tests/test_i18n.py
"""多语言骨架：语言选择、缺目录回退、环境变量覆盖。"""
import os

import pytest

from src import i18n


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for var in ("STREAMPACK_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    yield
    i18n.set_language("zh")


def test_defaults_to_chinese():
    assert i18n.set_language("zh") == "zh"
    assert i18n._("上传成功") == "上传成功"


def test_missing_catalog_falls_back_to_source():
    """没有 en 目录时也不能炸，原样返回中文"""
    assert i18n.set_language("en") == "en"
    assert i18n._("上传成功") == "上传成功"


def test_unknown_language_falls_back_to_chinese():
    assert i18n.set_language("klingon") == "zh"


def test_auto_uses_environment(monkeypatch):
    monkeypatch.setenv("STREAMPACK_LANG", "en")
    assert i18n.set_language(None) == "en"

    monkeypatch.setenv("STREAMPACK_LANG", "zh")
    assert i18n.set_language(None) == "zh"


def test_explicit_argument_beats_environment(monkeypatch):
    monkeypatch.setenv("STREAMPACK_LANG", "en")
    assert i18n.set_language("zh") == "zh"


def test_current_language_tracks_state():
    i18n.set_language("en")
    assert i18n.current_language() == "en"
    i18n.set_language("zh")
    assert i18n.current_language() == "zh"


def test_ngettext_works_without_catalog():
    assert i18n.ngettext("卷", "卷", 1) == "卷"


def test_available_languages_includes_supported():
    langs = i18n.available_languages()
    assert "zh" in langs
    assert set(langs) <= set(i18n.SUPPORTED)


def test_translated_catalog_is_used_when_present(tmp_path, monkeypatch):
    """真的放一个 .mo 进去，确认 gettext 生效"""
    msgfmt = pytest.importorskip("msgfmt")  # 没有工具链就跳过

    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    (tmp_path / "en" / "LC_MESSAGES").mkdir(parents=True)

    po = tmp_path / "en.po"
    po.write_text(
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: en\\n"\n\n'
        'msgid "上传成功"\n'
        'msgstr "Upload complete"\n',
        encoding="utf-8",
    )
    msgfmt.make(str(po), str(tmp_path / "en" / "LC_MESSAGES" / "streampack.mo"))

    assert i18n.set_language("en") == "en"
    assert i18n._("上传成功") == "Upload complete"
