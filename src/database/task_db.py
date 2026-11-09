# src/database/task_db.py
import sqlite3
import json
import time
import threading
from typing import List, Dict, Optional, Any
from enum import Enum
from pathlib import Path
from src.logger import get_logger

logger = get_logger(__name__)


class TaskStatus(Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    PARTIAL = 'partial'
    PAUSED = 'paused'
    INTERRUPTED = 'interrupted'   # 进程异常退出后由下次启动复位而来


class TaskDB:
    _write_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            home = Path.home()
            db_dir = home / ".netdisk_uploader"
            db_dir.mkdir(exist_ok=True)
            db_path = db_dir / "tasks.db"
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    source_dir TEXT NOT NULL,
                    file_list_json TEXT NOT NULL,
                    remote_dir TEXT NOT NULL,
                    base_name TEXT NOT NULL,
                    volume_size_bytes INTEGER NOT NULL,
                    remote_suffix TEXT NOT NULL DEFAULT '.zip',
                    total_volumes INTEGER DEFAULT 0,
                    completed_volumes INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    fixed_modified_at REAL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS volumes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    part_number INTEGER NOT NULL,
                    remote_path TEXT NOT NULL,
                    local_temp_path TEXT,
                    md5 TEXT,
                    status TEXT NOT NULL,
                    uploaded_size INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_volumes_task_id ON volumes(task_id);
                CREATE INDEX IF NOT EXISTS idx_volumes_status ON volumes(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn) -> None:
        """
        给老库补列。CREATE TABLE IF NOT EXISTS 不会改动已存在的表，
        所以新增字段必须显式 ALTER。
        """
        wanted = {
            'tasks': [
                ('fingerprint', 'TEXT'),        # 源文件清单指纹
                ('entries_json', 'TEXT'),       # 清单原文，用于续传时列出差异
                ('format_version', 'INTEGER'),  # 1 = 无 manifest，2 = 有
                ('encrypted', 'INTEGER'),       # 1 = 归档用 AES 加密
                ('password_tag', 'TEXT'),       # 密码校验值，不含密码本身
                ('layout', "TEXT DEFAULT 'continuous'"),  # 分卷布局
            ],
            'volumes': [
                ('size_bytes', 'INTEGER'),      # 该卷字节数
                ('start_offset', 'INTEGER'),    # 该卷在整条流中的起始偏移
            ],
        }
        for table, columns in wanted.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    logger.debug(f"迁移：{table} 新增列 {name} {decl}")

    # ---------- 任务操作 ----------

    def create_task(self, task_id: str, source_dir: str, file_list: List[str],
                    remote_dir: str, base_name: str, volume_size_bytes: int,
                    remote_suffix: str = ".zip",
                    fingerprint: Optional[str] = None,
                    entries_json: Optional[str] = None,
                    format_version: int = 1,
                    encrypted: bool = False,
                    password_tag: Optional[str] = None,
                    layout: str = "continuous") -> None:
        now = time.time()
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO tasks
                    (task_id, source_dir, file_list_json, remote_dir, base_name,
                     volume_size_bytes, remote_suffix, status,
                     created_at, updated_at, fixed_modified_at,
                     fingerprint, entries_json, format_version,
                     encrypted, password_tag, layout)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task_id, source_dir, json.dumps(file_list),
                    remote_dir, base_name, volume_size_bytes, remote_suffix,
                    TaskStatus.PENDING.value, now, now, now,
                    fingerprint, entries_json, format_version,
                    1 if encrypted else 0, password_tag, layout
                ))

    def update_task_fingerprint(self, task_id: str, fingerprint: str,
                                entries_json: Optional[str] = None) -> None:
        """给升级前的老任务补记指纹基线（只在缺失时调用）。"""
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("""
                    UPDATE tasks SET fingerprint = ?, entries_json = ?, updated_at = ?
                    WHERE task_id = ?
                """, (fingerprint, entries_json, time.time(), task_id))

    def update_task_status(self, task_id: str, status: TaskStatus,
                           error_message: Optional[str] = None) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("""
                    UPDATE tasks
                    SET status = ?, updated_at = ?, error_message = ?
                    WHERE task_id = ?
                """, (status.value, time.time(), error_message, task_id))

    def update_task_progress(self, task_id: str, completed_volumes: int,
                             total_volumes: Optional[int] = None) -> None:
        with self._write_lock:
            with self._connect() as conn:
                if total_volumes is not None:
                    conn.execute(
                        "UPDATE tasks SET completed_volumes = ?, total_volumes = ?, "
                        "updated_at = ? WHERE task_id = ?",
                        (completed_volumes, total_volumes, time.time(), task_id)
                    )
                else:
                    conn.execute(
                        "UPDATE tasks SET completed_volumes = ?, updated_at = ? "
                        "WHERE task_id = ?",
                        (completed_volumes, time.time(), task_id)
                    )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if row:
                result = dict(row)
                result['file_list'] = json.loads(result['file_list_json'])
                del result['file_list_json']
                return result
        return None

    def get_all_tasks(self, status: Optional[TaskStatus] = None) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                cur = conn.execute("SELECT * FROM tasks WHERE status = ?", (status.value,))
            else:
                cur = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")
            rows = cur.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d['file_list'] = json.loads(d['file_list_json'])
                del d['file_list_json']
                result.append(d)
            return result

    # ---------- 分卷操作 ----------

    def add_volume(self, task_id: str, part_number: int, remote_path: str,
                   local_temp_path: Optional[str] = None,
                   md5: Optional[str] = None) -> None:
        now = time.time()
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO volumes
                    (task_id, part_number, remote_path, local_temp_path, md5,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task_id, part_number, remote_path, local_temp_path, md5,
                    'pending', now, now
                ))

    def upsert_volume(self, task_id: str, part_number: int, remote_path: str,
                      local_temp_path: Optional[str] = None,
                      md5: Optional[str] = None,
                      size_bytes: Optional[int] = None,
                      start_offset: Optional[int] = None) -> None:
        """
        写入（或更新）一条分卷记录。

        压缩器在写出每一卷时就调用它，此时上传线程还没开始，所以记录先以
        pending 状态落库；上传线程随后只会更新 status。
        """
        now = time.time()
        with self._write_lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM volumes WHERE task_id = ? AND part_number = ?",
                    (task_id, part_number)
                ).fetchone()
                if row is None:
                    conn.execute("""
                        INSERT INTO volumes
                        (task_id, part_number, remote_path, local_temp_path, md5,
                         size_bytes, start_offset, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (task_id, part_number, remote_path, local_temp_path, md5,
                          size_bytes, start_offset, 'pending', now, now))
                else:
                    conn.execute("""
                        UPDATE volumes
                        SET remote_path = ?, local_temp_path = ?, md5 = ?,
                            size_bytes = ?, start_offset = ?, updated_at = ?
                        WHERE task_id = ? AND part_number = ?
                    """, (remote_path, local_temp_path, md5, size_bytes,
                          start_offset, now, task_id, part_number))

    def get_volume(self, task_id: str, part_number: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM volumes WHERE task_id = ? AND part_number = ?",
                (task_id, part_number)
            ).fetchone()
            return dict(row) if row else None

    def update_volume_status(self, task_id: str, part_number: int,
                             status: str,
                             uploaded_size: Optional[int] = None) -> None:
        with self._write_lock:
            with self._connect() as conn:
                if uploaded_size is not None:
                    conn.execute("""
                        UPDATE volumes
                        SET status = ?, uploaded_size = ?, updated_at = ?
                        WHERE task_id = ? AND part_number = ?
                    """, (status, uploaded_size, time.time(), task_id, part_number))
                else:
                    conn.execute("""
                        UPDATE volumes
                        SET status = ?, updated_at = ?
                        WHERE task_id = ? AND part_number = ?
                    """, (status, time.time(), task_id, part_number))

    def get_volume_status(self, task_id: str, part_number: int) -> Optional[str]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT status FROM volumes WHERE task_id = ? AND part_number = ?",
                (task_id, part_number)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_completed_part_numbers(self, task_id: str) -> List[int]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT part_number FROM volumes "
                "WHERE task_id = ? AND status = 'done' ORDER BY part_number",
                (task_id,)
            )
            return [row[0] for row in cur.fetchall()]

    def get_failed_part_numbers(self, task_id: str) -> List[int]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT part_number FROM volumes "
                "WHERE task_id = ? AND status = 'failed' ORDER BY part_number",
                (task_id,)
            )
            return [row[0] for row in cur.fetchall()]

    def get_all_volumes(self, task_id: str) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM volumes WHERE task_id = ? ORDER BY part_number",
                (task_id,)
            )
            return [dict(row) for row in cur.fetchall()]

    def mark_volume_pending(self, task_id: str, part_number: int) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE volumes SET status = 'pending', updated_at = ? "
                    "WHERE task_id = ? AND part_number = ?",
                    (time.time(), task_id, part_number)
                )

    def mark_all_volumes_pending(self, task_id: str) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE volumes SET status = 'pending', updated_at = ? "
                    "WHERE task_id = ?",
                    (time.time(), task_id)
                )

    # ---------- 启动复位 ----------

    def reset_stale_running_tasks(self) -> int:
        """
        把所有停在 running 的任务标记为 interrupted，返回受影响的行数。

        进程被强杀或崩溃时任务状态会永久停在 running，之后 `list` 一直显示
        running，用户无法分辨"还在跑"和"早就死了"。每次启动时复位一次。

        前提：CLI 是单任务模型（同一时刻只跑一个任务），因此不存在"另一个
        进程正在跑这个任务"的情况。
        """
        with self._write_lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE status = ?",
                    (TaskStatus.INTERRUPTED.value, time.time(),
                     TaskStatus.RUNNING.value)
                )
                return cur.rowcount

    def delete_task(self, task_id: str) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))