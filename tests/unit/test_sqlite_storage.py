import multiprocessing
import os
import platform
import random
import sqlite3
import tempfile
import time
from pathlib import Path

import orjson
import pytest

from trackio.sqlite_storage import SQLiteStorage
from trackio.utils import get_db_path


def test_init_creates_metrics_table(temp_dir):
    db_path = SQLiteStorage.init_db(get_db_path(temp_dir / "proj1"))
    assert os.path.exists(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM metrics")


def test_log_and_get_metrics(temp_dir):
    db_path = get_db_path(temp_dir / "proj1")
    metrics = {"acc": 0.9}
    SQLiteStorage.bulk_log(db_path=db_path, run="run1", metrics_list=[metrics])
    results = SQLiteStorage.get_logs(db_path=db_path, run="run1")
    assert len(results) == 1
    assert results[0]["acc"] == 0.9
    assert results[0]["step"] == 0
    assert "timestamp" in results[0]


def test_get_logs_scalar_only_excludes_heavy_values(temp_dir):
    db_path = get_db_path(temp_dir / "proj1")
    metrics = {
        "acc": 0.9,
        "count": 3,
        "note": "not plotted",
        "table": {
            "_type": "trackio.table",
            "_value": [{"prompt": "x" * 10_000}],
        },
    }
    SQLiteStorage.bulk_log(db_path=db_path, run="run1", metrics_list=[metrics])

    results = SQLiteStorage.get_logs(db_path=db_path, run="run1", scalar_only=True)

    assert results == [
        {
            "acc": 0.9,
            "count": 3,
            "timestamp": results[0]["timestamp"],
            "step": 0,
        }
    ]


def test_get_runs(temp_dir):
    db_path = get_db_path(temp_dir / "proj1")
    SQLiteStorage.bulk_log(db_path=db_path, run="run1", metrics_list=[{"a": 1}])
    SQLiteStorage.bulk_log(db_path=db_path, run="run2", metrics_list=[{"b": 2}])
    runs = set(SQLiteStorage.get_runs(db_path))
    assert runs == {"run1", "run2"}


def test_storage_connection_context_closes_connection(temp_dir):
    db_path = SQLiteStorage.init_db(get_db_path(temp_dir / "proj1"))
    with SQLiteStorage._get_connection(db_path) as conn:
        conn.execute("SELECT 1").fetchone()
    # Confirming that Trackio's _get_connection() closes the connection on exiting the context manager.
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_delete_run(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    run_name = "test_run"
    config = {"param1": "value1", "_Created": "2023-01-01T00:00:00"}
    metrics = [{"accuracy": 0.95, "loss": 0.1}]
    SQLiteStorage.bulk_log(db_path, run_name, metrics, config=config)

    assert SQLiteStorage.get_run_config(db_path, run_name) is not None
    assert len(SQLiteStorage.get_logs(db_path, run_name)) > 0

    SQLiteStorage.delete_run(db_path, run_name)
    assert SQLiteStorage.get_run_config(db_path, run_name) is None
    assert len(SQLiteStorage.get_logs(db_path, run_name)) == 0


def _worker_using_sqlite_storage(
    db_path, worker_id, duration_seconds=2, sync_start_time=None
):
    """
    Worker that uses SQLiteStorage methods for database access.
    This will be protected by ProcessLock when available.
    """
    if sync_start_time:
        while time.time() < sync_start_time:
            time.sleep(0.001)

    run_name = f"worker_{worker_id}"
    db_locked_errors = 0

    start_time = time.time()
    while time.time() - start_time < duration_seconds:
        try:
            for _ in range(4):
                batch_size = random.randint(3, 8)
                metrics_list = [
                    {"batch": True, "worker": worker_id, "item": i}
                    for i in range(batch_size)
                ]
                SQLiteStorage.bulk_log(db_path, run_name, metrics_list)

        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg or "database is busy" in error_msg:
                db_locked_errors += 1
                time.sleep(random.uniform(0.0001, 0.001))
        except Exception:
            pass

    return db_locked_errors


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Windows multiprocessing has different behavior",
)
def test_concurrent_database_access_without_errors():
    """
    Test that concurrent database access doesn't produce 'database is locked' errors.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = get_db_path(Path(tmp) / "concurrent_test")

        num_processes = 8
        duration = 2

        sync_start_time = time.time() + 0.5

        with multiprocessing.Pool(processes=num_processes) as pool:
            results = [
                pool.apply_async(
                    _worker_using_sqlite_storage,
                    (str(db_path), i, duration, sync_start_time),
                )
                for i in range(num_processes)
            ]

            total_db_locked_errors = 0

            for result in results:
                db_locked = result.get(timeout=duration + 10)
                total_db_locked_errors += db_locked

        print(f"Database locked errors: {total_db_locked_errors}")

        assert total_db_locked_errors == 0, (
            f"Got {total_db_locked_errors} 'database is locked' errors - ProcessLock fix failed"
        )

        runs = SQLiteStorage.get_runs(db_path)
        assert len(runs) > 0, "Should have created some runs"
        total_logs = 0
        for run in runs:
            logs = SQLiteStorage.get_logs(db_path, run)
            total_logs += len(logs)

        assert total_logs > 0, "Should have created some log entries"


def test_config_storage_in_database(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    config = {
        "epochs": 10,
        "_Username": "testuser",
        "_Created": "2024-01-01T00:00:00+00:00",
    }

    SQLiteStorage.bulk_log(
        db_path=db_path,
        run="test_run",
        metrics_list=[{"loss": 0.5}],
        config=config,
    )

    stored_config = SQLiteStorage.get_run_config(db_path, "test_run")
    assert stored_config["epochs"] == 10
    assert stored_config["_Username"] == "testuser"
    assert stored_config["_Created"] == "2024-01-01T00:00:00+00:00"


def test_database_without_configs_table(temp_dir):
    db_path = get_db_path(temp_dir / "test")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                run_name TEXT,
                run_id TEXT,
                step INTEGER,
                metrics TEXT
            )
        """)
        conn.execute(
            "INSERT INTO metrics (timestamp, run_name, run_id, step, metrics) VALUES (?, ?, ?, ?, ?)",
            ("2024-01-01", "test_run", "test_run", 0, orjson.dumps({"loss": 0.5})),
        )

    config = SQLiteStorage.get_run_config(db_path, "test_run")
    assert config is None

    all_configs = SQLiteStorage.get_all_run_configs(db_path)
    assert all_configs == {}


def test_get_runs_returns_chronological_order(temp_dir):
    db_path = get_db_path(temp_dir / "proj")

    SQLiteStorage.bulk_log(
        db_path,
        "run-z",
        [{"loss": 0.5}],
        timestamps=["2024-01-01T00:00:00+00:00"],
    )
    SQLiteStorage.bulk_log(
        db_path,
        "run-a",
        [{"loss": 0.5}],
        timestamps=["2024-01-02T00:00:00+00:00"],
    )
    SQLiteStorage.bulk_log(
        db_path,
        "run-m",
        [{"loss": 0.5}],
        timestamps=["2024-01-03T00:00:00+00:00"],
    )

    runs = SQLiteStorage.get_runs(db_path)
    assert runs == ["run-z", "run-a", "run-m"]


def test_get_metric_values_respects_run_id_and_name_resolves_latest_run(temp_dir):
    db_path = get_db_path(temp_dir / "proj_metric_values")
    run_name = "dup-run"

    SQLiteStorage.bulk_log(
        db_path,
        run_name,
        [{"loss": 1.0}],
        run_id="run-id-1",
        timestamps=["2024-01-01T00:00:00+00:00"],
    )
    SQLiteStorage.bulk_log(
        db_path,
        run_name,
        [{"loss": 2.0}],
        run_id="run-id-2",
        timestamps=["2024-01-02T00:00:00+00:00"],
    )

    latest_by_name = SQLiteStorage.get_metric_values(db_path, run_name, "loss")
    first_by_id = SQLiteStorage.get_metric_values(
        db_path, run_name, "loss", run_id="run-id-1"
    )
    second_by_id = SQLiteStorage.get_metric_values(
        db_path, run_name, "loss", run_id="run-id-2"
    )

    assert [row["value"] for row in latest_by_name] == [2.0]
    assert [row["value"] for row in first_by_id] == [1.0]
    assert [row["value"] for row in second_by_id] == [2.0]


def test_rename_run(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    old_name = "old_run"
    new_name = "new_run"

    config = {"param1": "value1", "_Created": "2023-01-01T00:00:00"}
    metrics = [{"accuracy": 0.95, "loss": 0.1}]
    SQLiteStorage.bulk_log(db_path, old_name, metrics, config=config)

    assert SQLiteStorage.get_run_config(db_path, old_name) is not None
    assert len(SQLiteStorage.get_logs(db_path, old_name)) > 0

    SQLiteStorage.rename_run(db_path, old_name, new_name)

    assert SQLiteStorage.get_run_config(db_path, old_name) is None
    assert len(SQLiteStorage.get_logs(db_path, old_name)) == 0

    assert SQLiteStorage.get_run_config(db_path, new_name) is not None
    assert len(SQLiteStorage.get_logs(db_path, new_name)) > 0

    new_logs = SQLiteStorage.get_logs(db_path, new_name)
    assert new_logs[0]["accuracy"] == 0.95
    assert new_logs[0]["loss"] == 0.1


def test_rename_run_allows_duplicate_name_in_new_schema(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    run1 = "run1"
    run2 = "run2"

    SQLiteStorage.bulk_log(db_path, run1, [{"a": 1}])
    SQLiteStorage.bulk_log(db_path, run2, [{"b": 2}])

    SQLiteStorage.rename_run(db_path, run1, run2)

    records = SQLiteStorage.get_run_records(db_path)
    duplicate_names = [record for record in records if record["name"] == run2]
    assert len(duplicate_names) == 2


def test_rename_run_with_media(temp_dir):
    from trackio.utils import media_dir

    project_dir = temp_dir / "proj"
    db_path = get_db_path(project_dir)
    old_name = "old_run"
    new_name = "new_run"

    old_media_dir = media_dir(project_dir) / old_name
    old_media_dir.mkdir(parents=True, exist_ok=True)
    test_file = old_media_dir / "test.txt"
    test_file.write_text("test content")

    metrics = [
        {
            "image": {
                "_type": "trackio.image",
                "file_path": f"{old_name}/test.txt",
                "caption": "test",
            }
        }
    ]
    SQLiteStorage.bulk_log(db_path, old_name, metrics)

    SQLiteStorage.rename_run(db_path, old_name, new_name)

    new_media_dir = media_dir(project_dir) / new_name
    assert new_media_dir.exists()
    assert (new_media_dir / "test.txt").exists()

    assert not old_media_dir.exists()

    new_logs = SQLiteStorage.get_logs(db_path, new_name)
    assert len(new_logs) > 0
    assert "image" in new_logs[0]
    assert new_logs[0]["image"]["file_path"].startswith(f"{new_name}/")


def test_rename_run_nonexistent(temp_dir):
    db_path = get_db_path(temp_dir / "proj")

    with pytest.raises(ValueError, match="does not exist"):
        SQLiteStorage.rename_run(db_path, "nonexistent_run", "new_run")


def test_rename_run_empty_name(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    old_name = "old_run"

    SQLiteStorage.bulk_log(db_path, old_name, [{"a": 1}])

    with pytest.raises(ValueError, match="cannot be empty"):
        SQLiteStorage.rename_run(db_path, old_name, "")

    with pytest.raises(ValueError, match="cannot be empty"):
        SQLiteStorage.rename_run(db_path, old_name, "   ")

    assert len(SQLiteStorage.get_logs(db_path, old_name)) > 0


def test_rename_run_with_system_metrics(temp_dir):
    db_path = get_db_path(temp_dir / "proj")
    old_name = "old_run"
    new_name = "new_run"

    metrics = [{"accuracy": 0.95}]
    SQLiteStorage.bulk_log(db_path, old_name, metrics)

    system_metrics = [{"gpu_usage": 80.5}]
    SQLiteStorage.bulk_log_system(db_path, old_name, system_metrics)

    SQLiteStorage.rename_run(db_path, old_name, new_name)

    assert len(SQLiteStorage.get_logs(db_path, new_name)) > 0
    assert len(SQLiteStorage.get_system_logs(db_path, new_name)) > 0
    assert len(SQLiteStorage.get_system_logs(db_path, old_name)) == 0

    new_system_logs = SQLiteStorage.get_system_logs(db_path, new_name)
    assert new_system_logs[0]["gpu_usage"] == 80.5


def test_query_allows_select(temp_dir):
    db_path = get_db_path(temp_dir / "qproj")
    SQLiteStorage.bulk_log(db_path=db_path, run="r1", metrics_list=[{"acc": 0.9}])
    result = SQLiteStorage.query(db_path, "SELECT run_name FROM metrics")
    assert result["columns"] == ["run_name"]
    assert result["row_count"] == 1
    assert result["rows"][0]["run_name"] == "r1"


def test_query_allows_with_and_safe_pragma(temp_dir):
    db_path = get_db_path(temp_dir / "qproj")
    SQLiteStorage.bulk_log(db_path=db_path, run="r1", metrics_list=[{"acc": 0.9}])
    cte = SQLiteStorage.query(db_path, "WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    assert cte["rows"] == [{"x": 1}]
    pragma = SQLiteStorage.query(db_path, "PRAGMA table_info(metrics)")
    assert pragma["row_count"] > 0


def test_query_denies_writes(temp_dir):
    db_path = get_db_path(temp_dir / "qproj")
    SQLiteStorage.bulk_log(db_path=db_path, run="r1", metrics_list=[{"acc": 0.9}])
    for bad in [
        "INSERT INTO metrics (run_name, step, metrics, timestamp) VALUES ('y',0,'{}','t')",
        "UPDATE metrics SET run_name='x'",
        "DELETE FROM metrics",
        "DROP TABLE metrics",
        "PRAGMA journal_mode = DELETE",
    ]:
        with pytest.raises(ValueError):
            SQLiteStorage.query(db_path, bad)


def test_query_row_limit(temp_dir):
    db_path = get_db_path(temp_dir / "qproj")
    for i in range(5):
        SQLiteStorage.bulk_log(db_path=db_path, run=f"r{i}", metrics_list=[{"a": i}])
    with pytest.raises(ValueError, match="more than"):
        SQLiteStorage.query(db_path, "SELECT * FROM metrics", max_rows=2)


def test_query_normalizes_bytes(temp_dir):
    db_path = get_db_path(temp_dir / "qproj")
    SQLiteStorage.bulk_log(db_path=db_path, run="r1", metrics_list=[{"a": 1}])
    result = SQLiteStorage.query(db_path, "SELECT randomblob(4) AS b")
    assert isinstance(result["rows"][0]["b"], str)
    assert len(result["rows"][0]["b"]) == 8


def test_query_missing_project(temp_dir):
    with pytest.raises(FileNotFoundError):
        SQLiteStorage.query(get_db_path(temp_dir / "nonexistent"), "SELECT 1")


def test_get_logs_keeps_all_eval_points_when_subsampling(temp_dir):
    db_path = get_db_path(temp_dir / "eval_keep")
    metrics_list = []
    steps = []
    timestamps = []
    eval_recon = []
    eval_acc = []
    for step in range(40):
        train_metrics = {"train/loss": float(step)}
        if step == 1:
            train_metrics["train/warmup"] = 1.0
        metrics_list.append(train_metrics)
        steps.append(step)
        timestamps.append(f"2026-01-01T00:{step:02d}:00+00:00")
        if step % 10 == 0:
            recon = float(step) + 0.5
            acc = 0.1 + step / 100.0
            eval_recon.append(recon)
            eval_acc.append(acc)
            metrics_list.append(
                {"eval/recon_loss": recon, "eval/disc_accuracy": acc}
            )
            steps.append(step)
            timestamps.append(f"2026-01-01T00:{step:02d}:30+00:00")

    SQLiteStorage.bulk_log(
        db_path=db_path,
        run="mel_ae",
        metrics_list=metrics_list,
        steps=steps,
        timestamps=timestamps,
    )

    logs = SQLiteStorage.get_logs(db_path, run="mel_ae", max_points=8)
    assert [row["eval/recon_loss"] for row in logs if "eval/recon_loss" in row] == eval_recon
    assert [
        row["eval/disc_accuracy"] for row in logs if "eval/disc_accuracy" in row
    ] == eval_acc
    assert any("train/loss" in row for row in logs)
    assert len(logs) <= 8 + 1 + len(eval_recon)

    batch = SQLiteStorage.get_logs_batch(
        db_path,
        runs=[{"run": "mel_ae"}],
        max_points=8,
        scalar_only=True,
    )
    assert len(batch) == 1
    assert set(batch[0]["metrics"]) == {
        "train/loss",
        "train/warmup",
        "eval/recon_loss",
        "eval/disc_accuracy",
    }
    assert [
        row["eval/recon_loss"]
        for row in batch[0]["logs"]
        if "eval/recon_loss" in row
    ] == eval_recon
