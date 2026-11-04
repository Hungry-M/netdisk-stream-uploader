# src/cli.py
import click
import logging
import hashlib
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from src.config.settings import ResourceSettings
from src.hardware.resource_manager import ResourceManager
from src.core.compressor import compress_and_split
from src.core.uploader import VolumeUploader, UploadResult
from src.baidu.client import BaiduClient
from src.database.task_db import TaskDB, TaskStatus

logger = logging.getLogger(__name__)


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


def _run_upload(task_id: str, task: dict, db: TaskDB,
                backpressure: Optional[int] = None) -> None:
    settings = ResourceSettings()
    rm = ResourceManager(settings)

    # 清理上次中断残留的孤儿分卷
    cleaned = rm.cleanup_orphan_volumes()
    if cleaned:
        click.echo(f"[CLEAN] 清理了 {cleaned} 个残留分卷")

    baidu_client = get_client()
    uploader = VolumeUploader(baidu_client, rm, db=db, task_id=task_id)
    uploader.remote_suffix = task.get('remote_suffix', '.zip')

    db.update_task_status(task_id, TaskStatus.RUNNING)

    try:
        vol_gen = compress_and_split(
            source_dir=task['source_dir'],
            file_list=task['file_list'],
            volume_size_bytes=task['volume_size_bytes'],
            resource_manager=rm,
            fixed_modified_at=_ts_to_dt(task['fixed_modified_at'] or task['created_at'])
        )
        if backpressure == 0:
            bp = uploader.worker_count * 2
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
def upload(source, files, remote, name, volume_size, resume, ext, backpressure):
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

    db = TaskDB()
    existing = db.get_task(task_id)
    if existing:
        status = existing['status']
        if status == TaskStatus.COMPLETED.value:
            click.echo(f"任务 {task_id} 已完成。如需重做请先 delete。")
            return
        click.echo(f"发现任务 {task_id}（{status}），将续传/重跑")
        _run_upload(task_id, existing, db, backpressure=bp_limit)
        return

    db.create_task(task_id, str(source_dir), file_list, remote, name,
                   volume_size, remote_suffix=ext)
    task = db.get_task(task_id)
    _run_upload(task_id, task, db, backpressure=bp_limit)


@cli.command()
@click.argument('task_id')
def resume(task_id):
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
    _run_upload(task_id, task, db, backpressure=None)


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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    cli()