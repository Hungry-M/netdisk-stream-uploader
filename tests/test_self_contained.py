# tests/test_self_contained.py
"""自包含分卷：装箱、每卷独立可解压、超限文件报错、确定性。"""
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config.settings import ResourceSettings
from src.core.compressor import (
    LAYOUT_CONTINUOUS, LAYOUT_SELF_CONTAINED, Entry, FileTooLargeForVolume,
    compress_and_split, pack_volumes,
)
from src.hardware.resource_manager import ResourceManager

FIXED = datetime(2024, 1, 1, tzinfo=timezone.utc)
MB = 1024 * 1024


def entry(name: str, size: int) -> Entry:
    return Entry(arcname=name, path=Path(name), size=size, mtime_ns=0)


def test_pack_keeps_order_and_groups():
    buckets = pack_volumes([entry("a", 3), entry("b", 3), entry("c", 3)], 6)
    # 3 + 3 = 6 超过 6 * 0.995，所以每个都得单独一卷
    assert [[e.arcname for e in b] for b in buckets] == [["a"], ["b"], ["c"]]


def test_pack_fills_a_volume():
    # 上限 6，留 0.5% 余量后是 5：2+2=4 放得下，再加 2 就超了
    buckets = pack_volumes([entry("a", 2), entry("b", 2), entry("c", 2)], 6)
    assert [[e.arcname for e in b] for b in buckets] == [["a", "b"], ["c"]]


def test_pack_rejects_oversized_file():
    with pytest.raises(FileTooLargeForVolume) as excinfo:
        pack_volumes([entry("huge", 10)], 6)
    assert excinfo.value.arcname == "huge"
    assert excinfo.value.limit == 6


def make_source(root: Path, count: int = 3, size: int = MB) -> list:
    root.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(count):
        p = root / f"f{i}.bin"
        p.write_bytes(os.urandom(size))
        names.append(p.name)
    return names


def collect(src: Path, names, tmp_path: Path, layout, volume_size: int) -> list:
    rm = ResourceManager(ResourceSettings(preferred_temp_dirs=[str(tmp_path / "tmp")]))
    volumes = []
    for vol in compress_and_split(
        str(src), names, volume_size, rm,
        fixed_modified_at=FIXED, layout=layout,
        manifest={"format": 2, "tool": "test"},
    ):
        volumes.append(vol.read())
        vol.close()
        os.unlink(vol.name)
    return volumes


def test_each_volume_is_a_valid_zip(tmp_path):
    src = tmp_path / "src"
    names = make_source(src, count=3, size=MB)
    # 3 MB 卷装 3 个 1 MB 文件：留 0.5% 余量后只放得下 2 个
    volumes = collect(src, names, tmp_path, LAYOUT_SELF_CONTAINED, 3 * MB)

    assert len(volumes) == 2
    seen = []
    for data in volumes:
        path = tmp_path / f"v{len(seen)}.zip"
        path.write_bytes(data)
        with zipfile.ZipFile(path) as zf:
            assert zf.testzip() is None
            names_in_zip = [n for n in zf.namelist() if n != "__streampack__.json"]
            assert names_in_zip, "这一卷应该至少含一个文件"
            seen.extend(names_in_zip)
    assert sorted(seen) == ["src/f0.bin", "src/f1.bin", "src/f2.bin"]


def test_self_contained_volume_has_volume_manifest(tmp_path):
    src = tmp_path / "src"
    names = make_source(src, count=3, size=MB)
    volumes = collect(src, names, tmp_path, LAYOUT_SELF_CONTAINED, 3 * MB)

    path = tmp_path / "v.zip"
    path.write_bytes(volumes[0])
    with zipfile.ZipFile(path) as zf:
        manifest = zf.read("__streampack__.json").decode("utf-8").replace(" ", "")

    assert '"layout":"self-contained"' in manifest
    assert '"total":2' in manifest
    assert '"index":1' in manifest


def test_continuous_volumes_are_not_individually_readable(tmp_path):
    """对照：默认布局单卷打不开，这正是要解决的问题"""
    src = tmp_path / "src"
    names = make_source(src, count=2, size=MB)
    volumes = collect(src, names, tmp_path, LAYOUT_CONTINUOUS, MB)

    path = tmp_path / "first.zip"
    path.write_bytes(volumes[0])
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(path)


def test_self_contained_is_deterministic(tmp_path):
    """同样的源文件压两次，字节必须完全一致（续传校验依赖这点）"""
    src = tmp_path / "src"
    names = make_source(src, count=3, size=MB)

    first = collect(src, names, tmp_path, LAYOUT_SELF_CONTAINED, 3 * MB)
    second = collect(src, names, tmp_path, LAYOUT_SELF_CONTAINED, 3 * MB)
    assert first == second


def test_oversized_file_raises_with_limit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "big.bin").write_bytes(b"x" * (3 * MB))
    rm = ResourceManager(ResourceSettings(preferred_temp_dirs=[str(tmp_path / "tmp")]))

    gen = compress_and_split(
        str(src), ["big.bin"], MB, rm, fixed_modified_at=FIXED,
        layout=LAYOUT_SELF_CONTAINED,
    )
    with pytest.raises(FileTooLargeForVolume):
        for vol in gen:
            vol.close()


def test_unknown_layout_rejected(tmp_path):
    rm = ResourceManager(ResourceSettings(preferred_temp_dirs=[str(tmp_path / "tmp")]))
    gen = compress_and_split(str(tmp_path), ["x"], 1024, rm, layout="nope")
    with pytest.raises(ValueError):
        list(gen)


def test_encrypted_self_contained_volumes_are_valid_zips(tmp_path):
    """加密卷也要能独立打开（用标准 zip 库应该报"需要密码"，而不是格式错误）"""
    src = tmp_path / "src"
    names = make_source(src, count=2, size=MB)
    rm = ResourceManager(ResourceSettings(preferred_temp_dirs=[str(tmp_path / "tmp")]))

    volumes = []
    for vol in compress_and_split(
        str(src), names, 3 * MB, rm, fixed_modified_at=FIXED,
        layout=LAYOUT_SELF_CONTAINED, password="pw", task_id="task-1",
    ):
        volumes.append(vol.read())
        vol.close()
        os.unlink(vol.name)

    path = tmp_path / "v.zip"
    path.write_bytes(volumes[0])
    with zipfile.ZipFile(path) as zf:                  # 能打开说明结构完整
        assert zf.namelist()


def test_pack_by_folder_keeps_folders_whole():
    """优先整文件夹：能放下就整组进同一卷，放不下才在组内切"""
    entries = [
        entry("photos/a.jpg", 2),
        entry("photos/b.jpg", 2),
        entry("docs/c.pdf", 2),
        entry("docs/d.pdf", 2),
    ]
    by_size = pack_volumes(entries, 9, pack_by="size")
    by_folder = pack_volumes(entries, 9, pack_by="folder")

    # 混装时按大小装满：4 + 4 = 8 ≤ 8.9（留余量），四个文件只占一卷
    assert len(by_size) == 1
    # 按文件夹分组后，photos 和 docs 各自成卷
    assert [[e.arcname for e in b] for b in by_folder] == [
        ["photos/a.jpg", "photos/b.jpg"],
        ["docs/c.pdf", "docs/d.pdf"],
    ]


def test_pack_by_folder_splits_oversized_folder():
    """一个文件夹本身就超过一卷时，仍然按大小在组内切分（不改变上限）"""
    entries = [entry("big/a", 4), entry("big/b", 4), entry("big/c", 4)]
    buckets = pack_volumes(entries, 9, pack_by="folder")
    assert all(sum(e.size for e in b) <= 9 * 0.995 for b in buckets)
    assert len(buckets) == 2


def test_pack_by_rejects_unknown_value():
    with pytest.raises(ValueError):
        pack_volumes([entry("a", 1)], 10, pack_by="whatever")


def test_oversized_entries_reports_before_writing(tmp_path):
    from src.core.compressor import oversized_entries

    entries = [entry("ok", 5), entry("big", 20)]
    found = oversized_entries(entries, 10)
    assert [e.arcname for e in found] == ["big"]
