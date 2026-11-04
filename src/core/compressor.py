# src/core/compressor.py
import os
import json
import hashlib
import tempfile
from dataclasses import dataclass
from src.logger import get_logger
from pathlib import Path
from datetime import datetime
from typing import List, Generator, BinaryIO, Optional, Iterator, Callable
from itertools import chain
from stream_zip import stream_zip, ZIP_64, ZIP_32

from src.hardware.resource_manager import ResourceManager

logger = get_logger(__name__)

# 归档自描述条目：放在 ZIP 的第一个条目，解开就知道这批分卷是什么。
MANIFEST_NAME = '__streampack__.json'


@dataclass(frozen=True)
class Entry:
    """ZIP 里的一个条目，同时是续传指纹的输入。"""
    arcname: str          # ZIP 内的路径，已统一为正斜杠
    path: Path            # 磁盘上的真实路径
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class VolumeInfo:
    """一卷写完后可落库的元数据。"""
    part_number: int
    temp_path: str
    md5: str
    size: int
    start_offset: int     # 该卷在整条 ZIP 字节流中的起始偏移


class VolumeMismatchError(Exception):
    """续传时重新压缩出的分卷与数据库记录不符 —— 源文件在中断后被改动过。"""


def iter_entries(root_dir: str, file_list: List[str],
                 warn_missing: bool = True) -> Iterator[Entry]:
    """
    按压缩顺序枚举要打包的文件。

    这是**唯一**的枚举入口：压缩和指纹都必须走它，否则两边顺序一差，
    续传校验就永远对不上。
    """
    root_path = Path(root_dir)

    def _entry(file_path: Path) -> Entry:
        try:
            arcname = file_path.relative_to(root_path.parent)
        except ValueError:
            arcname = file_path
        st = file_path.stat()
        return Entry(
            arcname=str(arcname).replace('\\', '/'),
            path=file_path,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
        )

    for file_path_str in file_list:
        file_path = Path(file_path_str)
        if not file_path.is_absolute():
            file_path = root_path / file_path

        if not file_path.exists():
            if warn_missing:
                logger.warning(f"路径不存在，跳过: {file_path}")
            continue

        if file_path.is_dir():
            files_in_dir = sorted(p for p in file_path.rglob('*') if p.is_file())
            if not files_in_dir:
                if warn_missing:
                    logger.warning(f"目录为空，跳过: {file_path}")
                continue
            for f in files_in_dir:
                yield _entry(f)
        else:
            yield _entry(file_path)


def entries_payload(source_dir: str, file_list: List[str]) -> List[list]:
    """
    源文件清单的纯数据形式：[[arcname, size, mtime_ns], ...]。

    既是指纹的输入，也是落库后做差异对比的依据。
    """
    return [
        [e.arcname, e.size, e.mtime_ns]
        for e in iter_entries(source_dir, file_list, warn_missing=False)
    ]


def fingerprint_of(payload: List[list]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def compute_fingerprint(source_dir: str, file_list: List[str]) -> str:
    """
    源文件的"清单指纹"：路径 + 大小 + mtime_ns，按压缩顺序拼接后取 SHA-256。

    续传时先比它：不一致说明源已经变了，重新压缩必然产生不同的字节流，
    已上传的分卷全部失效。
    """
    return fingerprint_of(entries_payload(source_dir, file_list))


def diff_entries(old: List[list], new: List[list]) -> dict:
    """
    对比两份清单，返回 {added, removed, changed}。

    changed 里的每项是 (arcname, 'size'|'mtime', 旧值, 新值)。
    """
    old_map = {item[0]: item for item in old}
    new_map = {item[0]: item for item in new}

    added = [new_map[name] for name in new_map.keys() - old_map.keys()]
    removed = [old_map[name] for name in old_map.keys() - new_map.keys()]

    changed = []
    for name in old_map.keys() & new_map.keys():
        old_item, new_item = old_map[name], new_map[name]
        if old_item[1] != new_item[1]:
            changed.append((name, 'size', old_item[1], new_item[1]))
        elif old_item[2] != new_item[2]:
            changed.append((name, 'mtime', old_item[2], new_item[2]))

    return {
        'added': sorted(added, key=lambda x: x[0]),
        'removed': sorted(removed, key=lambda x: x[0]),
        'changed': sorted(changed, key=lambda x: x[0]),
    }


def _walk_files(root_dir: str, file_list: List[str],
                modified_at: datetime) -> Generator:
    """把 Entry 转成 stream-zip 需要的元组序列。"""
    perms = 0o644

    def content_gen(path: Path):
        with open(path, 'rb') as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    for entry in iter_entries(root_dir, file_list):
        yield entry.arcname, modified_at, perms, ZIP_64, content_gen(entry.path)


def _manifest_entry(manifest: dict, modified_at: datetime) -> tuple:
    """
    把 manifest 变成一个 ZIP 条目。

    sort_keys + 固定分隔符 → 同样的输入必得同样的字节，续传才能逐字节复现。
    """
    data = json.dumps(
        manifest, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')
    return MANIFEST_NAME, modified_at, 0o644, ZIP_32, (data,)


def compress_and_split(
    source_dir: str,
    file_list: List[str],
    volume_size_bytes: int,
    resource_manager: ResourceManager,
    fixed_modified_at: Optional[datetime] = None,
    on_volume_complete: Optional[Callable[[VolumeInfo], None]] = None,
    manifest: Optional[dict] = None,
) -> Generator[BinaryIO, None, None]:
    """
    流式压缩，切分为不超过 volume_size_bytes 的分卷。

    每卷写完、yield 之前，会以 VolumeInfo 回调一次（含 MD5、大小、起始偏移），
    调用方借此把元数据落库，供续传校验。

    manifest 非空时会作为整个归档的**第一个条目**写入（`__streampack__.json`）。
    是否传它取决于任务的 format_version：老任务不能中途加上，否则字节流全变。

    ⚠️ 每个分卷被 yield 后，调用方负责 close + remove。
    """
    if not file_list:
        logger.warning("文件列表为空，不生成任何分卷")
        return

    # 分卷必须落盘，位置由 preferred_temp_dirs 决定
    temp_dir = resource_manager.decide_volume_temp_dir()
    os.makedirs(temp_dir, exist_ok=True)

    modified_at = fixed_modified_at or datetime.now()
    entries = _walk_files(source_dir, file_list, modified_at)
    if manifest is not None:
        entries = chain([_manifest_entry(manifest, modified_at)], entries)

    zip_stream = stream_zip(entries)

    def _new_temp_file() -> BinaryIO:
        return tempfile.NamedTemporaryFile(
            dir=temp_dir,
            prefix=resource_manager.settings.temp_file_prefix,
            suffix='.zip',
            delete=False
        )

    current_volume_bytes = 0
    current_temp_file: Optional[BinaryIO] = None
    volume_index = 0
    stream_offset = 0      # 整条流已写出的字节数
    volume_start = 0       # 当前卷在流中的起始偏移
    hasher = hashlib.md5()

    def _finish_volume() -> VolumeInfo:
        current_temp_file.flush()
        current_temp_file.seek(0)
        info = VolumeInfo(
            part_number=volume_index,
            temp_path=current_temp_file.name,
            md5=hasher.hexdigest(),
            size=current_volume_bytes,
            start_offset=volume_start,
        )
        if on_volume_complete is not None:
            on_volume_complete(info)
        return info

    for chunk in zip_stream:
        if (current_temp_file is not None
                and current_volume_bytes + len(chunk) > volume_size_bytes):
            volume_index += 1
            info = _finish_volume()
            logger.info(
                f"[ZIP]  分卷 {info.part_number} 生成完毕, "
                f"大小: {info.size / 1024 / 1024:.2f} MB, "
                f"md5: {info.md5[:8]}…"
            )
            yield current_temp_file
            current_temp_file = None
            current_volume_bytes = 0
            volume_start = stream_offset
            hasher = hashlib.md5()

        if current_temp_file is None:
            current_temp_file = _new_temp_file()

        current_temp_file.write(chunk)
        hasher.update(chunk)
        current_volume_bytes += len(chunk)
        stream_offset += len(chunk)

    if current_temp_file is not None:
        volume_index += 1
        info = _finish_volume()
        logger.info(
            f"[ZIP]  最后一卷 {info.part_number} 生成完毕, "
            f"大小: {info.size / 1024 / 1024:.2f} MB, "
            f"md5: {info.md5[:8]}…"
        )
        yield current_temp_file
    else:
        logger.warning("所有文件均不存在，生成空分卷")
        empty_temp = _new_temp_file()
        empty_bytes = b'PK\x05\x06' + b'\x00' * 18
        empty_temp.write(empty_bytes)
        empty_temp.flush()
        empty_temp.seek(0)
        if on_volume_complete is not None:
            on_volume_complete(VolumeInfo(
                part_number=1,
                temp_path=empty_temp.name,
                md5=hashlib.md5(empty_bytes).hexdigest(),
                size=len(empty_bytes),
                start_offset=0,
            ))
        yield empty_temp
