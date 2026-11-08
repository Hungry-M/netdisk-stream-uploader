# src/config/settings.py
import os
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class ResourceSettings:
    """
    硬件资源调度配置。
    所有变量均采用下划线命名，符合Python PEP8规范。
    """
    # ----- 内存 (RAM) 配置 -----
    # 是否允许使用内存作为缓冲区（True=优先使用，False=强制使用硬盘）
    allow_use_memory: bool = True
    
    # 允许使用的最大内存容量（字节）。默认 4 GB
    memory_limit_bytes: int = 4 * 1024 * 1024 * 1024  

    # ----- 硬盘 (Disk) 配置 -----
    # 是否允许使用硬盘作为缓冲区（当内存不足或不允许时自动启用）
    allow_use_disk: bool = True
    
    # 所有硬盘临时目录的总容量上限（字节）。默认 20 GB
    disk_limit_bytes: int = 20 * 1024 * 1024 * 1024

    # 临时目录优先级列表（按顺序使用）。
    # 留空则自动检测：Windows 下优先使用 D:/temp, E:/temp，最后回退到系统 %TEMP%
    # 注意：确保系统对这些路径有读写权限
    preferred_temp_dirs: List[str] = field(default_factory=lambda: [
        "D:/netdisk_temp",
        "E:/netdisk_temp",
        "C:/netdisk_temp"
    ])

    # ----- CPU 调度配置 -----
    # 允许程序使用的逻辑CPU核心列表（None 表示全部使用）
    # 例如 [0, 1, 2, 3] 表示只使用前4个核心
    cpu_affinity_cores: Optional[List[int]] = None
    
    # 并行工作的最大线程数（用于分片并发上传）。0 表示自动根据CPU核心数决定
    max_parallel_workers: int = 0  

    # ----- 上传背压配置 -----
    # 允许的"已生成但未上传成功"的分卷积压上限。
    # None 或 0 表示不启用（峰值空间可能接近整个压缩包）。
    # 若启用，峰值临时空间 ≈ max_pending_volumes × 分卷大小。
    max_pending_volumes: Optional[int] = None

    # 临时磁盘剩余空间的硬性下限（字节）。低于此值，压缩器会暂停。
    # 无论是否启用背压，这个安全阀始终生效。
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024  # 2GB

    # ----- 上传重试配置 -----
    # 单个分卷的最大尝试次数（含首次）。1 表示不重试。
    max_upload_retries: int = 3

    # 首次重试前的等待秒数，之后按 2 的幂递增（2s、4s、8s……）。
    # 设为 0 则立即重试，测试里常用。
    retry_backoff_seconds: float = 2.0

    # ----- 存储后端 -----
    # "baidu" 或 "local"。local 把分卷写到本地目录，用于离线预演/自测。
    provider: str = "baidu"

    # provider=local 时的落盘根目录
    local_rehearsal_dir: str = "./_rehearsal"

    # 默认的网盘目标目录。
    # 注意：可写目录由「应用名称」决定，固定是 /apps/<应用名>。
    # 用别人的程序、自己注册应用时，这里必须改成自己的应用名，否则会 404。
    remote_root: str = "/apps/StreamPack"

    # ----- 界面与语言 -----
    # "auto" 按系统区域判断；也可写 "zh" / "en"
    language: str = "auto"

    # 界面主题："auto" 跟随系统；也可写 "light" / "dark"
    theme: str = "auto"

    # ----- 通用配置 -----
    # 临时文件前缀（便于识别和清理）
    temp_file_prefix: str = "netdisk_upload_"