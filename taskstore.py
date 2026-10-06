# ---------- taskstore.py ----------
"""分析タスクの状態をSQLiteに永続化する。

以前は processing_tasks というプロセス内メモリの dict しか持たず、
サーバーを再起動すると実行中のタスクの情報が丸ごと消えて
「無効なタスクID」としか言えなくなっていた。SQLiteに書くことで
再起動後も状態を確認・掃除できるようにする。
"""
import json
import sqlite3
import threading
import time

STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


class TaskStore:
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    filepath TEXT,
                    result_path TEXT,
                    display_options TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL
                )
            """)
            self._conn.commit()

    def create_task(self, task_id, filepath, display_options):
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, status, progress, filepath, display_options, created_at) "
                "VALUES (?, ?, 0.0, ?, ?, ?)",
                (task_id, STATUS_PROCESSING, filepath, json.dumps(display_options), time.time()),
            )
            self._conn.commit()

    def update_progress(self, task_id, progress):
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET progress = ? WHERE task_id = ?", (progress, task_id)
            )
            self._conn.commit()

    def mark_completed(self, task_id, result_path):
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = ?, progress = 1.0, result_path = ? WHERE task_id = ?",
                (STATUS_COMPLETED, result_path, task_id),
            )
            self._conn.commit()

    def mark_error(self, task_id, message):
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = ?, error_message = ? WHERE task_id = ?",
                (STATUS_ERROR, message, task_id),
            )
            self._conn.commit()

    def get_task(self, task_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id, status, progress, filepath, result_path, display_options, "
                "error_message, created_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()

        if not row:
            return None

        return {
            "task_id": row[0],
            "status": row[1],
            "progress": row[2],
            "filepath": row[3],
            "result_path": row[4],
            "display_options": json.loads(row[5]) if row[5] else [],
            "error_message": row[6],
            "created_at": row[7],
        }

    def delete_task(self, task_id):
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            self._conn.commit()

    def list_stale_tasks(self, ttl_seconds):
        """完了/エラーで確定し、TTLを過ぎたタスクIDを返す。"""
        cutoff = time.time() - ttl_seconds
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id FROM tasks WHERE created_at < ? AND status IN (?, ?)",
                (cutoff, STATUS_COMPLETED, STATUS_ERROR),
            ).fetchall()
        return [r[0] for r in rows]
