import time

import pytest

from taskstore import TaskStore


@pytest.fixture
def store():
    return TaskStore(":memory:")


def test_create_and_get_task(store):
    store.create_task("t1", "uploads/t1.txt", ["wordcloud", "ai_summary"])
    task = store.get_task("t1")

    assert task["status"] == "processing"
    assert task["progress"] == 0.0
    assert task["filepath"] == "uploads/t1.txt"
    assert task["display_options"] == ["wordcloud", "ai_summary"]


def test_get_task_returns_none_for_missing_id(store):
    assert store.get_task("does-not-exist") is None


def test_update_progress(store):
    store.create_task("t1", "uploads/t1.txt", [])
    store.update_progress("t1", 0.5)
    assert store.get_task("t1")["progress"] == 0.5


def test_mark_completed(store):
    store.create_task("t1", "uploads/t1.txt", [])
    store.mark_completed("t1", "uploads/t1_result.json")

    task = store.get_task("t1")
    assert task["status"] == "completed"
    assert task["progress"] == 1.0
    assert task["result_path"] == "uploads/t1_result.json"


def test_mark_error(store):
    store.create_task("t1", "uploads/t1.txt", [])
    store.mark_error("t1", "boom")

    task = store.get_task("t1")
    assert task["status"] == "error"
    assert task["error_message"] == "boom"


def test_delete_task(store):
    store.create_task("t1", "uploads/t1.txt", [])
    store.delete_task("t1")
    assert store.get_task("t1") is None


def test_list_stale_tasks_only_returns_finished_tasks_past_ttl(store):
    store.create_task("still-processing", "a.txt", [])

    store.create_task("old-completed", "b.txt", [])
    store.mark_completed("old-completed", "b_result.json")

    store.create_task("fresh-completed", "c.txt", [])
    store.mark_completed("fresh-completed", "c_result.json")

    # 古いタスクだけTTLを過ぎたことにするため、作成時刻を直接書き換える
    with store._lock:
        store._conn.execute(
            "UPDATE tasks SET created_at = ? WHERE task_id = ?",
            (time.time() - 1000, "old-completed"),
        )
        store._conn.commit()

    stale = store.list_stale_tasks(ttl_seconds=10)

    assert stale == ["old-completed"]
