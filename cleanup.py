# ---------- cleanup.py ----------
"""アップロードされたファイル・解析結果・処理状況を定期的に片付ける。

これが無いと uploads/ と processing_tasks が無制限に増え続けてしまうため、
バックグラウンドスレッドから定期実行する想定。
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 1800  # 30分おき


def cleanup_once(processing_tasks, upload_folder, ttl_seconds):
    """TTLを過ぎた処理状況とアップロードファイルを削除する。"""
    now = time.time()

    stale_task_ids = [
        task_id for task_id, task in list(processing_tasks.items())
        if task['future'].done() and now - task['start_time'] > ttl_seconds
    ]
    for task_id in stale_task_ids:
        del processing_tasks[task_id]

    if stale_task_ids:
        logger.info(f"期限切れタスクを削除: {len(stale_task_ids)}件")

    if not os.path.isdir(upload_folder):
        return

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


def cleanup_loop(processing_tasks, upload_folder, ttl_seconds, interval_seconds=CLEANUP_INTERVAL_SECONDS):
    """バックグラウンドスレッドで定期的に cleanup_once を実行する。"""
    while True:
        time.sleep(interval_seconds)
        try:
            cleanup_once(processing_tasks, upload_folder, ttl_seconds)
        except Exception as e:
            logger.error(f"クリーンアップ処理でエラー: {e}")
