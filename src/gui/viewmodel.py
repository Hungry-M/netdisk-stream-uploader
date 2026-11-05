# src/gui/viewmodel.py
"""
GUI 的纯逻辑层：把数据库里的任务整理成界面要显示的形状。

这一层**不依赖任何 UI 库**，所以可以像普通模块一样测试；将来换 UI 库
（PySide6 / Tkinter / Web）只需要重写视图层。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.database.task_db import TaskDB, TaskStatus
from src.i18n import _

STATUS_LABELS = {
    TaskStatus.PENDING.value: "待开始",
    TaskStatus.RUNNING.value: "进行中",
    TaskStatus.COMPLETED.value: "已完成",
    TaskStatus.FAILED.value: "失败",
    TaskStatus.PARTIAL.value: "部分完成",
    TaskStatus.PAUSED.value: "已暂停",
    TaskStatus.INTERRUPTED.value: "已中断",
}


def label_for(status: str) -> str:
    """状态键 → 中文标签；未知状态原样返回，方便发现新值。"""
    return _(STATUS_LABELS.get(status, status))


@dataclass(frozen=True)
class TaskRow:
    """任务列表里的一行。"""
    task_id: str
    status: str
    base_name: str
    target: str                 # remote_dir/base_name.ext
    completed: int
    total: int
    error: Optional[str]

    @property
    def progress(self) -> float:
        """0.0 ~ 1.0；总卷数未知时返回 0。"""
        if not self.total:
            return 0.0
        return min(1.0, self.completed / self.total)

    @property
    def resumable(self) -> bool:
        return self.status != TaskStatus.COMPLETED.value

    @property
    def status_label(self) -> str:
        return label_for(self.status)


@dataclass(frozen=True)
class VolumeRow:
    """任务详情里的一个分卷。"""
    part_number: int
    status: str
    size_bytes: Optional[int]
    md5: Optional[str]
    remote_path: str


class TaskListViewModel:
    """任务列表的数据源。视图只负责把这些值画出来。"""

    def __init__(self, db: Optional[TaskDB] = None):
        self._db = db or TaskDB()

    # ---------- 读 ----------

    def refresh(self, reset_stale: bool = True) -> List[TaskRow]:
        """
        重新读取任务列表。

        reset_stale=True 时先把上次异常退出遗留的 running 复位成 interrupted，
        与 CLI 启动时的行为一致 —— 否则界面会一直显示"进行中"。
        """
        if reset_stale:
            try:
                self._db.reset_stale_running_tasks()
            except Exception:            # 复位失败不该让界面空白
                pass

        rows = []
        for task in self._db.get_all_tasks():
            suffix = task.get('remote_suffix') or '.zip'
            rows.append(TaskRow(
                task_id=task['task_id'],
                status=task['status'],
                base_name=task['base_name'],
                target=f"{task['remote_dir']}/{task['base_name']}{suffix}",
                completed=int(task.get('completed_volumes') or 0),
                total=int(task.get('total_volumes') or 0),
                error=task.get('error_message'),
            ))
        return rows

    def volumes(self, task_id: str) -> List[VolumeRow]:
        return [
            VolumeRow(
                part_number=v['part_number'],
                status=v['status'],
                size_bytes=v.get('size_bytes'),
                md5=v.get('md5'),
                remote_path=v['remote_path'],
            )
            for v in self._db.get_all_volumes(task_id)
        ]

    def summary(self) -> Dict[str, int]:
        """状态栏用：各状态各有多少个任务（键是原始状态值）。"""
        counts: Dict[str, int] = {}
        for task in self._db.get_all_tasks():
            counts[task['status']] = counts.get(task['status'], 0) + 1
        return counts

    def summary_labels(self) -> Dict[str, int]:
        """同上，但键换成中文标签，供界面直接显示。"""
        return {label_for(status): count
                for status, count in self.summary().items()}

    # ---------- 写 ----------

    def delete(self, task_id: str) -> None:
        """只删本地记录，不影响云端文件 —— 与 CLI 的 delete 一致。"""
        self._db.delete_task(task_id)
