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
    files: tuple = ()     # self-contained 模式下这一卷包含的文件（相对路径）


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


def _deterministic_crypto_random(task_id: str):
    """
    给 stream-zip 的加密用的"随机"字节源。

    stream-zip 默认用 secrets.token_bytes 生成每条目的盐，这会让同样输入
    每次压出不同字节，续传校验必然失败。这里改成由 task_id + 调用序号派生，
    保证同一任务重压时盐一致。

    调用顺序与条目顺序绑定，因此必须保证条目枚举是确定性的（由
    iter_entries 保证）。
    """
    state = {"n": 0}

    def getter(num_bytes: int) -> bytes:
        seed = f"streampack:{task_id}:{state['n']}".encode('utf-8')
        state["n"] += 1
        out = bytearray()
        block = 0
        while len(out) < num_bytes:
            out += hashlib.sha256(seed + block.to_bytes(4, 'big')).digest()
            block += 1
        return bytes(out[:num_bytes])

    return getter


def password_tag(task_id: str, password: str) -> str:
    """
    密码校验值：只用于确认"续传时输入的密码和创建时一致"，本身不能还原密码。

    用 PBKDF2 而不是裸 SHA-256，是为了让拿到 tasks.db 的人也没法快速穷举。
    """
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), task_id.encode("utf-8"), 100_000
    ).hex()


# ---------- 分卷布局 ----------

LAYOUT_CONTINUOUS = 'continuous'          # 一个 ZIP 切成 N 段（默认）
LAYOUT_SELF_CONTAINED = 'self-contained'  # 每卷一个独立 ZIP
LAYOUTS = (LAYOUT_CONTINUOUS, LAYOUT_SELF_CONTAINED)

PACK_BY_SIZE = 'size'        # 只按大小装箱
PACK_BY_FOLDER = 'folder'    # 尽量让同一个顶层文件夹落在少数卷里
PACK_BYS = (PACK_BY_SIZE, PACK_BY_FOLDER)

# 装箱时留的余量：deflate 最坏会略微膨胀（约 0.03%），
# 按未压缩大小装到 99.5% 可以保证压缩后仍不超过 volume_size。
_PACK_SAFETY = 0.995


class FileTooLargeForVolume(Exception):
    """
    self-contained 模式下，单个文件装不进一卷。

    这不是 bug：独立可解压意味着一个文件必须完整落在某一卷里，
    无法像连续流那样把它拦腰切断。
    """

    def __init__(self, arcname: str, size: int, limit: int):
        super().__init__(
            f"{arcname}（{size / 1024 / 1024:.1f} MB）超过分卷大小"
            f"（{limit / 1024 / 1024:.1f} MB）"
        )
        self.arcname = arcname
        self.size = size
        self.limit = limit


def effective_volume_bytes(max_bytes: int) -> int:
    """扣掉安全余量后，真正可用于装未压缩数据的上限。"""
    return max(1, int(max_bytes * _PACK_SAFETY))


def oversized_entries(entries: List[Entry], max_bytes: int) -> List[Entry]:
    """
    预检：哪些文件单独一个就超过分卷大小。

    self-contained 模式下这种文件根本装不进任何一卷，
    与其压缩到一半再抛错，不如在调用 provider 之前就列出来。
    """
    limit = effective_volume_bytes(max_bytes)
    return [e for e in entries if e.size > limit]


def pack_volumes(entries: List[Entry], max_bytes: int,
                 pack_by: str = PACK_BY_SIZE) -> List[List[Entry]]:
    """
    按文件顺序装箱：累加到放不下就另起一卷。

    pack_by='folder' 时按顶层文件夹分组，先尽量整个文件夹放一卷，
    放不下再在文件夹内部按大小切分——注意**不改变卷大小上限**，
    只是让"解压某一卷能拿到一个完整文件夹"的概率更高。

    保持原始顺序（不重排），这样同样的输入总得到同样的分卷划分。
    """
    if max_bytes <= 0:
        raise ValueError("分卷大小必须大于 0")
    if pack_by not in PACK_BYS:
        raise ValueError(f"未知的装箱方式: {pack_by!r}（可用 {', '.join(PACK_BYS)}）")

    effective = effective_volume_bytes(max_bytes)
    for entry in entries:
        if entry.size > effective:
            raise FileTooLargeForVolume(entry.arcname, entry.size, max_bytes)

    if pack_by == PACK_BY_SIZE:
        return _pack_by_size(entries, effective)

    buckets: List[List[Entry]] = []
    for _folder, group in _group_by_top_folder(entries):
        buckets.extend(_pack_by_size(group, effective))
    return buckets


def _group_by_top_folder(entries: List[Entry]):
    """按 arcname 的第一段分组，保持首次出现顺序。"""
    order: List[str] = []
    groups = {}
    for entry in entries:
        folder = entry.arcname.split('/', 1)[0] if '/' in entry.arcname else ''
        if folder not in groups:
            groups[folder] = []
            order.append(folder)
        groups[folder].append(entry)
    for folder in order:
        yield folder, groups[folder]


def _pack_by_size(entries: List[Entry], effective: int) -> List[List[Entry]]:
    buckets: List[List[Entry]] = []
    current: List[Entry] = []
    current_bytes = 0

    for entry in entries:
        if current and current_bytes + entry.size > effective:
            buckets.append(current)
            current = []
            current_bytes = 0

        current.append(entry)
        current_bytes += entry.size

    if current:
        buckets.append(current)
    return buckets


def compress_and_split(
    source_dir: str,
    file_list: List[str],
    volume_size_bytes: int,
    resource_manager: ResourceManager,
    fixed_modified_at: Optional[datetime] = None,
    on_volume_complete: Optional[Callable[[VolumeInfo], None]] = None,
    manifest: Optional[dict] = None,
    password: Optional[str] = None,
    task_id: Optional[str] = None,
    layout: str = LAYOUT_CONTINUOUS,
    pack_by: str = PACK_BY_SIZE,
) -> Generator[BinaryIO, None, None]:
    """
    流式压缩并按 layout 切分，逐卷 yield（调用方负责 close + remove）。

    - continuous：一个 ZIP 字节流切成 N 段，任意一卷都不是完整归档，必须拼接；
    - self-contained：每卷一个独立 ZIP，可单独解压，但单文件不能大于一卷。
    """
    if layout == LAYOUT_SELF_CONTAINED:
        gen = _self_contained_volumes(
            source_dir, file_list, volume_size_bytes, resource_manager,
            fixed_modified_at, on_volume_complete, manifest, password, task_id,
            pack_by,
        )
    elif layout == LAYOUT_CONTINUOUS:
        gen = _continuous_volumes(
            source_dir, file_list, volume_size_bytes, resource_manager,
            fixed_modified_at, on_volume_complete, manifest, password, task_id,
        )
    else:
        raise ValueError(f"未知的分卷布局: {layout!r}（可用 {', '.join(LAYOUTS)}）")

    yield from gen


def _file_chunks(path: Path):
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            yield chunk


def _zip_stream(entries, password: Optional[str], task_id: Optional[str]):
    if password is not None:
        if not task_id:
            raise ValueError("加密归档必须提供 task_id（盐由它派生）")
        return stream_zip(
            entries,
            password=password,
            get_crypto_random=_deterministic_crypto_random(task_id),
        )
    return stream_zip(entries)


def _self_contained_volumes(
    source_dir: str,
    file_list: List[str],
    volume_size_bytes: int,
    resource_manager: ResourceManager,
    fixed_modified_at: Optional[datetime],
    on_volume_complete: Optional[Callable[[VolumeInfo], None]],
    manifest: Optional[dict],
    password: Optional[str],
    task_id: Optional[str],
    pack_by: str = PACK_BY_SIZE,
) -> Generator[BinaryIO, None, None]:
    entries = list(iter_entries(source_dir, file_list))
    if not entries:
        logger.warning("文件列表为空，不生成任何分卷")
        return

    if password is not None and not task_id:
        raise ValueError("加密归档必须提供 task_id（盐由它派生）")

    buckets = pack_volumes(entries, volume_size_bytes, pack_by=pack_by)
    temp_dir = resource_manager.decide_volume_temp_dir()
    os.makedirs(temp_dir, exist_ok=True)
    logger.info(
        f"[ZIP] self-contained：{len(entries)} 个文件 -> {len(buckets)} 卷，"
        f"每卷上限 {volume_size_bytes / 1024 / 1024:.0f} MB"
    )

    modified_at = fixed_modified_at or datetime.now()
    stream_offset = 0

    for index, bucket in enumerate(buckets, 1):
        zip_entries = []
        if manifest is not None:
            volume_manifest = dict(manifest)
            volume_manifest['layout'] = LAYOUT_SELF_CONTAINED
            volume_manifest['volume'] = {
                'index': index,
                'total': len(buckets),
                'files': [[e.arcname, e.size, e.mtime_ns] for e in bucket],
            }
            zip_entries.append(_manifest_entry(volume_manifest, modified_at))

        for entry in bucket:
            zip_entries.append(
                (entry.arcname, modified_at, 0o644, ZIP_64, _file_chunks(entry.path))
            )

        temp_file = tempfile.NamedTemporaryFile(
            dir=temp_dir,
            prefix=resource_manager.settings.temp_file_prefix,
            suffix='.zip',
            delete=False,
        )
        hasher = hashlib.md5()
        size = 0
        try:
            for chunk in _zip_stream(zip_entries, password, task_id):
                temp_file.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
            temp_file.flush()
            temp_file.seek(0)
        except Exception:
            temp_file.close()
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass
            raise

        info = VolumeInfo(
            part_number=index,
            temp_path=temp_file.name,
            md5=hasher.hexdigest(),
            size=size,
            start_offset=stream_offset,
            files=tuple(e.arcname for e in bucket),
        )
        stream_offset += size
        logger.info(
            f"[ZIP]  分卷 {index}/{len(buckets)}（{len(bucket)} 个文件）, "
            f"大小: {size / 1024 / 1024:.2f} MB, md5: {info.md5[:8]}…"
        )
        if on_volume_complete is not None:
            on_volume_complete(info)
        yield temp_file


def _continuous_volumes(
    source_dir: str,
    file_list: List[str],
    volume_size_bytes: int,
    resource_manager: ResourceManager,
    fixed_modified_at: Optional[datetime],
    on_volume_complete: Optional[Callable[[VolumeInfo], None]],
    manifest: Optional[dict],
    password: Optional[str],
    task_id: Optional[str],
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

    if password is not None:
        if not task_id:
            raise ValueError("加密归档必须提供 task_id（盐由它派生）")
        logger.info("[ZIP] 启用 AES-256 加密")
        zip_stream = stream_zip(
            entries,
            password=password,
            get_crypto_random=_deterministic_crypto_random(task_id),
        )
    else:
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
