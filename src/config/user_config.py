# src/config/user_config.py
"""
用户配置文件：`~/.netdisk_uploader/config.json`

优先级（高 → 低）：
    环境变量 STREAMPACK_<字段名大写>
    配置文件 config.json
    src/config/settings.py 里的代码默认值

配置文件只保存用户显式改过的项，未出现的字段一律用代码默认值，
这样以后默认值调整能自动生效。
"""
import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.logger import get_logger
from src.config.settings import ResourceSettings

logger = get_logger(__name__)

ENV_PREFIX = "STREAMPACK_"
CONFIG_ENV = "STREAMPACK_CONFIG"      # 覆盖配置文件路径（测试用）


def config_path() -> Path:
    """配置文件位置；`STREAMPACK_CONFIG` 可覆盖。"""
    override = os.environ.get(CONFIG_ENV)
    if override:
        p = Path(override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    from src.paths import data_file

    return data_file("config.json")


# ---------- 值解析 ----------

def _parse_bool(raw: str) -> bool:
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"无法解析为布尔值: {raw!r}（可用 true/false）")


def _parse_int(raw: str) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"无法解析为整数: {raw!r}")


def _parse_float(raw: str) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"无法解析为数字: {raw!r}")


def _parse_str(raw: str) -> str:
    return str(raw)


def _parse_app_name(raw: str) -> str:
    """应用名：不能带斜杠，也不能为空——它会被拼进 /apps/<名字>。"""
    value = str(raw).strip().strip("/\\").strip()
    if not value:
        raise ValueError("应用名不能为空")
    if "/" in value or "\\" in value:
        raise ValueError(f"应用名不能包含斜杠: {raw!r}")
    return value


def _parse_list(raw: str) -> List[str]:
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"列表不能为空: {raw!r}")
    return items


def _parse_optional_int(raw: str) -> Optional[int]:
    v = str(raw).strip().lower()
    if v in ("", "none", "null", "-"):
        return None
    return _parse_int(raw)


def _parse_optional_int_list(raw: str) -> Optional[List[int]]:
    v = str(raw).strip().lower()
    if v in ("", "none", "null", "-"):
        return None
    return [_parse_int(x) for x in str(raw).split(",") if x.strip()]


# 字段名 -> (类型说明, 解析器, 一句话说明)
SPECS: Dict[str, Tuple[str, Any, str]] = {
    "allow_use_memory": ("bool", _parse_bool, "是否允许把内存作为缓冲候选"),
    "memory_limit_bytes": ("int", _parse_int, "内存缓冲上限（字节）"),
    "allow_use_disk": ("bool", _parse_bool, "是否允许把硬盘作为缓冲"),
    "disk_limit_bytes": ("int", _parse_int, "硬盘缓冲总上限（字节）"),
    "preferred_temp_dirs": ("list", _parse_list, "分卷临时目录，逗号分隔，按顺序尝试"),
    "cpu_affinity_cores": ("int-list?", _parse_optional_int_list, "限定使用的 CPU 核心，逗号分隔；none=全部"),
    "max_parallel_workers": ("int", _parse_int, "并发上传线程数；0=按 CPU 核数自动"),
    "max_pending_volumes": ("int?", _parse_optional_int, "背压上限；none=不启用"),
    "min_free_disk_bytes": ("int", _parse_int, "磁盘剩余空间硬性下限（字节）"),
    "max_upload_retries": ("int", _parse_int, "单个分卷最大尝试次数（含首次）"),
    "retry_backoff_seconds": ("float", _parse_float, "首次重试等待秒数，之后按 2 的幂递增"),
    "provider": ("str", _parse_str, "存储后端：baidu 或 local"),
    "local_rehearsal_dir": ("str", _parse_str, "provider=local 时的落盘根目录"),
    "remote_subdir": ("str", _parse_str, "应用目录下的子目录，留空表示直接用应用根目录"),
    "app_name": ("str", _parse_app_name, "百度应用名称，决定可写目录 /apps/<应用名>"),
    "language": ("str", _parse_str, "界面语言：auto / zh / en"),
    "theme": ("str", _parse_str, "界面主题：auto / light / dark"),
    "temp_file_prefix": ("str", _parse_str, "临时分卷文件名前缀"),
}

assert set(SPECS) == {f.name for f in fields(ResourceSettings)}, \
    "SPECS 与 ResourceSettings 字段不一致"


# ---------- 读写 ----------

def load_config() -> Dict[str, Any]:
    """读配置文件；不存在或损坏时返回空字典（不抛异常）。"""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"配置文件无法解析，按默认值处理: {e}")
        return {}
    if not isinstance(data, dict):
        logger.warning("配置文件内容不是对象，按默认值处理")
        return {}

    # 旧版本用的是自由填写的 remote_root；现在由 app_name + remote_subdir 推导，
    # 这里把老配置迁移过来，避免升级后目标目录突然变了。
    legacy_root = data.pop("remote_root", None)
    if isinstance(legacy_root, str) and legacy_root.strip() and "app_name" not in data:
        name, sub = _split_legacy_remote_root(legacy_root)
        if name:
            data["app_name"] = name
            if sub and "remote_subdir" not in data:
                data["remote_subdir"] = sub
            logger.info(f"已把配置项 remote_root={legacy_root} 迁移为 app_name={name}")
        else:
            logger.warning(f"旧配置 remote_root={legacy_root} 不在 /apps/ 下，已忽略")

    unknown = set(data) - set(SPECS)
    if unknown:
        logger.warning(f"配置文件含未知项，已忽略: {', '.join(sorted(unknown))}")
    return {k: v for k, v in data.items() if k in SPECS}


def _split_legacy_remote_root(value: str):
    """/apps/Foo/bar -> ("Foo", "bar")；不在 /apps/ 下返回 ("", "")。"""
    parts = [p for p in str(value).strip().split("/") if p]
    if len(parts) < 2 or parts[0] != "apps":
        return "", ""
    return parts[1], "/".join(parts[2:])


def save_config(data: Dict[str, Any]) -> Path:
    path = config_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _coerce(name: str, value: Any) -> Any:
    """把 JSON 里的值转成字段该有的类型（用户可能手写成字符串）。"""
    parser = SPECS[name][1]
    if value is None:
        return None
    if isinstance(value, str):
        return parser(value)
    if isinstance(value, list):
        # 列表类字段直接接受；其它字段交给解析器拼成字符串再解析
        if SPECS[name][0] in ("list", "int-list?"):
            return value
        return parser(",".join(str(x) for x in value))
    return value


def _env_value(name: str) -> Optional[str]:
    return os.environ.get(ENV_PREFIX + name.upper())


def effective_values() -> Dict[str, Any]:
    """合并后的生效值（不含来源信息）。"""
    defaults = ResourceSettings()
    merged: Dict[str, Any] = {f.name: getattr(defaults, f.name) for f in fields(defaults)}

    for name, value in load_config().items():
        try:
            merged[name] = _coerce(name, value)
        except ValueError as e:
            logger.warning(f"配置项 {name} 无效，沿用默认值: {e}")

    for name in SPECS:
        raw = _env_value(name)
        if raw is None:
            continue
        try:
            merged[name] = SPECS[name][1](raw)
        except ValueError as e:
            logger.warning(f"环境变量 {ENV_PREFIX}{name.upper()} 无效，已忽略: {e}")

    return merged


def sources() -> Dict[str, str]:
    """每个字段的值来自哪里：env / file / default。"""
    file_cfg = load_config()
    result = {}
    for name in SPECS:
        if _env_value(name) is not None:
            result[name] = "env"
        elif name in file_cfg:
            result[name] = "file"
        else:
            result[name] = "default"
    return result


def build_settings() -> ResourceSettings:
    """构造带用户覆盖的 ResourceSettings，供运行时使用。"""
    return ResourceSettings(**effective_values())


def set_value(name: str, raw: Any) -> Path:
    """写入一项配置（会先校验取值与组合约束）。"""
    if name not in SPECS:
        raise KeyError(name)

    data = load_config()
    data[name] = _coerce(name, raw)

    merged = {**effective_values(), name: data[name]}
    if not merged["allow_use_memory"] and not merged["allow_use_disk"]:
        raise ValueError("allow_use_memory 与 allow_use_disk 不能同时为 false")
    ResourceSettings(**merged)      # 字段名与类型的一致性兜底
    return save_config(data)


def unset_value(name: str) -> Path:
    """删除一项配置，回到默认值。"""
    data = load_config()
    data.pop(name, None)
    return save_config(data)
