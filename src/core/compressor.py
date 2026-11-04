# src/core/compressor.py
import os
import tempfile
from src.logger import get_logger
from pathlib import Path
from datetime import datetime
from typing import List, Generator, BinaryIO, Optional
from stream_zip import stream_zip, ZIP_64

from src.hardware.resource_manager import ResourceManager

logger = get_logger(__name__)


def _walk_files(root_dir: str, file_list: List[str],
                fixed_modified_at: Optional[datetime] = None) -> Generator:
    """
    遍历文件列表，生成 stream-zip 所需的条目生成器。
    支持单个文件、文件夹递归、相对路径、绝对路径。
    """
    root_path = Path(root_dir)

    def _make_entry(file_path: Path):
        try:
            arcname = file_path.relative_to(root_path.parent)
        except ValueError:
            arcname = file_path

        modified_at = fixed_modified_at or datetime.now()
        perms = 0o644

        def content_gen(path=file_path):
            with open(path, 'rb') as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return str(arcname).replace('\\', '/'), modified_at, perms, ZIP_64, content_gen()

    for file_path_str in file_list:
        file_path = Path(file_path_str)
        if not file_path.is_absolute():
            file_path = root_path / file_path

        if not file_path.exists():
            logger.warning(f"路径不存在，跳过: {file_path}")
            continue

        if file_path.is_dir():
            files_in_dir = sorted(p for p in file_path.rglob('*') if p.is_file())
            if not files_in_dir:
                logger.warning(f"目录为空，跳过: {file_path}")
                continue
            for f in files_in_dir:
                yield _make_entry(f)
        else:
            yield _make_entry(file_path)


def compress_and_split(
    source_dir: str,
    file_list: List[str],
    volume_size_bytes: int,
    resource_manager: ResourceManager,
    fixed_modified_at: Optional[datetime] = None
) -> Generator[BinaryIO, None, None]:
    """
    流式压缩，切分为不超过 volume_size_bytes 的分卷。
    ⚠️ 每个分卷被 yield 后，调用方负责 close + remove。
    """
    if not file_list:
        logger.warning("文件列表为空，不生成任何分卷")
        return

    # 分卷必须落盘，位置由 preferred_temp_dirs 决定
    temp_dir = resource_manager.decide_volume_temp_dir()
    os.makedirs(temp_dir, exist_ok=True)

    entries = _walk_files(source_dir, file_list, fixed_modified_at)
    zip_stream = stream_zip(entries)

    current_volume_bytes = 0
    current_temp_file = None
    volume_index = 0

    for chunk in zip_stream:
        if (current_temp_file is not None
                and current_volume_bytes + len(chunk) > volume_size_bytes):
            current_temp_file.flush()
            current_temp_file.seek(0)
            volume_index += 1
            logger.info(
                f"[ZIP]  分卷 {volume_index} 生成完毕, "
                f"大小: {current_volume_bytes / 1024 / 1024:.2f} MB"
            )
            yield current_temp_file
            current_temp_file = None
            current_volume_bytes = 0

        if current_temp_file is None:
            current_temp_file = tempfile.NamedTemporaryFile(
                dir=temp_dir,
                prefix=resource_manager.settings.temp_file_prefix,
                suffix='.zip',
                delete=False
            )

        current_temp_file.write(chunk)
        current_volume_bytes += len(chunk)

    if current_temp_file is not None:
        current_temp_file.flush()
        current_temp_file.seek(0)
        volume_index += 1
        logger.info(
            f"[ZIP]  最后一卷 {volume_index} 生成完毕, "
            f"大小: {current_volume_bytes / 1024 / 1024:.2f} MB"
        )
        yield current_temp_file
    else:
        logger.warning("所有文件均不存在，生成空分卷")
        empty_temp = tempfile.NamedTemporaryFile(
            dir=temp_dir,
            prefix=resource_manager.settings.temp_file_prefix,
            suffix='.zip',
            delete=False
        )
        empty_temp.write(b'PK\x05\x06' + b'\x00' * 18)
        empty_temp.flush()
        empty_temp.seek(0)
        yield empty_temp