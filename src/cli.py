# src/cli.py
import click
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from src.logger import get_logger
from src.i18n import _, _f
from src.config.settings import ResourceSettings
from src.config.user_config import (
    build_settings, config_path, effective_values, load_config,
    set_value, sources, unset_value, SPECS, ENV_PREFIX,
)
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import (
    compress_and_split, entries_payload, fingerprint_of, diff_entries,
    password_tag, VolumeInfo, VolumeMismatchError, iter_entries,
    LAYOUTS, LAYOUT_CONTINUOUS, LAYOUT_SELF_CONTAINED, PACK_BYS, PACK_BY_SIZE,
    effective_volume_bytes, FileTooLargeForVolume,
)
from src.core.uploader import VolumeUploader, UploadResult
from src.storage import available_providers, create_provider
from src.database.task_db import TaskDB, TaskStatus

logger = get_logger(__name__)


DEFAULT_VOLUME_SIZE = 1 * 1024 * 1024 * 1024


def _make_provider(name: Optional[str] = None):
    """
    构造存储后端。name 为空时取配置里的 provider（默认 baidu）。

    local 后端把分卷写到本地目录，用于离线预演：不联网、不占配额，
    也能用来验证分卷切分和续传逻辑。
    """
    settings = build_settings()
    provider_name = (name or settings.provider or "baidu").strip().lower()

    if provider_name == "local":
        root = settings.local_rehearsal_dir
        click.echo(f"[INFO] 使用本地后端，落盘目录: {root}")
        provider = create_provider("local", root=root)
        provider.authenticate(interactive=False)
        return provider

    provider = create_provider("baidu")
    provider.authenticate(interactive=True)
    return provider


def make_task_id(source_dir: str, name: str, remote: str, volume_size: int,
                 file_list: List[str], layout: str = LAYOUT_CONTINUOUS,
                 pack_by: str = PACK_BY_SIZE) -> str:
    sorted_files = sorted(f.strip() for f in file_list)
    raw = (f"{source_dir}|{name}|{remote}|{volume_size}|"
           f"{','.join(sorted_files)}|{layout}|{pack_by}")
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _ts_to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


VERIFY_CHOICES = ['full', 'stat', 'off']


def _print_diff(diff: dict, limit: int = 5) -> None:
    """把源文件差异打印成人能读的形式，每类最多列 limit 条。"""
    added, removed, changed = diff['added'], diff['removed'], diff['changed']
    if not (added or removed or changed):
        return

    click.echo(_("[DIFF] 源文件与任务创建时不一致："), err=True)
    for item in added[:limit]:
        click.echo(_f("  + {0}  ({1} 字节)", item[0], item[1]), err=True)
    for item in removed[:limit]:
        click.echo(_f("  - {0}  （已删除）", item[0]), err=True)
    for name, kind, old, new in changed[:limit]:
        if kind == 'size':
            click.echo(_f("  ~ {0}  大小 {1} → {2}", name, old, new), err=True)
        else:
            click.echo(_f("  ~ {0}  mtime 变了", name), err=True)

    for label, items in ((_("新增"), added), (_("删除"), removed), (_("改动"), changed)):
        if len(items) > limit:
            click.echo(_f("  … 另有 {0} 个{1}未列出", len(items) - limit, label), err=True)


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
        click.echo(_("[INFO] 该任务没有指纹基线，已按当前源文件补记；本次不做比对"))
        return

    if stored_fp == fingerprint_of(current):
        return

    try:
        stored = json.loads(task.get('entries_json') or '[]')
    except (TypeError, ValueError):
        stored = []

    _print_diff(diff_entries(stored, current))
    click.echo(
        _f("\n[ERR] 源文件已变化，中断前上传的分卷无法与新生成的字节流对齐。\n"
           "      选择一：`delete {0}` 后重建（会得到新的 task_id，旧分卷留在云端需自行清理）\n"
           "      选择二：确认源文件其实没变（例如只改动了 mtime），加 `--verify=off` 强制续传",
           task_id),
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
            _f("第 {0} 卷与记录不符：md5 记录 {1}… 实际 {2}…，大小记录 {3} 实际 {4}",
               info.part_number, prev['md5'][:8], info.md5[:8],
               prev.get('size_bytes'), info.size)
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
            click.echo(_("[WARN] 该任务创建时未加密，已忽略密码参数"), err=True)
        return None

    if not pwd:
        pwd = click.prompt(_("压缩包密码"), hide_input=True)

    expected = task.get('password_tag')
    if expected and password_tag(task_id, pwd) != expected:
        click.echo(_("[ERR] 密码与任务创建时不一致。"), err=True)
        raise SystemExit(1)
    return pwd


def _run_upload(task_id: str, task: dict, db: TaskDB,
                backpressure: Optional[int] = None,
                verify: str = 'full',
                password: Optional[str] = None,
                provider_name: Optional[str] = None,
                layout: Optional[str] = None,
                pack_by: Optional[str] = None) -> None:
    settings = build_settings()
    rm = ResourceManager(settings)

    # 清理上次中断残留的孤儿分卷
    cleaned = rm.cleanup_orphan_volumes()
    if cleaned:
        click.echo(_f("[CLEAN] 清理了 {0} 个残留分卷", cleaned))

    _verify_source(task, db, task_id, verify)
    pwd = _resolve_password(task, password, task_id)

    provider = _make_provider(provider_name)
    uploader = VolumeUploader(provider, rm, db=db, task_id=task_id)
    uploader.remote_suffix = task.get('remote_suffix', '.zip')

    db.update_task_status(task_id, TaskStatus.RUNNING)

    volume_files: dict = {}      # part_number -> [arcname, ...]，供索引使用

    def _record_volume(info: VolumeInfo) -> None:
        """压缩器每写完一卷就调用。

        - 与库里已有记录比对（校验）；不一致直接抛错，中止后续上传；
        - 一致或尚无记录时，把 MD5 / 大小 / 偏移落库。
        """
        if verify == 'full':
            _check_volume_against_record(db.get_volume(task_id, info.part_number), info)

        if info.files:
            volume_files[info.part_number] = list(info.files)

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

        effective_layout = layout or task.get('layout') or LAYOUT_CONTINUOUS
        effective_pack_by = pack_by or task.get('pack_by') or PACK_BY_SIZE

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
            layout=effective_layout,
            pack_by=effective_pack_by,
        )
        if backpressure == 0:
            bp = uploader.worker_count * 2
        elif backpressure is None:
            # 命令行没指定时，回落到配置文件里的默认值
            bp = settings.max_pending_volumes
        else:
            bp = backpressure

        if bp and bp > 50:
            click.echo(_f("[WARN] 背压上限 {0} 较大，可能占用较多临时空间", bp))

        result, uploaded, failed, skipped = uploader.upload_all(
            vol_gen, task['remote_dir'], task['base_name'], task_id,
            backpressure=bp
        )

        if result == UploadResult.COMPLETED:
            db.update_task_status(task_id, TaskStatus.COMPLETED)
            click.echo(_f("[OK] 任务 {0} 完成！本次上传 {1} 卷, 跳过 {2} 卷",
                          task_id, len(uploaded), len(skipped)))
            _upload_index(provider, uploader, task, task_id,
                          volume_files, effective_layout, effective_pack_by)
        elif result == UploadResult.PARTIAL:
            msg = _f("失败分卷: {0}", failed)
            db.update_task_status(task_id, TaskStatus.PARTIAL, error_message=msg)
            click.echo(_f("[WARN] 任务 {0} 部分完成。上传 {1}, 跳过 {2}, 失败 {3}",
                          task_id, len(uploaded), len(skipped), len(failed)))
            click.echo(_f("    使用 `resume {0}` 重试失败分卷", task_id))
        else:
            db.update_task_status(task_id, TaskStatus.FAILED,
                                  error_message=_f("所有分卷失败: {0}", failed))
            click.echo(_f("[ERR] 任务 {0} 全部失败", task_id), err=True)

    except FileTooLargeForVolume as e:
        db.update_task_status(task_id, TaskStatus.FAILED, error_message=str(e))
        click.echo(_f("[ERR] {0}", e), err=True)
        click.echo(_("      self-contained 模式要求每个文件完整落在一卷内。可选："), err=True)
        click.echo(_("      1) 调大 --volume-size   2) 改用 --layout continuous"), err=True)
        raise SystemExit(1)
    except VolumeMismatchError as e:
        db.update_task_status(task_id, TaskStatus.FAILED, error_message=str(e))
        click.echo(_f("[ERR] 续传校验失败：{0}", e), err=True)
        click.echo(_("      源文件在中断后被改动过，请 `delete` 后重建任务。"), err=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        # 已被 uploader 记录状态，这里仅提示
        sys.stderr.write(_("[STOP] 任务已中断，可通过 `resume` 恢复\n"))
        sys.stderr.flush()
        raise
    except Exception as e:
        db.update_task_status(task_id, TaskStatus.FAILED, error_message=str(e))
        click.echo(_f("[ERR] 任务 {0} 异常: {1}", task_id, e), err=True)
        raise


def _upload_index(provider, uploader, task: dict, task_id: str,
                  volume_files: dict, layout: str, pack_by: str) -> None:
    """
    上传一份索引：哪一卷里有哪些文件。

    self-contained 布局下每卷能单独解压，但"我要的那个文件在第几卷"没有答案；
    索引就是干这个的。上传失败不影响已上传的分卷，只记日志。
    """
    if layout != LAYOUT_SELF_CONTAINED or not volume_files:
        return

    base = task['base_name']
    remote_dir = task['remote_dir'].rstrip('/')
    payload = {
        "format": 1,
        "tool": "netdisk-stream-uploader",
        "task_id": task_id,
        "base_name": base,
        "remote_dir": remote_dir,
        "layout": layout,
        "pack_by": pack_by,
        "volume_size": task['volume_size_bytes'],
        "volumes": [
            {
                "part": part,
                "remote_path": f"{remote_dir}/{base}.part{part:04d}{uploader.remote_suffix}",
                "files": volume_files[part],
            }
            for part in sorted(volume_files)
        ],
    }

    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8')
    remote = f"{remote_dir}/{base}.index.json"
    try:
        provider.upload_file(remote, io.BytesIO(data), len(data))
        click.echo(_f("[OK] 索引已上传: {0}", remote))
    except Exception as e:                      # 索引是附加信息，不该影响任务
        logger.warning(f"索引上传失败（分卷不受影响）: {e}")


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
        click.echo(_f("[CLEAN] {0} 个上次中断的任务已标记为 interrupted，"
                      "可用 `resume <task_id>` 继续", stale))


@click.group()
def cli():
    """Netdisk Stream Uploader - 命令行工具"""
    _reset_stale_tasks()


@cli.command()
@click.option('--source', '-s', required=True, help=_('源目录路径'))
@click.option('--files', '-f', required=True,
              help=_('要压缩的文件列表，逗号分隔。用 "." 表示 source 下全部'))
@click.option('--remote', '-r', default=None,
              help=_('网盘目标目录，默认由配置项 app_name 推导为 /apps/<应用名>'))
@click.option('--name', '-n', required=True, help=_('压缩包基本名称（不含扩展名）'))
@click.option('--volume-size', '-v', default=DEFAULT_VOLUME_SIZE,
              help=_('分卷大小（字节），默认 1GB'))
@click.option('--resume', is_flag=True, help=_('尝试续传已存在的同名任务'))
@click.option('--backpressure', default=None, is_flag=False, flag_value='auto',
              help=_('启用背压。无值=默认上限；--backpressure=20=上限20；不传=不启用'))
@click.option('--ext', default='.zip', help=_('上传后的文件后缀（如 .zip / .zi123p）'))
@click.option('--verify', type=click.Choice(VERIFY_CHOICES), default='full',
              show_default=True,
              help=_('续传前校验：full=指纹+逐卷 MD5，stat=只比指纹，off=不校验'))
@click.option('--password', default=None,
              help=_('用 AES-256 加密归档。建议改用环境变量 STREAMPACK_PASSWORD，避免留在命令历史里'))
@click.option('--provider', type=click.Choice(available_providers()), default=None,
              help=_('存储后端；默认取配置项 provider（baidu）。local 用于离线预演'))
@click.option('--layout', type=click.Choice(list(LAYOUTS)), default=LAYOUT_CONTINUOUS,
              show_default=True,
              help=_('分卷布局：continuous=一个 ZIP 切成多段（需拼接）；'
                     'self-contained=每卷一个独立 ZIP（可单独解压，但单文件不能超过分卷）'))
@click.option('--pack-by', type=click.Choice(list(PACK_BYS)), default=PACK_BY_SIZE,
              show_default=True,
              help=_('装箱方式：size=只按大小装满；folder=尽量让同一个顶层文件夹落在少数卷里'
                     '（只在 self-contained 布局下生效）'))
def upload(source, files, remote, name, volume_size, resume, ext, backpressure,
           verify, password, provider, layout, pack_by):
    """创建并执行上传任务。若任务已存在，将续传/重跑。"""
    file_list = [f.strip() for f in files.split(',')]
    source_dir = Path(source).resolve()
    if not source_dir.exists():
        click.echo(_f("[ERR] 源目录 {0} 不存在", source_dir), err=True)
        return

    # 目标目录由应用名推导：/apps/<应用名>[/子目录]，不让人手填整条路径
    settings = build_settings()
    if not remote:
        remote = settings.remote_root

    task_id = make_task_id(str(source_dir), name, remote, volume_size, file_list,
                           layout, pack_by)
    click.echo(_f("任务 ID: {0}", task_id))

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
            click.echo(_f("任务 {0} 已完成。如需重做请先 delete。", task_id))
            return
        click.echo(_f("发现任务 {0}（{1}），将续传/重跑", task_id, status))
        _run_upload(task_id, existing, db, backpressure=bp_limit,
                    verify=verify, password=password, provider_name=provider)
        return

    entries = entries_payload(str(source_dir), file_list)

    # 预先检查：self-contained 要求每个文件都能装进一卷，别等压到一半才报错
    if layout == LAYOUT_SELF_CONTAINED:
        entries_obj = list(iter_entries(str(source_dir), file_list))
        limit = effective_volume_bytes(volume_size)
        too_big = [e for e in entries_obj if e.size > limit]
        if too_big:
            click.echo(_f("[ERR] {0} 个文件超过分卷大小（上限 {1}，留 0.5% 余量后 {2}）",
                          len(too_big),
                          _f("{0:.2f} GB", volume_size / 1024 ** 3),
                          _f("{0:.2f} GB", limit / 1024 ** 3)), err=True)
            for entry in too_big[:10]:
                click.echo(_f("      {0}  {1}", entry.arcname,
                              _f("{0:.2f} GB", entry.size / 1024 ** 3)), err=True)
            if len(too_big) > 10:
                click.echo(_f("      … 另有 {0} 个", len(too_big) - 10), err=True)
            click.echo(_("      self-contained 模式下每个文件必须完整落在一卷内。可选："), err=True)
            click.echo(_("      1) 调大 --volume-size   2) 改用 --layout continuous"), err=True)
            return

    db.create_task(task_id, str(source_dir), file_list, remote, name,
                   volume_size, remote_suffix=ext,
                   fingerprint=fingerprint_of(entries),
                   entries_json=json.dumps(entries),
                   format_version=2,
                   encrypted=bool(pwd),
                   password_tag=password_tag(task_id, pwd) if pwd else None,
                   layout=layout, pack_by=pack_by)
    task = db.get_task(task_id)
    _run_upload(task_id, task, db, backpressure=bp_limit,
                verify=verify, password=password, provider_name=provider,
                layout=layout, pack_by=pack_by)


@cli.command()
@click.argument('task_id')
@click.option('--verify', type=click.Choice(VERIFY_CHOICES), default='full',
              show_default=True,
              help=_('续传前校验：full=指纹+逐卷 MD5，stat=只比指纹，off=不校验'))
@click.option('--password', default=None,
              help=_('加密任务的密码（也可用环境变量 STREAMPACK_PASSWORD；缺省时交互输入）'))
@click.option('--provider', type=click.Choice(available_providers()), default=None,
              help=_('存储后端；默认取配置项 provider（baidu）'))
def resume(task_id, verify, password, provider):
    """恢复中断的上传任务"""
    db = TaskDB()
    task = db.get_task(task_id)
    if not task:
        click.echo(_f("[ERR] 任务 {0} 不存在", task_id), err=True)
        return
    if task['status'] == TaskStatus.COMPLETED.value:
        click.echo(_f("任务 {0} 已完成，无需恢复", task_id))
        return
    click.echo(_f("已上传分卷: {0}", db.get_completed_part_numbers(task_id)))
    click.echo(_f("待重试分卷: {0}", db.get_failed_part_numbers(task_id)))
    _run_upload(task_id, task, db, backpressure=None, verify=verify,
                password=password, provider_name=provider)


@cli.command(name='list')
def list_tasks():
    """列出所有任务"""
    db = TaskDB()
    tasks = db.get_all_tasks()
    if not tasks:
        click.echo(_("没有任务记录"))
        return
    for t in tasks:
        target = f"{t['remote_dir']}/{t['base_name']}{t.get('remote_suffix', '.zip')}"
        click.echo(_f("{0} | {1} | 分卷 {2}/{3} | {4}", t['task_id'], t['status'],
                      t['completed_volumes'], t['total_volumes'], target))


@cli.command()
@click.argument('task_id')
def status(task_id):
    """查看任务详情"""
    db = TaskDB()
    task = db.get_task(task_id)
    if not task:
        click.echo(_f("[ERR] 任务 {0} 不存在", task_id), err=True)
        return
    target = f"{task['remote_dir']}/{task['base_name']}{task.get('remote_suffix', '.zip')}"
    click.echo(_f("任务 ID: {0}", task['task_id']))
    click.echo(_f("状态: {0}", task['status']))
    click.echo(_f("源目录: {0}", task['source_dir']))
    click.echo(_f("文件数: {0}", len(task['file_list'])))
    click.echo(_f("目标: {0}", target))
    click.echo(_f("分卷大小: {0} MB", task['volume_size_bytes'] // (1024 * 1024)))
    click.echo(_f("总卷数: {0}", task['total_volumes']))
    click.echo(_f("已完成: {0}", task['completed_volumes']))
    if task['error_message']:
        click.echo(_f("错误: {0}", task['error_message']))
    volumes = db.get_all_volumes(task_id)
    if volumes:
        click.echo(_("\n分卷状态:"))
        for v in volumes:
            click.echo(_f("  {0:4d} | {1} | {2}", v['part_number'], v['status'], v['remote_path']))


@cli.command()
@click.argument('task_id')
@click.option('--yes', is_flag=True, help=_('确认删除，无需二次确认'))
def delete(task_id, yes):
    """删除任务记录（不影响已上传文件）"""
    db = TaskDB()
    if not yes:
        click.confirm(_f("确定要删除任务 {0} 吗？", task_id), abort=True)
    db.delete_task(task_id)
    click.echo(_f("任务 {0} 已删除", task_id))


@cli.group()
def config():
    """查看与修改用户配置（~/.netdisk_uploader/config.json）"""


@config.command('path')
def config_path_cmd():
    """显示配置文件位置"""
    p = config_path()
    click.echo(str(p))
    click.echo(_f("状态: {0}", _("已存在") if p.exists() else _("尚未创建（全部使用默认值）")))


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

    click.echo(_f("{0:<{1}}  {2:<{3}}  {4}", _("配置项"), name_width,
                  _("值"), value_width, _("来源")))
    click.echo("-" * (name_width + value_width + 12))
    for name, shown, source in rows:
        click.echo(f"{name:<{name_width}}  {shown:<{value_width}}  {source}")

    click.echo()
    click.echo(_f("来源：env = 环境变量 {0}<字段名>，"
                  "file = 配置文件，default = 代码默认值", ENV_PREFIX))


@config.command('get')
@click.argument('key')
def config_get(key):
    """读取一项配置的生效值"""
    if key not in SPECS:
        click.echo(_f("[ERR] 未知配置项: {0}（用 `config list` 查看可选项）", key), err=True)
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
        click.echo(_f("[ERR] 未知配置项: {0}（用 `config list` 查看可选项）", key), err=True)
        raise SystemExit(1)
    try:
        path = set_value(key, value)
    except ValueError as e:
        click.echo(_f("[ERR] {0}", e), err=True)
        raise SystemExit(1)
    except TypeError as e:
        click.echo(_f("[ERR] 取值不合法: {0}", e), err=True)
        raise SystemExit(1)
    click.echo(_f("[OK] {0} = {1}", key, effective_values()[key]))
    click.echo(_f("     已写入 {0}", path))


@config.command('unset')
@click.argument('key')
def config_unset(key):
    """删除一项配置，回到默认值"""
    if key not in SPECS:
        click.echo(_f("[ERR] 未知配置项: {0}", key), err=True)
        raise SystemExit(1)
    path = unset_value(key)
    click.echo(_f("[OK] {0} 已恢复默认值（{1}）", key, effective_values()[key]))
    click.echo(_f("     已写入 {0}", path))


def _translate_command_docs(group: click.Group) -> None:
    """
    click 拿 docstring 当命令说明，而且是在装饰时就抓走的，
    所以只能在定义完之后统一翻一遍。
    """
    if group.help:
        group.help = _(group.help)
    if group.short_help:
        group.short_help = _(group.short_help)

    for cmd in group.commands.values():
        if cmd.help:
            cmd.help = _(cmd.help)
        if cmd.short_help:
            cmd.short_help = _(cmd.short_help)
        if isinstance(cmd, click.Group):
            _translate_command_docs(cmd)


_translate_command_docs(cli)


if __name__ == '__main__':
    from src.logger import setup_logging
    setup_logging()
    cli()