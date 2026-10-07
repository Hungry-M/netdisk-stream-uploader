# src/config/hardware_profile.py
import os
import psutil
from typing import Dict, List, Optional

class HardwareProfile:
    """静态硬件信息快照"""
    
    @staticmethod
    def get_total_ram_bytes() -> int:
        """获取物理总内存大小（字节）"""
        return psutil.virtual_memory().total

    @staticmethod
    def get_available_ram_bytes() -> int:
        """获取当前可用内存大小（字节）"""
        return psutil.virtual_memory().available

    @staticmethod
    def get_cpu_count() -> int:
        """获取逻辑CPU核心数。获取失败时抛异常，让用户手动配置。"""
        count = os.cpu_count()
        if not count:
            raise RuntimeError(
                "无法获取 CPU 核心数。请在 settings.py 中显式设置 "
                "max_parallel_workers（>0 的整数）"
            )
        return count

    @staticmethod
    def get_disk_usage(path: str) -> Optional[int]:
        """
        获取指定路径所在磁盘的剩余可用空间（字节）
        如果路径无效返回 None
        """
        try:
            # 如果路径不存在则创建
            os.makedirs(path, exist_ok=True)
            usage = psutil.disk_usage(path)
            return usage.free
        except (OSError, FileNotFoundError, PermissionError):
            return None

    @staticmethod
    def get_all_disks_info(preferred_paths: List[str]) -> Dict[str, int]:
        """
        扫描多个候选路径，返回 {路径: 剩余字节数} 的字典
        仅返回有效且可写的路径
        """
        valid_disks = {}
        for p in preferred_paths:
            free = HardwareProfile.get_disk_usage(p)
            if free is not None and free > 0:
                valid_disks[p] = free
        return valid_disks