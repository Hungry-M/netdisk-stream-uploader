# tests/conftest.py
import os
import pytest
from pathlib import Path

from src.database.task_db import TaskDB


@pytest.fixture
def temp_db(tmp_path):
    """使用临时文件路径的 TaskDB 实例"""
    db_path = tmp_path / "test_tasks.db"
    return TaskDB(db_path=str(db_path))


@pytest.fixture
def sample_files(tmp_path):
    """创建 3 个 50KB 随机文件（不可压缩，便于切多卷）"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    files = []
    for i in range(3):
        f = source_dir / f"file_{i}.bin"
        f.write_bytes(os.urandom(50 * 1024))
        files.append(str(f))
    return str(source_dir), files