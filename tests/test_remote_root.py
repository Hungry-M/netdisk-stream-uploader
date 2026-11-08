# tests/test_remote_root.py
"""网盘目录由应用名推导：/apps/<应用名>[/子目录]，不再让人手填整条路径。"""
import json

import pytest

from src.config.settings import ResourceSettings, build_remote_root


def test_default_is_apps_stream_pack():
    assert build_remote_root() == "/apps/StreamPack"


def test_subdir_is_appended():
    assert build_remote_root("MyApp", "daily") == "/apps/MyApp/daily"


def test_slashes_are_stripped_not_escaped():
    assert build_remote_root("/MyApp/", "/daily/") == "/apps/MyApp/daily"
    assert build_remote_root("MyApp//", "//") == "/apps/MyApp"


def test_blank_app_name_falls_back():
    assert build_remote_root("", "") == "/apps/StreamPack"
    assert build_remote_root("   ", "x") == "/apps/StreamPack/x"


def test_settings_property_derives_from_fields():
    s = ResourceSettings(app_name="Demo", remote_subdir="v1")
    assert s.remote_root == "/apps/Demo/v1"


def test_app_name_rejects_slash_in_config(tmp_path, monkeypatch):
    from src.config.user_config import set_value

    path = tmp_path / "config.json"
    monkeypatch.setenv("STREAMPACK_CONFIG", str(path))

    with pytest.raises(ValueError):
        set_value("app_name", "bad/name")


def test_legacy_remote_root_is_migrated(tmp_path, monkeypatch):
    """老配置里的 remote_root 要迁移成 app_name + remote_subdir"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"remote_root": "/apps/Old/backups"}),
                    encoding="utf-8")
    monkeypatch.setenv("STREAMPACK_CONFIG", str(path))

    from src.config.user_config import build_settings, load_config

    cfg = load_config()
    assert "remote_root" not in cfg
    assert cfg["app_name"] == "Old"
    assert cfg["remote_subdir"] == "backups"
    assert build_settings().remote_root == "/apps/Old/backups"


def test_legacy_root_outside_apps_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"remote_root": "/mydata"}), encoding="utf-8")
    monkeypatch.setenv("STREAMPACK_CONFIG", str(path))

    from src.config.user_config import build_settings

    assert build_settings().remote_root == "/apps/StreamPack"
