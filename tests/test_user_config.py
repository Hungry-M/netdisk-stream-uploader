# tests/test_user_config.py
"""用户配置文件：解析、优先级、校验。"""
import json

import pytest

from src.config import user_config
from src.config.settings import ResourceSettings


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """把配置文件指到临时目录，避免碰真实用户配置"""
    p = tmp_path / "config.json"
    monkeypatch.setenv("STREAMPACK_CONFIG", str(p))
    # 清掉可能存在的 STREAMPACK_* 环境变量
    for name in user_config.SPECS:
        monkeypatch.delenv(user_config.ENV_PREFIX + name.upper(), raising=False)
    return p


def test_defaults_when_no_file(cfg_file):
    assert not cfg_file.exists()
    values = user_config.effective_values()
    assert values["max_upload_retries"] == ResourceSettings().max_upload_retries
    assert user_config.sources()["max_upload_retries"] == "default"


def test_set_writes_file_and_takes_effect(cfg_file):
    user_config.set_value("max_upload_retries", "7")

    assert cfg_file.exists()
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["max_upload_retries"] == 7
    assert user_config.effective_values()["max_upload_retries"] == 7
    assert user_config.sources()["max_upload_retries"] == "file"
    assert user_config.build_settings().max_upload_retries == 7


def test_env_overrides_file(cfg_file, monkeypatch):
    user_config.set_value("max_upload_retries", "7")
    monkeypatch.setenv("STREAMPACK_MAX_UPLOAD_RETRIES", "9")

    assert user_config.effective_values()["max_upload_retries"] == 9
    assert user_config.sources()["max_upload_retries"] == "env"


def test_unset_restores_default(cfg_file):
    user_config.set_value("max_upload_retries", "7")
    user_config.unset_value("max_upload_retries")

    default = ResourceSettings().max_upload_retries
    assert user_config.effective_values()["max_upload_retries"] == default
    assert user_config.sources()["max_upload_retries"] == "default"


def test_list_field_round_trip(cfg_file):
    user_config.set_value("preferred_temp_dirs", "D:/t1, E:/t2")
    assert user_config.effective_values()["preferred_temp_dirs"] == ["D:/t1", "E:/t2"]


def test_optional_none(cfg_file):
    user_config.set_value("max_pending_volumes", "none")
    assert user_config.effective_values()["max_pending_volumes"] is None

    user_config.set_value("max_pending_volumes", "20")
    assert user_config.effective_values()["max_pending_volumes"] == 20


def test_bool_parsing(cfg_file):
    user_config.set_value("allow_use_memory", "false")
    assert user_config.effective_values()["allow_use_memory"] is False

    with pytest.raises(ValueError):
        user_config.set_value("allow_use_memory", "maybe")


def test_invalid_value_does_not_corrupt_file(cfg_file):
    user_config.set_value("max_upload_retries", "7")
    before = cfg_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        user_config.set_value("max_upload_retries", "abc")

    assert cfg_file.read_text(encoding="utf-8") == before


def test_rejects_turning_off_both_buffers(cfg_file):
    user_config.set_value("allow_use_disk", "false")
    with pytest.raises(ValueError):
        user_config.set_value("allow_use_memory", "false")


def test_unknown_keys_in_file_are_ignored(cfg_file):
    cfg_file.write_text(json.dumps({"not_a_field": 1, "max_upload_retries": 5}),
                        encoding="utf-8")
    values = user_config.effective_values()
    assert values["max_upload_retries"] == 5
    assert "not_a_field" not in values


def test_corrupt_file_falls_back_to_defaults(cfg_file):
    cfg_file.write_text("{ this is not json", encoding="utf-8")
    values = user_config.effective_values()
    assert values["max_upload_retries"] == ResourceSettings().max_upload_retries


def test_bad_env_value_is_ignored(cfg_file, monkeypatch):
    monkeypatch.setenv("STREAMPACK_MAX_UPLOAD_RETRIES", "not-a-number")
    values = user_config.effective_values()
    assert values["max_upload_retries"] == ResourceSettings().max_upload_retries


def test_specs_cover_all_settings_fields():
    from dataclasses import fields
    assert set(user_config.SPECS) == {f.name for f in fields(ResourceSettings)}
