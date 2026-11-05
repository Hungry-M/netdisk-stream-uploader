# tests/test_storage_local.py
"""LocalDirProvider：接口契约的第二个实现，也是离线预演的基础。"""
import io

import pytest

from src.storage import create_provider, available_providers
from src.storage.base import ProviderError, RemoteFile, StorageProvider
from src.storage.local import LocalDirProvider


@pytest.fixture
def provider(tmp_path):
    p = LocalDirProvider(root=str(tmp_path / "cloud"))
    p.authenticate(interactive=False)
    return p


def test_satisfies_protocol(provider):
    assert isinstance(provider, StorageProvider)


def test_upload_and_read_back(provider, tmp_path):
    data = b"hello stream pack" * 1000
    provider.upload_file("/backup/a.part0001.zip", io.BytesIO(data), size=len(data))

    written = (tmp_path / "cloud" / "backup" / "a.part0001.zip").read_bytes()
    assert written == data


def test_upload_creates_nested_dirs(provider, tmp_path):
    provider.upload_file("/apps/StreamPack/deep/b.zip", io.BytesIO(b"x"), size=1)
    assert (tmp_path / "cloud" / "apps" / "StreamPack" / "deep" / "b.zip").is_file()


def test_upload_rejects_size_mismatch(provider):
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/backup/bad.zip", io.BytesIO(b"abc"), size=999)
    assert exc.value.retryable is True


def test_failed_upload_leaves_no_partial_file(provider, tmp_path):
    with pytest.raises(ProviderError):
        provider.upload_file("/backup/bad.zip", io.BytesIO(b"abc"), size=999)

    target = tmp_path / "cloud" / "backup" / "bad.zip"
    assert not target.exists()
    assert not target.with_suffix(".zip.part").exists()


def test_overwrite_existing(provider, tmp_path):
    provider.upload_file("/backup/a.zip", io.BytesIO(b"old"), size=3)
    provider.upload_file("/backup/a.zip", io.BytesIO(b"newdata"), size=7)
    assert (tmp_path / "cloud" / "backup" / "a.zip").read_bytes() == b"newdata"


def test_list_dir_returns_sorted_remote_files(provider):
    for name, size in (("b.zip", 5), ("a.zip", 3), ("c.zip", 7)):
        provider.upload_file(f"/backup/{name}", io.BytesIO(b"x" * size), size=size)
    provider.ensure_dir("/backup/sub")

    files = provider.list_dir("/backup")
    assert [f.path for f in files] == [
        "/backup/a.zip", "/backup/b.zip", "/backup/c.zip",
    ]
    assert all(isinstance(f, RemoteFile) for f in files)
    assert [f.size for f in files] == [3, 5, 7]


def test_list_missing_dir_raises(provider):
    with pytest.raises(ProviderError) as exc:
        provider.list_dir("/nope")
    assert exc.value.retryable is False


def test_delete_file(provider, tmp_path):
    provider.upload_file("/backup/a.zip", io.BytesIO(b"x"), size=1)
    provider.delete_file("/backup/a.zip")
    assert not (tmp_path / "cloud" / "backup" / "a.zip").exists()


def test_delete_missing_is_not_retryable(provider):
    with pytest.raises(ProviderError) as exc:
        provider.delete_file("/backup/ghost.zip")
    assert exc.value.retryable is False


def test_path_traversal_rejected(provider):
    with pytest.raises(ProviderError) as exc:
        provider.upload_file("/../escape.zip", io.BytesIO(b"x"), size=1)
    assert exc.value.retryable is False
    assert ".." in str(exc.value)


def test_provider_error_carries_name(provider):
    with pytest.raises(ProviderError) as exc:
        provider.list_dir("/nope")
    assert "local" in str(exc.value)


def test_factory_builds_local(tmp_path):
    provider = create_provider("local", root=str(tmp_path / "c"))
    assert isinstance(provider, LocalDirProvider)
    assert "local" in available_providers()


def test_factory_rejects_unknown():
    with pytest.raises(ProviderError) as exc:
        create_provider("dropbox")
    assert "未知的存储后端" in str(exc.value)
