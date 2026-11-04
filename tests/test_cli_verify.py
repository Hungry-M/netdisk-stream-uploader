# tests/test_cli_verify.py
"""续传校验的行为测试：指纹比对、差异列出、逐卷比对。"""
import json

import pytest

from src.cli import _verify_source, _check_volume_against_record
from src.core.compressor import (
    entries_payload, fingerprint_of, VolumeInfo, VolumeMismatchError,
)
from src.database.task_db import TaskDB


def _create_task(db, source_dir, files, task_id="t1", with_fingerprint=True):
    entries = entries_payload(source_dir, files)
    db.create_task(
        task_id, source_dir, files, "/apps", "arch", 1024,
        fingerprint=fingerprint_of(entries) if with_fingerprint else None,
        entries_json=json.dumps(entries) if with_fingerprint else None,
    )
    return db.get_task(task_id)


def _vol(part=1, md5="aaa", size=100, offset=0):
    return VolumeInfo(part_number=part, temp_path="/tmp/x", md5=md5,
                      size=size, start_offset=offset)


# ---------- 指纹比对 ----------

def test_verify_passes_when_source_unchanged(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for i in range(3):
        f = src / f"f{i}.bin"
        f.write_bytes(b"x" * (100 + i))
        files.append(str(f))

    db = TaskDB(db_path=str(tmp_path / "t.db"))
    task = _create_task(db, str(src), files)

    # 不应抛异常
    _verify_source(task, db, "t1", verify="full")


def test_verify_aborts_when_file_changed(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for i in range(3):
        f = src / f"f{i}.bin"
        f.write_bytes(b"x" * 100)
        files.append(str(f))

    db = TaskDB(db_path=str(tmp_path / "t.db"))
    task = _create_task(db, str(src), files)

    (src / "f1.bin").write_bytes(b"y" * 200)   # 改了大小

    with pytest.raises(SystemExit) as exc:
        _verify_source(task, db, "t1", verify="full")
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "f1.bin" in err
    assert "大小" in err


def test_verify_lists_added_and_removed(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(2):
        (src / f"f{i}.bin").write_bytes(b"x" * 100)

    db = TaskDB(db_path=str(tmp_path / "t.db"))
    # 传目录，才能枚举出"新增"的文件
    task = _create_task(db, str(src), [str(src)])

    (src / "f0.bin").unlink()
    (src / "new.bin").write_bytes(b"z" * 10)

    with pytest.raises(SystemExit):
        _verify_source(task, db, "t1", verify="full")

    err = capsys.readouterr().err
    assert "new.bin" in err
    assert "f0.bin" in err


def test_verify_off_skips_check(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.bin"
    f.write_bytes(b"x" * 10)
    files = [str(f)]

    db = TaskDB(db_path=str(tmp_path / "t.db"))
    task = _create_task(db, str(src), files)

    f.write_bytes(b"y" * 999)          # 变了，但 verify=off

    _verify_source(task, db, "t1", verify="off")   # 不应抛异常


def test_verify_backfills_missing_fingerprint(tmp_path, capsys):
    """老任务没有指纹：补记基线，本次放行，且库里被写上"""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.bin"
    f.write_bytes(b"x" * 10)
    files = [str(f)]

    db = TaskDB(db_path=str(tmp_path / "t.db"))
    task = _create_task(db, str(src), files, with_fingerprint=False)
    assert task["fingerprint"] is None

    _verify_source(task, db, "t1", verify="full")

    assert db.get_task("t1")["fingerprint"] is not None
    assert "补记" in capsys.readouterr().out


# ---------- 逐卷比对 ----------

def test_volume_check_passes_for_matching_record():
    prev = {"status": "done", "md5": "aaa", "size_bytes": 100, "start_offset": 0}
    _check_volume_against_record(prev, _vol())     # 不抛异常


def test_volume_check_raises_on_md5_mismatch():
    prev = {"status": "done", "md5": "bbb", "size_bytes": 100, "start_offset": 0}
    with pytest.raises(VolumeMismatchError) as exc:
        _check_volume_against_record(prev, _vol())
    assert "第 1 卷" in str(exc.value)


def test_volume_check_raises_on_size_mismatch():
    prev = {"status": "done", "md5": "aaa", "size_bytes": 999, "start_offset": 0}
    with pytest.raises(VolumeMismatchError):
        _check_volume_against_record(prev, _vol())


def test_volume_check_ignores_pending_and_missing():
    # 未完成的分卷本来就该被覆盖
    _check_volume_against_record(
        {"status": "pending", "md5": "zzz", "size_bytes": 1, "start_offset": 0}, _vol())
    # 没有记录
    _check_volume_against_record(None, _vol())


def test_volume_check_skips_legacy_record_without_md5():
    """本功能上线前的记录没有 md5，不能拿它判失败"""
    _check_volume_against_record(
        {"status": "done", "md5": None, "size_bytes": None, "start_offset": None}, _vol())
