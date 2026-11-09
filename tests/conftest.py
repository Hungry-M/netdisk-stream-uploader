# tests/conftest.py
import os
import pytest
from pathlib import Path

from src.database.task_db import TaskDB
from src.storage.local import LocalDirProvider


@pytest.fixture(autouse=True)
def fixed_test_language():
    """
    每个用例开始前固定为中文。

    很多用例断言中文文案（"已中断"、"设置"…），而语言是进程级全局状态，
    前面某个用例切到 en 就会污染后面的用例 —— CI（en-US）上因此失败，
    本地却因为"恰好"还用着旧目录而通过。

    用 function scope 而不是 session scope：每条用例都从干净状态开始。
    需要切语言的用例（tests/test_i18n.py）在用例内部自己调 set_language。
    """
    from src.i18n import set_language

    set_language("zh")
    yield


@pytest.fixture(scope="session")
def qapp():
    """Qt 应用实例（offscreen）；没装 PySide6 时跳过相关用例。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


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


class RecordingProvider:
    """
    包一层真实的 LocalDirProvider，记录每次调用，并能按需注入失败。

    比 MagicMock 强的地方：文件真的写到了磁盘，所以"临时分卷有没有删干净"
    "写入的字节对不对"都能断言。
    """

    name = "recording"
    capabilities = LocalDirProvider.capabilities
    default_root = "/backup"

    def __init__(self, root):
        self.inner = LocalDirProvider(str(root))
        self.inner.authenticate(interactive=False)
        self.calls = []
        self.failures = []          # [{needle, exc, left}]

    def fail(self, needle: str, exc: Exception, times: int = 1):
        """让路径里含 needle 的上传失败 times 次（times<0 表示永远失败）"""
        self.failures.append({"needle": needle, "exc": exc, "left": times})
        return self

    def authenticate(self, interactive: bool = True) -> None:
        self.inner.authenticate(interactive)

    def ensure_dir(self, remote_dir: str) -> None:
        self.inner.ensure_dir(remote_dir)

    def upload_file(self, remote_path, fileobj, size=None):
        self.calls.append(("upload", remote_path, size))
        for rule in self.failures:
            if rule["left"] != 0 and rule["needle"] in remote_path:
                if rule["left"] > 0:
                    rule["left"] -= 1
                raise rule["exc"]
        self.inner.upload_file(remote_path, fileobj, size)

    def list_dir(self, remote_dir):
        return self.inner.list_dir(remote_dir)

    def delete_file(self, remote_path):
        self.calls.append(("delete", remote_path, None))
        self.inner.delete_file(remote_path)
