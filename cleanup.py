# ---------- cleanup.py ----------
"""アップロードされたファイル・解析結果・タスク状態を定期的に片付ける。

これが無いと uploads/ と タスクDB が無制限に増え続けてしまうため、
バックグラウンドスレッドから定期実行する想定。
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 1800  # 30分おき


def cleanup_once(task_store, upload_folder, ttl_seconds):
    """TTLを過ぎたタスクとその関連ファイルを削除する。"""
    stale_task_ids = task_store.list_stale_tasks(ttl_seconds)

    for task_id in stale_task_ids:
        task = task_store.get_task(task_id)
        if task:
            for path in (task.get('filepath'), task.get('result_path')):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as e:
                        logger.warning(f"ファイル削除失敗 [{path}]: {e}")
        task_store.delete_task(task_id)

    if stale_task_ids:
        logger.info(f"期限切れタスクを削除: {len(stale_task_ids)}件")

    # タスクDBに記録が無い孤立ファイル（移行前の残留物など）も
    # mtimeベースで掃除する安全網
    if not os.path.isdir(upload_folder):
        return

    now = time.time()
    removed = 0
    for filename in os.listdir(upload_folder):
        filepath = os.path.join(upload_folder, filename)
        try:
            if now - os.path.getmtime(filepath) > ttl_seconds:
                os.remove(filepath)
                removed += 1
        except OSError as e:
            logger.warning(f"ファイル削除失敗 [{filename}]: {e}")

    if removed:
        logger.info(f"期限切れファイルを削除: {removed}件")


def cleanup_loop(task_store, upload_folder, ttl_seconds, interval_seconds=CLEANUP_INTERVAL_SECONDS):
    """バックグラウンドスレッドで定期的に cleanup_once を実行する。"""
    while True:
        time.sleep(interval_seconds)
        try:
            cleanup_once(task_store, upload_folder, ttl_seconds)
        except Exception as e:
            logger.error(f"クリーンアップ処理でエラー: {e}")
