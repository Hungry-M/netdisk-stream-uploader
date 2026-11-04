# src/cli.py
import click
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from src.logger import get_logger
from src.config.settings import ResourceSettings
from src.config.user_config import (
    build_settings, config_path, effective_values, load_config,
    set_value, sources, unset_value, SPECS, ENV_PREFIX,
)
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import (
    compress_and_split, entries_payload, fingerprint_of, diff_entries,
    password_tag, VolumeInfo, VolumeMismatchError,
)
from src.core.uploader import VolumeUploader, UploadResult
from src.baidu.client import BaiduClient
from src.database.task_db import TaskDB, TaskStatus

logger = get_logger(__name__)


DEFAULT_VOLUME_SIZE = 1 * 1024 * 1024 * 1024


def get_client() -> BaiduClient:
    from src.config.credentials import load_credentials
    creds = load_credentials()
    client = BaiduClient(creds.client_id, creds.client_secret, creds.token_file)
    client._get_access_token()
    return client


def make_task_id(source_dir: str, name: str, remote: str, volume_size: int,
                 file_list: List[str]) -> str:
    sorted_files = sorted(f.strip() for f in file_list)
    raw = f"{source_dir}|{name}|{remote}|{volume_size}|{','.join(sorted_files)}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _ts_to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


VERIFY_CHOICES = ['full', 'stat', 'off']


def _print_diff(diff: dict, limit: int = 5) -> None:
    """把源文件差异打印成人能读的形式，每类最多列 limit 条。"""
    added, removed, changed = diff['added'], diff['removed'], diff['changed']
    if not (added or removed or changed):
        return

    click.echo("[DIFF] 源文件与任务创建时不一致：", err=True)
    for item in added[:limit]:
        click.echo(f"  + {item[0]}  ({item[1]} 字节)", err=True)
    for item in removed[:limit]:
        click.echo(f"  - {item[0]}  （已删除）", err=True)
    for name, kind, old, new in changed[:limit]:
        if kind == 'size':
            click.echo(f"  ~ {name}  大小 {old} → {new}", err=True)
        else:
            click.echo(f"  ~ {name}  mtime 变了", err=True)

    for label, items in (("新增", added), ("删除", removed), ("改动", changed)):
        if len(items) > limit:
            click.echo(f"  … 另有 {len(items) - limit} 个{label}未列出", err=True)


def _verify_source(task: dict, db: TaskDB, task_id: str, verify: str) -> None:
    """
    续传前确认源文件没被改动过。

    指纹不一致时**不自动重建**：task_id 由参数哈希得到，重建会覆盖云端同名
    分卷，必须由用户显式决定。
    """
    if verify == 'off':
        return

    current = entries_payload(task['source_dir'], task['file_list'])
    stored_fp = task.get('fingerprint')

    if not stored_fp:
        # 升级前创建的老任务没有基线，补记一次，本次无法比对
        db.update_task_fingerprint(
            task_id, fingerprint_of(current), json.dumps(current)
        )
        click.echo("[INFO] 该任务没有指纹基线，已按当前源文件补记；本次不做比对")
        return

    if stored_fp == fingerprint_of(current):
        return

    try:
        stored = json.loads(task.get('entries_json') or '[]')
    except (TypeError, ValueError):
        stored = []

    _print_diff(diff_entries(stored, current))
    click.echo(
        "\n[ERR] 源文件已变化，中断前上传的分卷无法与新生成的字节流对齐。\n"
        f"      选择一：`delete {task_id}` 后重建（会得到新的 task_id，旧分卷留在云端需自行清理）\n"
        "      选择二：确认源文件其实没变（例如只改动了 mtime），加 `--verify=off` 强制续传",
        err=True,
    )
    raise SystemExit(1)


def _build_manifest(task_id: str, task: dict) -> dict:
    """
    构造归档内嵌的 manifest。

    只含"任务创建时就确定"的字段：一旦掺进压缩结果（比如每卷 MD5），
    重压就复现不出同样的字节流，续传校验会失效。
    """
    try:
        files = json.loads(task.get('entries_json') or '[]')
    except (TypeError, ValueError):
        files = []

    return {
        "format": int(task.get('format_version') or 1),
        "tool": "netdisk-stream-uploader",
        "task_id": task_id,
        "base_name": task['base_name'],
        "remote_dir": task['remote_dir'],
        "volume_size": task['volume_size_bytes'],
        "created_at": task['created_at'],
        "fixed_modified_at": task.get('fixed_modified_at'),
        "files": files,
    }


def _check_volume_against_record(prev: Optional[dict], info: VolumeInfo) -> None:
    """
    把刚压出来的一卷与数据库记录比对。

    只在与"已完成"的分卷比较时才有意义：未上传的卷本来就该被覆盖。
    老记录没有 md5（本功能上线前创建）时跳过比对，否则会误判。
    """
    if not prev or prev.get('status') != 'done' or prev.get('md5') is None:
        return
    if (prev['md5'] != info.md5
            or prev.get('size_bytes') != info.size
            or prev.get('start_offset') != info.start_offset):
        raise VolumeMismatchError(
            f"第 {info.part_number} 卷与记录不符："
            f"md5 记录 {prev['md5'][:8]}… 实际 {info.md5[:8]}…，"
            f"大小记录 {prev.get('size_bytes')} 实际 {info.size}"
        )


PASSWORD_ENV = "STREAMPACK_PASSWORD"


def _resolve_password(task: dict, provided: Optional[str], task_id: str) -> Optional[str]:
    """
    确定本次压缩要用的密码。

    加密任务在续传时必须拿到同一个密码：命令行参数 > 环境变量 > 交互输入。
    拿到后先核对密码校验值，避免"密码错了但先压了半小时才发现"。
    """
    encrypted = bool(task.get('encrypted'))
    pwd = provided or os.environ.get(PASSWORD_ENV)

    if not encrypted:
        if pwd:
            click.echo("[WARN] 该任务创建时未加密，已忽略密码参数", err=True)
        return None

    if not pwd:
        pwd = click.prompt("压缩包密码", hide_input=True)

    expected = task.get('password_tag')
    if expected and password_tag(task_id, pwd) != expected:
        click.echo("[ERR] 密码与任务创建时不一致。", err=True)
        raise SystemExit(1)
    return pwd


def _run_upload(task_id: str, task: dict, db: TaskDB,
                backpressure: Optional[int] = None,
                verify: str = 'full',
                password: Optional[str] = None) -> None:
    settings = build_settings()
    rm = ResourceManager(settings)

    # 清理上次中断残留的孤儿分卷
    cleaned = rm.cleanup_orphan_volumes()
    if cleaned:
        click.echo(f"[CLEAN] 清理了 {cleaned} 个残留分卷")

    _verify_source(task, db, task_id, verify)
    pwd = _resolve_password(task, password, task_id)

    baidu_client = get_client()
    uploader = VolumeUploader(baidu_client, rm, db=db, task_id=task_id)
    uploader.remote_suffix = task.get('remote_suffix', '.zip')

    db.update_task_status(task_id, TaskStatus.RUNNING)

    def _record_volume(info: VolumeInfo) -> None:
        """压缩器每写完一卷就调用。

        - 与库里已有记录比对（校验）；不一致直接抛错，中止后续上传；
        - 一致或尚无记录时，把 MD5 / 大小 / 偏移落库。
        """
        if verify == 'full':
            _check_volume_against_record(db.get_volume(task_id, info.part_number), info)

        remote_path = (f"{task['remote_dir'].rstrip('/')}/{task['base_name']}"
                       f".part{info.part_number:04d}{uploader.remote_suffix}")
        try:
            db.upsert_volume(
                task_id, info.part_number, remote_path, info.temp_path,
                md5=info.md5, size_bytes=info.size, start_offset=info.start_offset,
            )
        except Exception as e:      # 记录失败不该中断上传
            logger.warning(f"记录分卷元数据失败（卷 {info.part_number}）: {e}")

    try:
        manifest = None
        if int(task.get('format_version') or 1) >= 2:
            manifest = _build_manifest(task_id, task)

        vol_gen = compress_and_split(
            source_dir=task['source_dir'],
            file_list=task['file_list'],
            volume_size_bytes=task['volume_size_bytes'],
            resource_manager=rm,
            fixed_modified_at=_ts_to_dt(task['fixed_modified_at'] or task['created_at']),
            on_volume_complete=_record_volume,
            manifest=manifest,
            password=pwd,
            task_id=task_id,
        )
        if backpressure == 0:
            bp = uploader.worker_count * 2
        elif backpressure is None:
            # 命令行没指定时，回落到配置文件里的默认值
            bp = settings.max_pending_volumes
        else:
            bp = backpressure

        if bp and bp > 50:
            click.echo(f"[WARN] 背压上限 {bp} 较大，可能占用较多临时空间")

        result, uploaded, failed, skipped = uploader.upload_all(
            vol_gen, task['remote_dir'], task['base_name'], task_id,
            backpressure=bp
        )

        if result == UploadResult.COMPLETED:
            db.update_task_status(task_id, TaskStatus.COMPLETED)
            click.echo(
                f"[OK] 任务 {task_id} 完成！"
                f"本次上传 {len(uploaded)} 卷, 跳过 {len(skipped)} 卷"
            )
        elif result == UploadResult.PARTIAL:
            msg = f"失败分卷: {failed}"
            db.update_task_status(task_id, TaskStatus.PARTIAL, error_message=msg)
            click.echo(
                f"[WARN] 任务 {task_id} 部分完成。"
                f"上传 {len(uploaded)}, 跳过 {len(skipped)}, 失败 {len(failed)}"
            )
            click.echo(f"    使用 `resume {task_id}` 重试失败分卷")
        else:
            db.update_task_status(task_id, TaskStatus.FAILED,
                                  error_message=f"所有分卷失败: {failed}")
            click.echo(f"[ERR] 任务 {task_id} 全部失败", err=True)

    except VolumeMismatchError as e:
        db.update_task_status(task_id, TaskStatus.FAILED, error_message=str(e))
        click.echo(f"[ERR] 续传校验失败：{e}", err=True)
        click.echo("      源文件在中断后被改动过，请 `delete` 后重建任务。", err=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        # 已被 uploader 记录状态，这里仅提示
        sys.stderr.write("[STOP] 任务已中断，可通过 `resume` 恢复\n")
        sys.stderr.flush()
        raise
    except Exception as e:
        db.update_task_status(task_id, TaskStatus.FAILED, error_message=str(e))
        click.echo(f"[ERR] 任务 {task_id} 异常: {e}", err=True)
        raise


def _reset_stale_tasks() -> None:
    """把上次异常退出遗留的 running 任务标记为 interrupted。

    数据库打不开也不该挡住命令本身，因此这里吞掉异常只记日志。
    """
    try:
        db = TaskDB()
        stale = db.reset_stale_running_tasks()
    except Exception as e:
        logger.warning(f"检查遗留任务状态失败: {e}")
        return
    if stale:
        click.echo(
            f"[CLEAN] {stale} 个上次中断的任务已标记为 interrupted，"
            f"可用 `resume <task_id>` 继续"
        )


@click.group()
def cli():
    """Netdisk Stream Uploader - 命令行工具"""
    _reset_stale_tasks()


@cli.command()
@click.option('--source', '-s', required=True, help='源目录路径')
@click.option('--files', '-f', required=True,
              help='要压缩的文件列表，逗号分隔。用 "." 表示 source 下全部')
@click.option('--remote', '-r', default='/apps/StreamPack',
              help='网盘目标目录（默认 /apps/StreamPack）')
@click.option('--name', '-n', required=True, help='压缩包基本名称（不含扩展名）')
@click.option('--volume-size', '-v', default=DEFAULT_VOLUME_SIZE,
              help='分卷大小（字节），默认 1GB')
@click.option('--resume', is_flag=True, help='尝试续传已存在的同名任务')
@click.option('--backpressure', default=None, is_flag=False, flag_value='auto',
              help='启用背压。无值=默认上限；--backpressure=20=上限20；不传=不启用')
@click.option('--ext', default='.zip', help='上传后的文件后缀（如 .zip / .zi123p）')
@click.option('--verify', type=click.Choice(VERIFY_CHOICES), default='full',
              show_default=True,
              help='续传前校验：full=指纹+逐卷 MD5，stat=只比指纹，off=不校验')
@click.option('--password', default=None,
              help='用 AES-256 加密归档。建议改用环境变量 STREAMPACK_PASSWORD，避免留在命令历史里')
def upload(source, files, remote, name, volume_size, resume, ext, backpressure,
           verify, password):
    """创建并执行上传任务。若任务已存在，将续传/重跑。"""
    file_list = [f.strip() for f in files.split(',')]
    source_dir = Path(source).resolve()
    if not source_dir.exists():
        click.echo(f"[ERR] 源目录 {source_dir} 不存在", err=True)
        return

    task_id = make_task_id(str(source_dir), name, remote, volume_size, file_list)
    click.echo(f"任务 ID: {task_id}")

    if backpressure is None:
        bp_limit = None
    elif backpressure == 'auto':
        bp_limit = 0
    else:
        bp_limit = int(backpressure)

    pwd = password or os.environ.get(PASSWORD_ENV)

    db = TaskDB()
    existing = db.get_task(task_id)
    if existing:
        status = existing['status']
        if status == TaskStatus.COMPLETED.value:
            click.echo(f"任务 {task_id} 已完成。如需重做请先 delete。")
            return
        click.echo(f"发现任务 {task_id}（{status}），将续传/重跑")
        _run_upload(task_id, existing, db, backpressure=bp_limit,
                    verify=verify, password=password)
        return

    entries = entries_payload(str(source_dir), file_list)
    db.create_task(task_id, str(source_dir), file_list, remote, name,
                   volume_size, remote_suffix=ext,
                   fingerprint=fingerprint_of(entries),
                   entries_json=json.dumps(entries),
                   format_version=2,
                   encrypted=bool(pwd),
                   password_tag=password_tag(task_id, pwd) if pwd else None)
    task = db.get_task(task_id)
    _run_upload(task_id, task, db, backpressure=bp_limit,
                verify=verify, password=password)


@cli.command()
@click.argument('task_id')
@click.option('--verify', type=click.Choice(VERIFY_CHOICES), default='full',
              show_default=True,
              help='续传前校验：full=指纹+逐卷 MD5，stat=只比指纹，off=不校验')
@click.option('--password', default=None,
              help='加密任务的密码（也可用环境变量 STREAMPACK_PASSWORD；缺省时交互输入）')
def resume(task_id, verify, password):
    """恢复中断的上传任务"""
    db = TaskDB()
    task = db.get_task(task_id)
    if not task:
        click.echo(f"[ERR] 任务 {task_id} 不存在", err=True)
        return
    if task['status'] == TaskStatus.COMPLETED.value:
        click.echo(f"任务 {task_id} 已完成，无需恢复")
        return
    click.echo(f"已上传分卷: {db.get_completed_part_numbers(task_id)}")
    click.echo(f"待重试分卷: {db.get_failed_part_numbers(task_id)}")
    _run_upload(task_id, task, db, backpressure=None, verify=verify, password=password)


@cli.command(name='list')
def list_tasks():
    """列出所有任务"""
    db = TaskDB()
    tasks = db.get_all_tasks()
    if not tasks:
        click.echo("没有任务记录")
        return
    for t in tasks:
        click.echo(
            f"{t['task_id']} | {t['status']} | "
            f"分卷 {t['completed_volumes']}/{t['total_volumes']} | "
            f"{t['remote_dir']}/{t['base_name']}{t.get('remote_suffix', '.zip')}"
        )


@cli.command()
@click.argument('task_id')
def status(task_id):
    """查看任务详情"""
    db = TaskDB()
    task = db.get_task(task_id)
    if not task:
        click.echo(f"[ERR] 任务 {task_id} 不存在", err=True)
        return
    click.echo(f"任务 ID: {task['task_id']}")
    click.echo(f"状态: {task['status']}")
    click.echo(f"源目录: {task['source_dir']}")
    click.echo(f"文件数: {len(task['file_list'])}")
    click.echo(f"目标: {task['remote_dir']}/{task['base_name']}{task.get('remote_suffix', '.zip')}")
    click.echo(f"分卷大小: {task['volume_size_bytes'] // (1024*1024)} MB")
    click.echo(f"总卷数: {task['total_volumes']}")
    click.echo(f"已完成: {task['completed_volumes']}")
    if task['error_message']:
        click.echo(f"错误: {task['error_message']}")
    volumes = db.get_all_volumes(task_id)
    if volumes:
        click.echo("\n分卷状态:")
        for v in volumes:
            click.echo(f"  {v['part_number']:4d} | {v['status']} | {v['remote_path']}")


@cli.command()
@click.argument('task_id')
@click.option('--yes', is_flag=True, help='确认删除，无需二次确认')
def delete(task_id, yes):
    """删除任务记录（不影响已上传文件）"""
    db = TaskDB()
    if not yes:
        click.confirm(f"确定要删除任务 {task_id} 吗？", abort=True)
    db.delete_task(task_id)
    click.echo(f"任务 {task_id} 已删除")


@cli.group()
def config():
    """查看与修改用户配置（~/.netdisk_uploader/config.json）"""


@config.command('path')
def config_path_cmd():
    """显示配置文件位置"""
    p = config_path()
    click.echo(str(p))
    click.echo(f"状态: {'已存在' if p.exists() else '尚未创建（全部使用默认值）'}")


@config.command('list')
def config_list():
    """列出所有配置项及其来源"""
    values = effective_values()
    srcs = sources()

    def render(value) -> str:
        if isinstance(value, list):
            return ",".join(str(v) for v in value)
        return "None" if value is None else str(value)

    rows = [(name, render(values[name]), srcs[name]) for name in SPECS]
    name_width = max(len(r[0]) for r in rows)
    value_width = min(max(len(r[1]) for r in rows), 72)

    click.echo(f"{'配置项':<{name_width}}  {'值':<{value_width}}  来源")
    click.echo("-" * (name_width + value_width + 12))
    for name, shown, source in rows:
        click.echo(f"{name:<{name_width}}  {shown:<{value_width}}  {source}")

    click.echo()
    click.echo(f"来源：env = 环境变量 {ENV_PREFIX}<字段名>，"
               f"file = 配置文件，default = 代码默认值")


@config.command('get')
@click.argument('key')
def config_get(key):
    """读取一项配置的生效值"""
    if key not in SPECS:
        click.echo(f"[ERR] 未知配置项: {key}（用 `config list` 查看可选项）", err=True)
        raise SystemExit(1)
    value = effective_values()[key]
    if isinstance(value, list):
        click.echo(",".join(str(v) for v in value))
    elif value is None:
        click.echo("None")
    else:
        click.echo(value)


@config.command('set')
@click.argument('key')
@click.argument('value')
def config_set(key, value):
    """修改一项配置（写入配置文件）"""
    if key not in SPECS:
        click.echo(f"[ERR] 未知配置项: {key}（用 `config list` 查看可选项）", err=True)
        raise SystemExit(1)
    try:
        path = set_value(key, value)
    except ValueError as e:
        click.echo(f"[ERR] {e}", err=True)
        raise SystemExit(1)
    except TypeError as e:
        click.echo(f"[ERR] 取值不合法: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"[OK] {key} = {effective_values()[key]}")
    click.echo(f"     已写入 {path}")


@config.command('unset')
@click.argument('key')
def config_unset(key):
    """删除一项配置，回到默认值"""
    if key not in SPECS:
        click.echo(f"[ERR] 未知配置项: {key}", err=True)
        raise SystemExit(1)
    path = unset_value(key)
    click.echo(f"[OK] {key} 已恢复默认值（{effective_values()[key]}）")
    click.echo(f"     已写入 {path}")


if __name__ == '__main__':
    from src.logger import setup_logging
    setup_logging()
    cli()