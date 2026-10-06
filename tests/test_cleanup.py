import os
import time

import pytest

from cleanup import cleanup_once
from taskstore import TaskStore


@pytest.fixture
def store():
    return TaskStore(":memory:")


def test_cleanup_removes_stale_completed_task_and_its_files(store, tmp_path):
    upload_file = tmp_path / "t1.txt"
    result_file = tmp_path / "t1_result.json"
    upload_file.write_text("dummy")
    result_file.write_text("{}")

    store.create_task("t1", str(upload_file), [])
    store.mark_completed("t1", str(result_file))

    with store._lock:
        store._conn.execute(
            "UPDATE tasks SET created_at = ? WHERE task_id = ?",
            (time.time() - 1000, "t1"),
        )
        store._conn.commit()

    cleanup_once(store, str(tmp_path), ttl_seconds=10)

    assert store.get_task("t1") is None
    assert not upload_file.exists()
    assert not result_file.exists()


def test_cleanup_keeps_fresh_task(store, tmp_path):
    upload_file = tmp_path / "t2.txt"
    upload_file.write_text("dummy")

    store.create_task("t2", str(upload_file), [])
    store.mark_completed("t2", str(upload_file))

    cleanup_once(store, str(tmp_path), ttl_seconds=3600)

    assert store.get_task("t2") is not None
    assert upload_file.exists()


def test_cleanup_sweeps_orphan_files_not_tracked_in_db(store, tmp_path):
    orphan = tmp_path / "orphan.txt"
    orphan.write_text("leftover")
    old_time = time.time() - 1000
    os.utime(orphan, (old_time, old_time))

    cleanup_once(store, str(tmp_path), ttl_seconds=10)

    assert not orphan.exists()
