# src/hardware/resource_manager.py
import os
import glob
import logging
from typing import Optional, Tuple, List

from src.config.settings import ResourceSettings
from src.config.hardware_profile import HardwareProfile

logger = logging.getLogger(__name__)


class ResourceManager:
    """
    资源调度管理器。
    负责检查内存/硬盘容量，并决定缓冲区落在哪里，以及 CPU 核心数。
    """

    def __init__(self, settings: ResourceSettings):
        self.settings = settings
        self._validate_settings()

    def _validate_settings(self):
        if not self.settings.allow_use_memory and not self.settings.allow_use_disk:
            raise ValueError("至少允许使用内存（Memory）或硬盘（Disk）中的一种！")

    # --- 1. 缓冲区介质决策 ---
    def decide_buffer_medium(self) -> Tuple[str, Optional[str], int]:
        """
        根据配置和当前资源，决定本次使用的缓冲区介质。
        Returns: (介质类型, 路径或None, 可用容量)
        """
        if self.settings.allow_use_memory:
            avail_ram = HardwareProfile.get_available_ram_bytes()
            if avail_ram >= self.settings.memory_limit_bytes:
                logger.info(
                    f"资源调度：选择内存，可用 "
                    f"{avail_ram / 1024 / 1024 / 1024:.2f} GB"
                )
                return ('memory', None, self.settings.memory_limit_bytes)
            else:
                logger.warning(
                    f"内存不足 ({avail_ram / 1024 / 1024:.0f} MB < "
                    f"{self.settings.memory_limit_bytes / 1024 / 1024:.0f} MB)，"
                    f"回退尝试硬盘"
                )

        if self.settings.allow_use_disk:
            disk_info = HardwareProfile.get_all_disks_info(self.settings.preferred_temp_dirs)

            if not disk_info:
                fallback = os.path.join(
                    os.environ.get('TEMP', '/tmp'), 'netdisk_temp'
                )
                logger.warning(f"预置路径均无效，回退到系统临时目录：{fallback}")
                free_space = HardwareProfile.get_disk_usage(fallback)
                if free_space and free_space > 0:
                    disk_info[fallback] = free_space

            sorted_disks = sorted(disk_info.items(), key=lambda x: x[1], reverse=True)

            for path, free_bytes in sorted_disks:
                usable_bytes = min(self.settings.disk_limit_bytes, free_bytes)
                if usable_bytes >= 10 * 1024 * 1024:
                    logger.info(
                        f"资源调度：选择硬盘 {path}，可用 "
                        f"{usable_bytes / 1024 / 1024 / 1024:.2f} GB"
                    )
                    os.makedirs(path, exist_ok=True)
                    return ('disk', path, usable_bytes)

        raise RuntimeError("系统可用内存和硬盘空间均不足（<10MB），无法继续上传！")

    # --- 2. 分卷临时目录决策 ---
    def decide_volume_temp_dir(self) -> str:
        """
        选择分卷临时目录。永远返回磁盘路径（即使内存充裕）。
        会检查 min_free_disk_bytes 安全阀。
        """
        if not self.settings.allow_use_disk:
            raise RuntimeError("分卷必须落盘，但 allow_use_disk=False")

        disk_info = HardwareProfile.get_all_disks_info(self.settings.preferred_temp_dirs)

        candidates = {
            path: free for path, free in disk_info.items()
            if free > self.settings.min_free_disk_bytes
        }

        if not candidates:
            raise RuntimeError(
                f"所有候选临时目录剩余空间均低于 "
                f"{self.settings.min_free_disk_bytes / 1024 / 1024 / 1024:.2f} GB，"
                f"无法安全存放分卷"
            )

        best_path = max(candidates.items(), key=lambda x: x[1])[0]
        logger.info(f"分卷临时目录: {best_path}")
        return best_path

    # --- 3. 孤儿分卷清理 ---
    def cleanup_orphan_volumes(self) -> int:
        """
        清理所有残留的分卷临时文件。
        假定：同一时刻只有一个任务在跑（CLI 单任务模型）。
        Returns: 清理的文件数量
        """
        count = 0
        pattern_name = f"{self.settings.temp_file_prefix}*.zip"
        for temp_dir in self.settings.preferred_temp_dirs:
            if not os.path.isdir(temp_dir):
                continue
            for path in glob.glob(os.path.join(temp_dir, pattern_name)):
                try:
                    os.remove(path)
                    logger.info(f"清理残留分卷: {os.path.basename(path)}")
                    count += 1
                except OSError as e:
                    logger.warning(f"清理失败: {path}, {e}")
        return count

    # --- 4. CPU 核心调度决策 ---
    def decide_cpu_affinity(self) -> List[int]:
        """
        返回本任务应该使用的CPU核心列表。
        """
        total_cores = HardwareProfile.get_cpu_count()

        if self.settings.cpu_affinity_cores is None:
            return list(range(total_cores))

        valid_cores = [c for c in self.settings.cpu_affinity_cores if 0 <= c < total_cores]
        if not valid_cores:
            logger.warning(
                f"指定的CPU核心 {self.settings.cpu_affinity_cores} 无效，回退到全部核心"
            )
            return list(range(total_cores))

        logger.info(f"CPU 调度：仅使用核心 {valid_cores}")
        return valid_cores

    def decide_worker_count(self, default_to_cores: bool = True) -> int:
        """
        决定并发工作线程数。
        如果 max_parallel_workers == 0，则根据 CPU 核心数自动计算。
        """
        if self.settings.max_parallel_workers > 0:
            return self.settings.max_parallel_workers

        cores = len(self.decide_cpu_affinity())
        return max(1, cores - 1)