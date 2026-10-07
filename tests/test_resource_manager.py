# tests/test_resource_manager.py
import pytest
from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager


def test_decide_buffer_medium_memory():
    settings = ResourceSettings(
        allow_use_memory=True,
        memory_limit_bytes=1 * 1024 * 1024,
        allow_use_disk=False,
    )
    rm = ResourceManager(settings)
    medium, path, cap = rm.decide_buffer_medium()
    assert medium == "memory"


def test_decide_buffer_medium_disk(tmp_path):
    settings = ResourceSettings(
        allow_use_memory=False,
        allow_use_disk=True,
        disk_limit_bytes=1 * 1024 * 1024 * 1024,
        preferred_temp_dirs=[str(tmp_path)],
    )
    rm = ResourceManager(settings)
    medium, path, cap = rm.decide_buffer_medium()
    assert medium == "disk"
    assert path == str(tmp_path)


def test_reject_both_disabled():
    settings = ResourceSettings(allow_use_memory=False, allow_use_disk=False)
    with pytest.raises(ValueError):
        ResourceManager(settings)