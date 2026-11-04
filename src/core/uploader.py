# src/core/uploader.py
import os
import sys
import time
from enum import Enum
from typing import BinaryIO, Generator, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

from src.logger import get_logger
from src.baidu.client import BaiduClient
from src.hardware.resource_manager import ResourceManager
from src.database.task_db import TaskDB

logger = get_logger(__name__)


class UploadResult(Enum):
    COMPLETED = 'completed'
    PARTIAL = 'partial'
    FAILED = 'failed'


class VolumeUploader:
    def __init__(self, baidu_client: BaiduClient,
                 resource_manager: ResourceManager,
                 db: Optional[TaskDB] = None,
                 task_id: Optional[str] = None):
        self.client = baidu_client
        self.resource_manager = resource_manager
        self.db = db
        self.task_id = task_id
        self.worker_count = resource_manager.decide_worker_count()
        self.remote_suffix = ".zip"
        logger.info(f"上传器初始化，并发线程数: {self.worker_count}")

    def _ensure_volume_record(self, task_id: str, part_number: int,
                              remote_path: str, temp_path: str) -> None:
        if not self.db or not task_id:
            return
        if self.db.get_volume_status(task_id, part_number) is None:
            self.db.add_volume(task_id, part_number, remote_path, temp_path)

    def _cleanup_local(self, volume_file: BinaryIO) -> None:
        file_path = volume_file.name
        try:
            volume_file.close()
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as e:
            logger.warning(f"删除临时文件失败: {file_path}, {e}")

    def _upload_with_retry(self, remote_path: str, volume_file: BinaryIO,
                           part_number: int) -> None:
        """
        上传单个分卷，失败按指数退避重试。

        网络抖动、限流这类瞬时错误占失败的大多数，重试一次往往就能过去；
        每次重试前必须 seek(0)，否则 requests 会从上次读到的位置继续。
        全部尝试仍失败时抛出最后一次异常，由调用方记录为 failed。
        """
        settings = self.resource_manager.settings
        attempts = max(1, int(settings.max_upload_retries))
        backoff = max(0.0, float(settings.retry_backoff_seconds))

        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                volume_file.seek(0)
                self.client.upload_file(remote_path, volume_file)
                if attempt > 1:
                    logger.info(f"[OK]   分卷 {part_number} 第 {attempt} 次尝试成功")
                return
            except Exception as e:
                last_error = e
                if attempt >= attempts:
                    break
                delay = backoff * (2 ** (attempt - 1))
                logger.warning(
                    f"[RETRY] 分卷 {part_number} 第 {attempt}/{attempts} 次失败: {e}；"
                    f"{delay:.1f}s 后重试"
                )
                if delay > 0:
                    time.sleep(delay)

        assert last_error is not None
        raise last_error

    def upload_volume(self, volume_file: BinaryIO, remote_dir: str,
                      base_name: str, part_number: int
                      ) -> Tuple[int, bool, Optional[str], bool]:
        """
        Returns: (part_number, success, error_message, skipped)
        """
        remote_path = (f"{remote_dir.rstrip('/')}/{base_name}"
                       f".part{part_number:04d}{self.remote_suffix}")
        file_path = volume_file.name

        if self.db and self.task_id:
            status = self.db.get_volume_status(self.task_id, part_number)
            if status == 'done':
                logger.info(f"[SKIP] 分卷 {part_number} 已完成，跳过")
                self._cleanup_local(volume_file)
                return (part_number, True, None, True)
            self.db.update_volume_status(self.task_id, part_number, 'uploading')

        try:
            size_mb = os.path.getsize(file_path) / 1024 / 1024
            logger.info(f"[UP]   分卷 {part_number} 上传中 (大小: {size_mb:.2f} MB)")

            self._upload_with_retry(remote_path, volume_file, part_number)

            if self.db and self.task_id:
                self.db.update_volume_status(self.task_id, part_number, 'done')
                completed = len(self.db.get_completed_part_numbers(self.task_id))
                self.db.update_task_progress(self.task_id, completed)

            logger.info(f"[OK]   分卷 {part_number} 上传成功")
            return (part_number, True, None, False)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[ERR]  分卷 {part_number} 上传失败: {error_msg}")
            if self.db and self.task_id:
                self.db.update_volume_status(self.task_id, part_number, 'failed')
            return (part_number, False, error_msg, False)

        finally:
            self._cleanup_local(volume_file)

    def upload_all(self, volume_generator: Generator[BinaryIO, None, None],
                   remote_dir: str, base_name: str,
                   task_id: Optional[str] = None,
                   backpressure: Optional[int] = None
                   ) -> Tuple[UploadResult, List[str], List[int], List[str]]:
        """
        Returns: (result, uploaded_paths, failed_parts, skipped_paths)
        """
        effective_task_id = task_id or self.task_id
        uploaded_paths: List[str] = []
        failed_parts: List[int] = []
        skipped_paths: List[str] = []
        part_number = 1
        futures_by_path = {}
        files_by_path = {}
        executor = ThreadPoolExecutor(max_workers=self.worker_count)

        try:
            for vol_file in volume_generator:
                remote_path = (f"{remote_dir.rstrip('/')}/{base_name}"
                               f".part{part_number:04d}{self.remote_suffix}")
                if self.db and effective_task_id:
                    self._ensure_volume_record(
                        effective_task_id, part_number, remote_path, vol_file.name
                    )

                if backpressure and backpressure > 0:
                    while sum(1 for f in futures_by_path.values() if not f.done()) >= backpressure:
                        wait(list(futures_by_path.values()),
                             return_when=FIRST_COMPLETED, timeout=0.5)

                future = executor.submit(
                    self.upload_volume, vol_file, remote_dir, base_name, part_number
                )
                futures_by_path[vol_file.name] = future
                files_by_path[vol_file.name] = vol_file
                part_number += 1

            total_volumes = part_number - 1
            if self.db and effective_task_id:
                completed = len(self.db.get_completed_part_numbers(effective_task_id))
                self.db.update_task_progress(effective_task_id, completed, total_volumes)
                logger.info(f"[STAT] 任务总卷数: {total_volumes}, 已完成: {completed}")

            for path, future in futures_by_path.items():
                try:
                    pn, ok, err, skipped = future.result()
                    full_path = (f"{remote_dir.rstrip('/')}/{base_name}"
                                 f".part{pn:04d}{self.remote_suffix}")
                    if ok:
                        if skipped:
                            skipped_paths.append(full_path)
                        else:
                            uploaded_paths.append(full_path)
                    else:
                        failed_parts.append(pn)
                except Exception as e:
                    logger.error(f"future 异常: {e}")

        except KeyboardInterrupt:
            # 直接 print + flush，绕过 logger 缓冲，让用户立即看到反馈
            sys.stderr.write("[STOP] 收到中断信号，正在取消未开始的任务...\n")
            sys.stderr.flush()
            executor.shutdown(wait=False, cancel_futures=True)
            for path, future in futures_by_path.items():
                if future.cancelled():
                    vol_file = files_by_path.get(path)
                    if vol_file is not None:
                        self._cleanup_local(vol_file)
                        logger.debug(f"已清理未上传的分卷: {path}")
            raise

        finally:
            executor.shutdown(wait=False)

        if not failed_parts:
            result = UploadResult.COMPLETED
        elif uploaded_paths or skipped_paths:
            result = UploadResult.PARTIAL
        else:
            result = UploadResult.FAILED

        logger.info(
            f"[DONE] 上传结束: {result.value}, "
            f"本次上传 {len(uploaded_paths)} 卷, "
            f"跳过 {len(skipped_paths)} 卷, "
            f"失败 {len(failed_parts)} 卷"
        )
        return result, uploaded_paths, failed_parts, skipped_paths