import hashlib
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

import orjson

from trackio import cas
from trackio.typehints import Manifest, Sha256Digest
from trackio.utils import (
    deserialize_values,
    media_dir,
    serialize_values,
)

_READ_ONLY_QUERY_PREFIXES = ("select", "with", "pragma")
_QUERY_MAX_ROWS = 10_000
_READ_ONLY_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo"}
)

_JOURNAL_MODE_WHITELIST = frozenset(
    {"wal", "delete", "truncate", "persist", "memory", "off"}
)
_SYNCHRONOUS_WHITELIST = frozenset({"off", "normal", "full", "extra"})
_LOCKING_MODE_WHITELIST = frozenset({"normal", "exclusive"})
_TEMP_STORE_WHITELIST = frozenset({"default", "file", "memory"})


def _env_pragma_choice(name: str, whitelist: frozenset[str]) -> str | None:
    value = os.environ.get(name, "").strip().lower()
    if value in whitelist:
        return value.upper()
    return None


def _env_pragma_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _configure_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    override = os.environ.get("TRACKIO_SQLITE_JOURNAL_MODE", "").strip().lower()
    journal = override.upper() if override in _JOURNAL_MODE_WHITELIST else "WAL"
    conn.execute(f"PRAGMA journal_mode = {journal}")
    synchronous = (
        _env_pragma_choice("TRACKIO_SQLITE_SYNCHRONOUS", _SYNCHRONOUS_WHITELIST)
        or "NORMAL"
    )
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    temp_store = (
        _env_pragma_choice("TRACKIO_SQLITE_TEMP_STORE", _TEMP_STORE_WHITELIST)
        or "MEMORY"
    )
    conn.execute(f"PRAGMA temp_store = {temp_store}")
    conn.execute("PRAGMA cache_size = -20000")
    mmap_size = _env_pragma_int("TRACKIO_SQLITE_MMAP_SIZE")
    conn.execute(f"PRAGMA mmap_size = {0 if mmap_size is None else mmap_size}")
    locking_mode = _env_pragma_choice(
        "TRACKIO_SQLITE_LOCKING_MODE", _LOCKING_MODE_WHITELIST
    )
    if locking_mode is not None:
        conn.execute(f"PRAGMA locking_mode = {locking_mode}")


class ProcessLock:
    """File-based lock used to coordinate cross-process database access."""

    def __init__(self, lockfile_path: Path):
        self.lockfile_path = lockfile_path
        self.lockfile = None

    def __enter__(self):
        if fcntl is None and _msvcrt is None:
            return self
        self.lockfile_path.parent.mkdir(parents=True, exist_ok=True)
        self.lockfile = open(self.lockfile_path, "w")

        max_retries = 100
        for attempt in range(max_retries):
            try:
                if fcntl is not None:
                    fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    _msvcrt.locking(self.lockfile.fileno(), _msvcrt.LK_NBLCK, 1)
                return self
            except (IOError, OSError):
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                else:
                    raise IOError("Could not acquire database lock after 10 seconds")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lockfile:
            try:
                if fcntl is not None:
                    fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_UN)
                elif _msvcrt is not None:
                    _msvcrt.locking(self.lockfile.fileno(), _msvcrt.LK_UNLCK, 1)
            except (IOError, OSError):
                pass
            self.lockfile.close()


class SQLiteStorage:
    """SQLite storage for a single project directory.

    Every method takes the path to the project's SQLite database file
    (``<project_dir>/trackio.db``) as its first argument.
    """

    @staticmethod
    @contextmanager
    def _get_connection(
        db_path: Path,
        *,
        timeout: float = 30.0,
        configure_pragmas: bool = True,
        row_factory=sqlite3.Row,
    ) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(db_path), timeout=timeout)
        try:
            if configure_pragmas:
                _configure_sqlite_pragmas(conn)
            if row_factory is not None:
                conn.row_factory = row_factory
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _get_process_lock(db_path: Path) -> ProcessLock:
        return ProcessLock(db_path.with_name(db_path.stem + ".lock"))

    @staticmethod
    def init_db(db_path: Path) -> Path:
        """
        Initialize the SQLite database with required tables.
        Returns the database path.
        """
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path, row_factory=None) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        run_name TEXT NOT NULL,
                        step INTEGER NOT NULL,
                        metrics TEXT NOT NULL,
                        log_id TEXT
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS configs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        run_name TEXT NOT NULL,
                        config TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(run_id)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        run_name TEXT NOT NULL,
                        metrics TEXT NOT NULL,
                        log_id TEXT
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS traces (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        run_name TEXT NOT NULL,
                        step INTEGER NOT NULL,
                        key TEXT NOT NULL,
                        trace_index INTEGER,
                        messages TEXT NOT NULL,
                        metadata TEXT NOT NULL,
                        search_text TEXT NOT NULL,
                        log_id TEXT
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        run_name TEXT NOT NULL,
                        title TEXT NOT NULL,
                        text TEXT,
                        level TEXT NOT NULL DEFAULT 'warn',
                        step INTEGER,
                        alert_id TEXT
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS project_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifacts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        type TEXT NOT NULL,
                        description TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifact_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
                        version INTEGER NOT NULL,
                        manifest_digest TEXT NOT NULL,
                        manifest TEXT NOT NULL,
                        metadata TEXT,
                        size_bytes INTEGER NOT NULL,
                        producer_run_id TEXT,
                        producer_run_name TEXT,
                        created_at TEXT NOT NULL,
                        UNIQUE(artifact_id, version),
                        UNIQUE(artifact_id, manifest_digest)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifact_aliases (
                        artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
                        alias TEXT NOT NULL,
                        artifact_version_id INTEGER NOT NULL REFERENCES artifact_versions(id),
                        PRIMARY KEY (artifact_id, alias)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS run_artifact_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT,
                        run_name TEXT,
                        artifact_version_id INTEGER NOT NULL REFERENCES artifact_versions(id),
                        direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_run_artifact_links_run
                    ON run_artifact_links(run_id, run_name)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_run_artifact_links_version
                    ON run_artifact_links(artifact_version_id)
                    """
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_run_artifact_links_unique
                    ON run_artifact_links(run_id, artifact_version_id, direction)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_metrics_run_step
                    ON metrics(run_id, step)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_metrics_run_timestamp
                    ON metrics(run_id, timestamp)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_configs_run_name
                    ON configs(run_name)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_system_metrics_run_timestamp
                    ON system_metrics(run_id, timestamp)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_traces_run_step
                    ON traces(run_id, step)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_traces_run_timestamp
                    ON traces(run_id, timestamp)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_traces_search
                    ON traces(search_text)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_run
                    ON alerts(run_id)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_timestamp
                    ON alerts(timestamp)
                    """
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_alert_id
                    ON alerts(alert_id) WHERE alert_id IS NOT NULL
                    """
                )
                for table in ("metrics", "system_metrics"):
                    cursor.execute(
                        f"""CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_log_id
                        ON {table}(log_id) WHERE log_id IS NOT NULL"""
                    )
                conn.commit()
        return db_path

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        cursor = conn.cursor()
        try:
            cursor.execute(f"PRAGMA table_info({table})")
        except sqlite3.OperationalError:
            return set()
        return {row[1] for row in cursor.fetchall()}

    @staticmethod
    def _resolve_run_identity(
        conn: sqlite3.Connection,
        run_name: str | None = None,
        run_id: str | None = None,
        *,
        table: str = "metrics",
    ) -> tuple[str, str] | None:
        """Resolve a (run_name, run_id) pair to the concrete row filter to use.

        Returns ("run_id", value) or ("run_name", value), or None when the
        run cannot be found.
        """
        if run_id is not None:
            return ("run_id", run_id)
        if run_name is None:
            return None
        source_table = (
            table
            if "timestamp" in SQLiteStorage._table_columns(conn, table)
            else "metrics"
        )
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT run_id
                FROM {source_table}
                WHERE run_name = ?
                GROUP BY run_id
                ORDER BY MIN(timestamp) DESC
                LIMIT 1
                """,
                (run_name,),
            )
        except sqlite3.OperationalError:
            return None
        row = cursor.fetchone()
        if row is None:
            return None
        return ("run_id", row[0])

    @staticmethod
    def get_run_records(db_path: Path) -> list[dict[str, str | None]]:
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT run_id, run_name, MIN(timestamp) as created_at
                    FROM metrics
                    GROUP BY run_id, run_name
                    ORDER BY created_at ASC
                    """
                )
                return [
                    {
                        "id": row["run_id"],
                        "name": row["run_name"],
                        "created_at": row["created_at"],
                    }
                    for row in cursor.fetchall()
                ]
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return []
            raise

    @staticmethod
    def get_latest_run_record_by_name(
        db_path: Path, run_name: str
    ) -> dict[str, str | None] | None:
        db_path = Path(db_path)
        if not db_path.exists():
            return None

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT run_id, run_name, MIN(timestamp) as created_at
                FROM metrics
                WHERE run_name = ?
                GROUP BY run_id, run_name
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_name,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "id": row["run_id"],
                "name": row["run_name"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def get_runs(db_path: Path) -> list[str]:
        """Get list of all run names, ordered by creation time."""
        return [record["name"] for record in SQLiteStorage.get_run_records(db_path)]

    @staticmethod
    def bulk_log(
        db_path: Path,
        run: str,
        metrics_list: list[dict],
        steps: list[int] | None = None,
        timestamps: list[str] | None = None,
        config: dict | None = None,
        log_ids: list[str] | None = None,
        run_id: str | None = None,
    ):
        """
        Safely log bulk metrics to the database. Before logging, this method will ensure the database exists
        and is set up with the correct tables. It also uses a cross-process lock to prevent
        database locking errors when multiple processes access the same database.
        """
        if not metrics_list:
            return

        if timestamps is None:
            timestamps = [datetime.now(timezone.utc).isoformat()] * len(metrics_list)

        db_path = SQLiteStorage.init_db(db_path)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                resolved_run_id = run_id or run

                if steps is None:
                    steps = list(range(len(metrics_list)))
                elif any(s is None for s in steps):
                    cursor.execute(
                        "SELECT MAX(step) FROM metrics WHERE run_id = ?",
                        (resolved_run_id,),
                    )
                    last_step = cursor.fetchone()[0]
                    current_step = 0 if last_step is None else last_step + 1
                    processed_steps = []
                    for step in steps:
                        if step is None:
                            processed_steps.append(current_step)
                            current_step += 1
                        else:
                            processed_steps.append(step)
                    steps = processed_steps

                if len(metrics_list) != len(steps) or len(metrics_list) != len(
                    timestamps
                ):
                    raise ValueError(
                        "metrics_list, steps, and timestamps must have the same length"
                    )

                data = []
                trace_rows = []
                for i, metrics in enumerate(metrics_list):
                    lid = log_ids[i] if log_ids else None
                    clean_metrics, rows = SQLiteStorage._split_trace_metrics(
                        metrics,
                        run=run,
                        run_id=resolved_run_id,
                        step=steps[i],
                        timestamp=timestamps[i],
                        log_id=lid,
                    )
                    trace_rows.extend(rows)
                    data.append(
                        (
                            timestamps[i],
                            resolved_run_id,
                            run,
                            steps[i],
                            orjson.dumps(serialize_values(clean_metrics)),
                            lid,
                        )
                    )

                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO metrics
                    (timestamp, run_id, run_name, step, metrics, log_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    data,
                )

                SQLiteStorage._insert_trace_rows(cursor, trace_rows)

                if config:
                    current_timestamp = datetime.now(timezone.utc).isoformat()
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO configs
                        (run_id, run_name, config, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            resolved_run_id,
                            run,
                            orjson.dumps(serialize_values(config)),
                            current_timestamp,
                        ),
                    )

                conn.commit()

    @staticmethod
    def bulk_log_system(
        db_path: Path,
        run: str,
        metrics_list: list[dict],
        timestamps: list[str] | None = None,
        log_ids: list[str] | None = None,
        run_id: str | None = None,
    ):
        """
        Log system metrics (GPU, etc.) to the database without step numbers.
        These metrics use timestamps for the x-axis instead of steps.
        """
        if not metrics_list:
            return

        if timestamps is None:
            timestamps = [datetime.now(timezone.utc).isoformat()] * len(metrics_list)

        if len(metrics_list) != len(timestamps):
            raise ValueError("metrics_list and timestamps must have the same length")

        db_path = SQLiteStorage.init_db(db_path)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                resolved_run_id = run_id or run
                data = []
                for i, metrics in enumerate(metrics_list):
                    lid = log_ids[i] if log_ids else None
                    data.append(
                        (
                            timestamps[i],
                            resolved_run_id,
                            run,
                            orjson.dumps(serialize_values(metrics)),
                            lid,
                        )
                    )

                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO system_metrics
                    (timestamp, run_id, run_name, metrics, log_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    data,
                )
                conn.commit()

    @staticmethod
    def bulk_alert(
        db_path: Path,
        run: str,
        titles: list[str],
        texts: list[str | None],
        levels: list[str],
        steps: list[int | None],
        timestamps: list[str] | None = None,
        alert_ids: list[str] | None = None,
        run_id: str | None = None,
    ):
        if not titles:
            return

        if timestamps is None:
            timestamps = [datetime.now(timezone.utc).isoformat()] * len(titles)

        db_path = SQLiteStorage.init_db(db_path)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                resolved_run_id = run_id or run
                data = []
                for i in range(len(titles)):
                    aid = alert_ids[i] if alert_ids else None
                    data.append(
                        (
                            resolved_run_id,
                            timestamps[i],
                            run,
                            titles[i],
                            texts[i],
                            levels[i],
                            steps[i],
                            aid,
                        )
                    )

                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO alerts
                    (run_id, timestamp, run_name, title, text, level, step, alert_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    data,
                )
                conn.commit()

    @staticmethod
    def get_alerts(
        db_path: Path,
        run_name: str | None = None,
        run_id: str | None = None,
        level: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                query = (
                    "SELECT timestamp, run_name, title, text, level, step FROM alerts"
                )
                conditions = []
                params = []
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run_name, run_id=run_id, table="alerts"
                )
                if run_identity is not None:
                    conditions.append(f"{run_identity[0]} = ?")
                    params.append(run_identity[1])
                elif run_name is not None or run_id is not None:
                    return []
                if level is not None:
                    conditions.append("level = ?")
                    params.append(level)
                if since is not None:
                    conditions.append("timestamp > ?")
                    params.append(since)
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                query += " ORDER BY timestamp DESC"
                cursor.execute(query, params)

                rows = cursor.fetchall()
                return [
                    {
                        "timestamp": row["timestamp"],
                        "run": row["run_name"],
                        "title": row["title"],
                        "text": row["text"],
                        "level": row["level"],
                        "step": row["step"],
                    }
                    for row in rows
                ]
            except sqlite3.OperationalError as e:
                if "no such table: alerts" in str(e):
                    return []
                raise

    @staticmethod
    def get_alert_count(db_path: Path) -> int:
        db_path = Path(db_path)
        if not db_path.exists():
            return 0

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM alerts")
                return cursor.fetchone()[0]
            except sqlite3.OperationalError:
                return 0

    @staticmethod
    def _fetch_system_logs_with_cursor(
        cursor: sqlite3.Cursor,
        run_identity: tuple[str, Any],
        max_points: int | None = None,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            f"""
            SELECT timestamp, metrics
            FROM system_metrics
            WHERE {run_identity[0]} = ?
            ORDER BY timestamp
            """,
            (run_identity[1],),
        )
        rows = cursor.fetchall()
        rows = SQLiteStorage._subsample_metric_rows(rows, max_points)
        results = []
        for row in rows:
            metrics = orjson.loads(row["metrics"])
            metrics = deserialize_values(metrics)
            metrics["timestamp"] = row["timestamp"]
            results.append(metrics)
        return results

    @staticmethod
    def get_system_logs(
        db_path: Path,
        run: str | None = None,
        run_id: str | None = None,
        max_points: int | None = None,
    ) -> list[dict]:
        """Retrieve system metrics for a specific run. Returns metrics with timestamps (no steps)."""
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id, table="system_metrics"
                )
                if run_identity is None:
                    return []
                return SQLiteStorage._fetch_system_logs_with_cursor(
                    cursor, run_identity, max_points
                )
        except sqlite3.OperationalError as e:
            if "no such table: system_metrics" in str(e):
                return []
            raise

    @staticmethod
    def get_system_logs_batch(
        db_path: Path,
        runs: list[dict[str, Any]] | None = None,
        max_points: int | None = None,
    ) -> list[dict[str, Any]]:
        if not runs:
            return []
        db_path = Path(db_path)
        if not db_path.exists():
            return [
                {
                    "run": r.get("run"),
                    "run_id": r.get("run_id"),
                    "logs": [],
                }
                for r in runs
            ]

        out: list[dict[str, Any]] = []
        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                for r in runs:
                    run = r.get("run")
                    run_id = r.get("run_id")
                    run_identity = SQLiteStorage._resolve_run_identity(
                        conn, run_name=run, run_id=run_id, table="system_metrics"
                    )
                    if run_identity is None:
                        logs = []
                    else:
                        logs = SQLiteStorage._fetch_system_logs_with_cursor(
                            cursor, run_identity, max_points
                        )
                    out.append(
                        {
                            "run": run,
                            "run_id": run_id,
                            "logs": logs,
                        }
                    )
        except sqlite3.OperationalError as e:
            if "no such table: system_metrics" in str(e):
                return [
                    {
                        "run": r.get("run"),
                        "run_id": r.get("run_id"),
                        "logs": [],
                    }
                    for r in runs
                ]
            raise

        return out

    @staticmethod
    def get_all_system_metrics_for_run(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> list[str]:
        """Get all system metric names for a specific run."""
        return SQLiteStorage._get_metric_names(
            db_path,
            run,
            "system_metrics",
            exclude_keys={"timestamp"},
            run_id=run_id,
        )

    @staticmethod
    def has_system_metrics(db_path: Path) -> bool:
        """Check if the project has any system metrics logged."""
        db_path = Path(db_path)
        if not db_path.exists():
            return False

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM system_metrics LIMIT 1")
                count = cursor.fetchone()[0]
                return count > 0
            except sqlite3.OperationalError:
                return False

    @staticmethod
    def get_log_count(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> int:
        db_path = Path(db_path)
        if not db_path.exists():
            return 0
        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id
                )
                if run_identity is None:
                    return 0
                cursor.execute(
                    f"SELECT COUNT(*) FROM metrics WHERE {run_identity[0]} = ?",
                    (run_identity[1],),
                )
                return cursor.fetchone()[0]
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return 0
            raise

    @staticmethod
    def get_last_step(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> int | None:
        db_path = Path(db_path)
        if not db_path.exists():
            return None
        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id
                )
                if run_identity is None:
                    return None
                cursor.execute(
                    f"SELECT MAX(step) FROM metrics WHERE {run_identity[0]} = ?",
                    (run_identity[1],),
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] is not None else None
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return None
            raise

    @staticmethod
    def get_tab_availability_flags(db_path: Path) -> dict[str, bool]:
        flags = {
            "metrics": False,
            "system": False,
            "traces": False,
            "media": False,
            "reports": False,
            "alerts": False,
        }
        db_path = Path(db_path)
        if not db_path.exists():
            return flags

        def _exists(conn, sql, params=()):
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.fetchone() is not None
            except sqlite3.OperationalError:
                return False

        with SQLiteStorage._get_connection(db_path) as conn:
            flags["metrics"] = _exists(
                conn,
                "SELECT 1 FROM metrics "
                "WHERE CAST(metrics AS TEXT) GLOB '*:[0-9]*' "
                "OR CAST(metrics AS TEXT) GLOB '*:-[0-9]*' "
                "LIMIT 1",
            )
            flags["media"] = _exists(
                conn,
                "SELECT 1 FROM metrics WHERE "
                "CAST(metrics AS TEXT) GLOB ? "
                "OR CAST(metrics AS TEXT) GLOB ? "
                "OR CAST(metrics AS TEXT) GLOB ? "
                "OR CAST(metrics AS TEXT) GLOB ? "
                "LIMIT 1",
                (
                    '*"_type":"trackio.image"*',
                    '*"_type":"trackio.video"*',
                    '*"_type":"trackio.audio"*',
                    '*"_type":"trackio.table"*',
                ),
            )
            flags["reports"] = _exists(
                conn,
                "SELECT 1 FROM metrics WHERE CAST(metrics AS TEXT) GLOB ? LIMIT 1",
                ('*"_type":"trackio.markdown"*',),
            )
            flags["system"] = _exists(conn, "SELECT 1 FROM system_metrics LIMIT 1")
            flags["traces"] = _exists(conn, "SELECT 1 FROM traces LIMIT 1")
            flags["alerts"] = _exists(conn, "SELECT 1 FROM alerts LIMIT 1")
        return flags

    @staticmethod
    def _subsample_metric_rows(rows: list[Any], max_points: int | None) -> list[Any]:
        if max_points is None or max_points < 1:
            return rows
        if len(rows) <= max_points:
            return rows
        step = len(rows) / max_points
        indices = {int(i * step) for i in range(max_points)}
        indices.add(len(rows) - 1)
        return [rows[i] for i in sorted(indices)]

    @staticmethod
    def _metric_rows_to_log_dicts(
        rows: list[Any],
        *,
        scalar_only: bool = False,
    ) -> list[dict[str, Any]]:
        results = []
        for row in rows:
            metrics = orjson.loads(row["metrics"])
            if scalar_only:
                metrics = {
                    key: value
                    for key, value in metrics.items()
                    if isinstance(value, int | float) and not isinstance(value, bool)
                }
            else:
                metrics = deserialize_values(metrics)
            metrics["timestamp"] = row["timestamp"]
            metrics["step"] = row["step"]
            results.append(metrics)
        return results

    @staticmethod
    def _fetch_metric_logs_with_cursor(
        cursor: sqlite3.Cursor,
        run_identity: tuple[str, Any],
        max_points: int | None,
        *,
        scalar_only: bool = False,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            f"""
            SELECT timestamp, step, metrics
            FROM metrics
            WHERE {run_identity[0]} = ?
            ORDER BY timestamp
            """,
            (run_identity[1],),
        )
        rows = cursor.fetchall()
        rows = SQLiteStorage._subsample_metric_rows(rows, max_points)
        return SQLiteStorage._metric_rows_to_log_dicts(rows, scalar_only=scalar_only)

    @staticmethod
    def get_logs(
        db_path: Path,
        run: str | None = None,
        max_points: int | None = None,
        run_id: str | None = None,
        scalar_only: bool = False,
    ) -> list[dict]:
        """Retrieve logs for a specific run. Logs include the step count (int) and the timestamp (datetime object)."""
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id
                )
                if run_identity is None:
                    return []
                return SQLiteStorage._fetch_metric_logs_with_cursor(
                    cursor, run_identity, max_points, scalar_only=scalar_only
                )
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return []
            raise

    @staticmethod
    def get_logs_batch(
        db_path: Path,
        runs: list[dict[str, Any]] | None = None,
        max_points: int | None = None,
        scalar_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not runs:
            return []
        db_path = Path(db_path)
        if not db_path.exists():
            return [
                {
                    "run": r.get("run"),
                    "run_id": r.get("run_id"),
                    "logs": [],
                }
                for r in runs
            ]

        out: list[dict[str, Any]] = []
        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                for r in runs:
                    run = r.get("run")
                    run_id = r.get("run_id")
                    run_identity = SQLiteStorage._resolve_run_identity(
                        conn, run_name=run, run_id=run_id
                    )
                    if run_identity is None:
                        logs = []
                    else:
                        logs = SQLiteStorage._fetch_metric_logs_with_cursor(
                            cursor, run_identity, max_points, scalar_only=scalar_only
                        )
                    out.append(
                        {
                            "run": run,
                            "run_id": run_id,
                            "logs": logs,
                        }
                    )
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return [
                    {
                        "run": r.get("run"),
                        "run_id": r.get("run_id"),
                        "logs": [],
                    }
                    for r in runs
                ]
            raise

        return out

    @staticmethod
    def _is_trace_payload(value: Any) -> bool:
        return isinstance(value, dict) and value.get("_type") == "trackio.trace"

    @staticmethod
    def _split_trace_metrics(
        metrics: dict,
        *,
        run: str,
        run_id: str,
        step: int,
        timestamp: str,
        log_id: str | None,
    ) -> tuple[dict, list[dict[str, Any]]]:
        clean_metrics = {}
        trace_rows: list[dict[str, Any]] = []

        for key, value in metrics.items():
            is_list = isinstance(value, list)
            candidates = value if is_list else [value]
            traces_for_key = [
                (index if is_list else None, candidate)
                for index, candidate in enumerate(candidates)
                if SQLiteStorage._is_trace_payload(candidate)
            ]
            if not traces_for_key:
                clean_metrics[key] = value
                continue

            if is_list:
                non_trace_items = [
                    candidate
                    for candidate in candidates
                    if not SQLiteStorage._is_trace_payload(candidate)
                ]
                if non_trace_items:
                    clean_metrics[key] = non_trace_items

            for trace_index, trace in traces_for_key:
                trace_id_parts = [run_id or run, log_id or uuid.uuid4().hex, key]
                if trace_index is not None:
                    trace_id_parts.append(str(trace_index))
                trace_record = {
                    "id": ":".join(str(part) for part in trace_id_parts),
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "run_name": run,
                    "step": step,
                    "key": key,
                    "trace_index": trace_index,
                    "messages": trace.get("messages", []),
                    "metadata": trace.get("metadata", {}),
                    "log_id": log_id,
                }
                trace_record["search_text"] = (
                    f"{trace_record['id']} {key} "
                    f"{SQLiteStorage._flatten_trace_search_text(trace_record)}"
                ).lower()
                trace_rows.append(trace_record)

        return clean_metrics, trace_rows

    @staticmethod
    def _insert_trace_rows(cursor: sqlite3.Cursor, trace_rows: list[dict[str, Any]]):
        if not trace_rows:
            return
        cursor.executemany(
            """
            INSERT OR IGNORE INTO traces
            (id, run_id, timestamp, run_name, step, key, trace_index, messages, metadata, search_text, log_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["id"],
                    row["run_id"],
                    row["timestamp"],
                    row["run_name"],
                    row["step"],
                    row["key"],
                    row["trace_index"],
                    orjson.dumps(serialize_values(row["messages"])),
                    orjson.dumps(serialize_values(row["metadata"])),
                    row["search_text"],
                    row["log_id"],
                )
                for row in trace_rows
            ],
        )

    @staticmethod
    def _flatten_trace_search_text(trace: dict[str, Any]) -> str:
        parts: list[str] = []

        def visit(value: Any):
            if value is None:
                return
            if isinstance(value, dict):
                for nested in value.values():
                    visit(nested)
                return
            if isinstance(value, list):
                for nested in value:
                    visit(nested)
                return
            parts.append(str(value))

        visit(trace.get("messages", []))
        visit(trace.get("metadata", {}))
        return " ".join(parts).lower()

    @staticmethod
    def _sort_traces(
        traces: list[dict[str, Any]], sort: str | None
    ) -> list[dict[str, Any]]:
        sort_key = sort or "request_time_desc"
        if sort_key == "step_asc":
            return sorted(traces, key=lambda trace: trace.get("step") or 0)
        if sort_key == "step_desc":
            return sorted(
                traces, key=lambda trace: trace.get("step") or 0, reverse=True
            )
        if sort_key == "request_time_asc":
            return sorted(traces, key=lambda trace: trace.get("timestamp") or "")
        return sorted(
            traces, key=lambda trace: trace.get("timestamp") or "", reverse=True
        )

    @staticmethod
    def get_traces(
        db_path: Path,
        run: str | None = None,
        search: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        run_id: str | None = None,
        step: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0
        if limit is not None:
            try:
                limit = max(0, int(limit))
            except (TypeError, ValueError):
                limit = None

        db_path = Path(db_path)
        if not db_path.exists():
            return []

        order_by = {
            "step_asc": "step ASC, timestamp ASC, id ASC",
            "step_desc": "step DESC, timestamp DESC, id DESC",
            "request_time_asc": "timestamp ASC, id ASC",
            "request_time_desc": "timestamp DESC, id DESC",
        }.get(sort or "request_time_desc", "timestamp DESC, id DESC")

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id, table="traces"
                )
                if run_identity is None:
                    return []

                where = [f"{run_identity[0]} = ?"]
                params: list[Any] = [run_identity[1]]
                if step is not None:
                    where.append("step = ?")
                    params.append(step)
                if search:
                    needle = search.strip().lower()
                    if needle:
                        where.append("search_text LIKE ?")
                        params.append(f"%{needle}%")

                query = f"""
                    SELECT id, key, trace_index, run_name, run_id, step, timestamp, messages, metadata
                    FROM traces
                    WHERE {" AND ".join(where)}
                    ORDER BY {order_by}
                """
                if limit is not None:
                    query += " LIMIT ?"
                    params.append(limit)
                if offset > 0:
                    if limit is None:
                        query += " LIMIT -1"
                    query += " OFFSET ?"
                    params.append(offset)

                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table: traces" in str(e):
                return []
            raise

        return [
            {
                "id": row["id"],
                "key": row["key"],
                "index": row["trace_index"],
                "run": row["run_name"],
                "run_id": row["run_id"],
                "step": row["step"],
                "timestamp": row["timestamp"],
                "messages": deserialize_values(orjson.loads(row["messages"])),
                "metadata": deserialize_values(orjson.loads(row["metadata"])),
            }
            for row in rows
        ]

    @staticmethod
    def get_trace_steps(
        db_path: Path,
        run: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return per-step trace counts and total for a run.

        Returns: {"total": int, "steps": [{"step": int, "count": int}, ...]}
        Steps are returned in ascending order.
        """
        db_path = Path(db_path)
        if not db_path.exists():
            return {"total": 0, "steps": []}

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id, table="traces"
                )
                if run_identity is None:
                    return {"total": 0, "steps": []}
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    SELECT step, COUNT(*) AS c
                    FROM traces
                    WHERE {run_identity[0]} = ?
                    GROUP BY step
                    ORDER BY step ASC
                    """,
                    (run_identity[1],),
                )
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table: traces" in str(e):
                return {"total": 0, "steps": []}
            raise

        steps = [{"step": row["step"], "count": row["c"]} for row in rows]
        total = sum(item["count"] for item in steps)
        return {"total": total, "steps": steps}

    @staticmethod
    def _validate_read_only_query(query: str) -> str:
        normalized = query.strip().rstrip(";").strip()
        if not normalized:
            raise ValueError("Query cannot be empty.")
        if not normalized.lower().startswith(_READ_ONLY_QUERY_PREFIXES):
            raise ValueError(
                "Only read-only SELECT, WITH, and safe PRAGMA queries are supported."
            )
        return normalized

    @staticmethod
    def _query_authorizer(
        action_code: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        source: str | None,
    ) -> int:
        del arg2, db_name, source
        if action_code in {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
        }:
            return sqlite3.SQLITE_OK
        pragma_code = getattr(sqlite3, "SQLITE_PRAGMA", None)
        if action_code == pragma_code:
            pragma_name = (arg1 or "").lower()
            if pragma_name in _READ_ONLY_PRAGMAS:
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _normalize_query_value(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).hex()
        return value

    @staticmethod
    def query(
        db_path: Path, query: str, max_rows: int = _QUERY_MAX_ROWS
    ) -> dict[str, Any]:
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"No trackio database found at '{db_path}'.")

        normalized_query = SQLiteStorage._validate_read_only_query(query)
        with SQLiteStorage._get_connection(db_path) as conn:
            conn.set_authorizer(SQLiteStorage._query_authorizer)
            try:
                cursor = conn.cursor()
                cursor.execute(normalized_query)
                description = cursor.description or []
                columns = [column[0] for column in description]
                fetched = cursor.fetchmany(max_rows + 1)
                if len(fetched) > max_rows:
                    raise ValueError(
                        f"Query returned more than {max_rows} rows. "
                        "Refine the query or add a LIMIT clause."
                    )
                rows = [
                    {
                        column: SQLiteStorage._normalize_query_value(row[column])
                        for column in columns
                    }
                    for row in fetched
                ]
            except sqlite3.DatabaseError as e:
                raise ValueError(str(e)) from e
            finally:
                conn.set_authorizer(None)

        return {
            "query": normalized_query,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }

    @staticmethod
    def get_max_steps_for_runs(db_path: Path) -> dict[str, int]:
        """Get the maximum step for each run, keyed by run_id."""
        db_path = Path(db_path)
        if not db_path.exists():
            return {}

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT run_name, run_id, MAX(step) as max_step
                    FROM metrics
                    GROUP BY run_id, run_name
                    """
                )
                results = {}
                for row in cursor.fetchall():
                    results[row["run_id"]] = row["max_step"]
                return results
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return {}
            raise

    @staticmethod
    def get_max_step_for_run(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> int | None:
        """Get the maximum step for a specific run, or None if no logs exist."""
        db_path = Path(db_path)
        if not db_path.exists():
            return None

        try:
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id
                )
                if run_identity is None:
                    return None
                cursor.execute(
                    f"SELECT MAX(step) FROM metrics WHERE {run_identity[0]} = ?",
                    (run_identity[1],),
                )
                result = cursor.fetchone()[0]
                return result
        except sqlite3.OperationalError as e:
            if "no such table: metrics" in str(e):
                return None
            raise

    @staticmethod
    def get_run_config(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> dict | None:
        """Get configuration for a specific run."""
        db_path = Path(db_path)
        if not db_path.exists():
            return None

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id, table="metrics"
                )
                if run_identity is None:
                    return None
                cursor.execute(
                    "SELECT config FROM configs WHERE run_id = ?",
                    (run_identity[1],),
                )

                row = cursor.fetchone()
                if row:
                    config = orjson.loads(row["config"])
                    return deserialize_values(config)
                return None
            except sqlite3.OperationalError as e:
                if "no such table: configs" in str(e):
                    return None
                raise

    @staticmethod
    def delete_run(db_path: Path, run: str, run_id: str | None = None) -> bool:
        """Delete a run from the database (metrics, config, system_metrics, alerts, traces)."""
        db_path = Path(db_path)
        if not db_path.exists():
            return False

        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                try:
                    run_identity = SQLiteStorage._resolve_run_identity(
                        conn, run_name=run, run_id=run_id
                    )
                    if run_identity is None:
                        return False
                    cursor.execute(
                        f"DELETE FROM metrics WHERE {run_identity[0]} = ?",
                        (run_identity[1],),
                    )
                    cursor.execute(
                        "DELETE FROM configs WHERE run_id = ?"
                        if run_identity[0] == "run_id"
                        else "DELETE FROM configs WHERE run_name = ?",
                        (run_identity[1],),
                    )
                    try:
                        cursor.execute(
                            f"DELETE FROM system_metrics WHERE {run_identity[0]} = ?",
                            (run_identity[1],),
                        )
                    except sqlite3.OperationalError:
                        pass
                    try:
                        cursor.execute(
                            f"DELETE FROM alerts WHERE {run_identity[0]} = ?",
                            (run_identity[1],),
                        )
                    except sqlite3.OperationalError:
                        pass
                    try:
                        cursor.execute(
                            f"DELETE FROM traces WHERE {run_identity[0]} = ?",
                            (run_identity[1],),
                        )
                    except sqlite3.OperationalError:
                        pass
                    conn.commit()
                    return True
                except sqlite3.Error:
                    return False

    @staticmethod
    def _update_media_paths(obj, old_prefix, new_prefix):
        """Update media file paths in nested data structures."""
        if isinstance(obj, dict):
            if obj.get("_type") in [
                "trackio.image",
                "trackio.video",
                "trackio.audio",
            ]:
                old_path = obj.get("file_path", "")
                if isinstance(old_path, str):
                    normalized_path = old_path.replace("\\", "/")
                    if normalized_path.startswith(old_prefix):
                        new_path = normalized_path.replace(old_prefix, new_prefix, 1)
                        return {**obj, "file_path": new_path}
            return {
                key: SQLiteStorage._update_media_paths(value, old_prefix, new_prefix)
                for key, value in obj.items()
            }
        elif isinstance(obj, list):
            return [
                SQLiteStorage._update_media_paths(item, old_prefix, new_prefix)
                for item in obj
            ]
        return obj

    @staticmethod
    def _rewrite_trace_rows(
        trace_rows,
        new_run_name,
        old_prefix,
        new_prefix,
    ):
        result = []
        for row in trace_rows:
            messages = deserialize_values(orjson.loads(row["messages"]))
            metadata = deserialize_values(orjson.loads(row["metadata"]))
            messages = SQLiteStorage._update_media_paths(
                messages, old_prefix, new_prefix
            )
            metadata = SQLiteStorage._update_media_paths(
                metadata, old_prefix, new_prefix
            )
            result.append(
                (
                    row["id"],
                    row["run_id"],
                    row["timestamp"],
                    new_run_name,
                    row["step"],
                    row["key"],
                    row["trace_index"],
                    orjson.dumps(serialize_values(messages)),
                    orjson.dumps(serialize_values(metadata)),
                    row["search_text"],
                    row["log_id"],
                )
            )
        return result

    @staticmethod
    def _move_media_dir(source: Path, target: Path):
        """Move a media directory from source to target."""
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(source), str(target))

    @staticmethod
    def rename_run(
        db_path: Path, old_name: str, new_name: str, run_id: str | None = None
    ) -> None:
        """Rename a run.

        Raises:
            ValueError: If the new name is empty, the old run doesn't exist,
                        or a run with the new name already exists.
            RuntimeError: If the database operation fails.
        """
        if not new_name or not new_name.strip():
            raise ValueError("New run name cannot be empty")

        new_name = new_name.strip()

        db_path = Path(db_path)
        if not db_path.exists():
            raise ValueError(
                f"Run '{old_name}' does not exist: no trackio database at '{db_path}'"
            )

        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                cursor = conn.cursor()
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=old_name, run_id=run_id
                )
                if run_identity is None:
                    raise ValueError(f"Run '{old_name}' does not exist")

                run_col, run_value = run_identity

                try:
                    cursor.execute(
                        f"SELECT run_id, timestamp, step, metrics FROM metrics WHERE {run_col} = ?",
                        (run_value,),
                    )
                    metrics_rows = cursor.fetchall()

                    old_prefix = f"{old_name}/"
                    new_prefix = f"{new_name}/"

                    updated_rows = []
                    for row in metrics_rows:
                        metrics_data = orjson.loads(row["metrics"])
                        metrics_deserialized = deserialize_values(metrics_data)
                        updated = SQLiteStorage._update_media_paths(
                            metrics_deserialized, old_prefix, new_prefix
                        )
                        updated_rows.append(
                            (
                                row["run_id"],
                                row["timestamp"],
                                new_name,
                                row["step"],
                                orjson.dumps(serialize_values(updated)),
                            )
                        )

                    cursor.execute(
                        f"DELETE FROM metrics WHERE {run_col} = ?", (run_value,)
                    )
                    cursor.executemany(
                        "INSERT INTO metrics (run_id, timestamp, run_name, step, metrics) VALUES (?, ?, ?, ?, ?)",
                        updated_rows,
                    )

                    cursor.execute(
                        "UPDATE configs SET run_name = ? WHERE run_id = ?"
                        if run_col == "run_id"
                        else "UPDATE configs SET run_name = ? WHERE run_name = ?",
                        (new_name, run_value),
                    )

                    try:
                        cursor.execute(
                            f"UPDATE system_metrics SET run_name = ? WHERE {run_col} = ?",
                            (new_name, run_value),
                        )
                    except sqlite3.OperationalError:
                        pass

                    try:
                        cursor.execute(
                            f"UPDATE alerts SET run_name = ? WHERE {run_col} = ?",
                            (new_name, run_value),
                        )
                    except sqlite3.OperationalError:
                        pass

                    try:
                        cursor.execute(
                            f"""
                            SELECT id, run_id, timestamp, step, key, trace_index, messages, metadata, search_text, log_id
                            FROM traces WHERE {run_col} = ?
                            """,
                            (run_value,),
                        )
                        trace_rows = cursor.fetchall()
                        updated_trace_rows = SQLiteStorage._rewrite_trace_rows(
                            trace_rows,
                            new_name,
                            old_prefix,
                            new_prefix,
                        )
                        cursor.execute(
                            f"DELETE FROM traces WHERE {run_col} = ?", (run_value,)
                        )
                        cursor.executemany(
                            """
                            INSERT INTO traces
                            (id, run_id, timestamp, run_name, step, key, trace_index, messages, metadata, search_text, log_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            updated_trace_rows,
                        )
                    except sqlite3.OperationalError:
                        pass

                    conn.commit()

                    project_dir = db_path.parent
                    SQLiteStorage._move_media_dir(
                        media_dir(project_dir) / old_name,
                        media_dir(project_dir) / new_name,
                    )
                except sqlite3.Error as e:
                    raise RuntimeError(
                        f"Database error while renaming run '{old_name}' to '{new_name}': {e}"
                    ) from e

    @staticmethod
    def get_all_run_configs(db_path: Path) -> dict[str, dict]:
        """Get configurations for all runs, keyed by run_id."""
        db_path = Path(db_path)
        if not db_path.exists():
            return {}

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT run_id, config FROM configs")

                results = {}
                for row in cursor.fetchall():
                    config = orjson.loads(row["config"])
                    results[row["run_id"]] = deserialize_values(config)
                return results
            except sqlite3.OperationalError as e:
                if "no such table: configs" in str(e):
                    return {}
                raise

    @staticmethod
    def get_metric_values(
        db_path: Path,
        run: str | None,
        metric_name: str,
        step: int | None = None,
        around_step: int | None = None,
        at_time: str | None = None,
        window: int | float | None = None,
        run_id: str | None = None,
    ) -> list[dict]:
        """Get values for a specific metric in a run with optional filtering.

        Filtering modes:
          - step: return the single row at exactly this step
          - around_step + window: return rows where step is in [around_step - window, around_step + window]
          - at_time + window: return rows within ±window seconds of the ISO timestamp
          - No filters: return all rows
        """
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            run_identity = SQLiteStorage._resolve_run_identity(
                conn, run_name=run, run_id=run_id, table="metrics"
            )
            if run_identity is None:
                return []

            query = f"SELECT timestamp, step, metrics FROM metrics WHERE {run_identity[0]} = ?"
            params: list = [run_identity[1]]

            if step is not None:
                query += " AND step = ?"
                params.append(step)
            elif around_step is not None and window is not None:
                query += " AND step >= ? AND step <= ?"
                params.extend([around_step - int(window), around_step + int(window)])
            elif at_time is not None and window is not None:
                query += (
                    " AND timestamp >= datetime(?, '-' || ? || ' seconds')"
                    " AND timestamp <= datetime(?, '+' || ? || ' seconds')"
                )
                params.extend([at_time, int(window), at_time, int(window)])

            query += " ORDER BY timestamp"
            cursor.execute(query, params)

            rows = cursor.fetchall()
            results = []
            for row in rows:
                metrics = orjson.loads(row["metrics"])
                metrics = deserialize_values(metrics)
                if metric_name in metrics:
                    results.append(
                        {
                            "timestamp": row["timestamp"],
                            "step": row["step"],
                            "value": metrics[metric_name],
                        }
                    )
            return results

    @staticmethod
    def get_snapshot(
        db_path: Path,
        run: str | None = None,
        step: int | None = None,
        around_step: int | None = None,
        at_time: str | None = None,
        window: int | float | None = None,
        run_id: str | None = None,
    ) -> dict[str, list[dict]]:
        """Get all metrics at/around a point in time or step.

        Returns a dict mapping metric names to lists of {timestamp, step, value}.
        """
        db_path = Path(db_path)
        if not db_path.exists():
            return {}

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            run_identity = SQLiteStorage._resolve_run_identity(
                conn, run_name=run, run_id=run_id, table="metrics"
            )
            if run_identity is None:
                return {}
            query = f"SELECT timestamp, step, metrics FROM metrics WHERE {run_identity[0]} = ?"
            params: list = [run_identity[1]]

            if step is not None:
                query += " AND step = ?"
                params.append(step)
            elif around_step is not None and window is not None:
                query += " AND step >= ? AND step <= ?"
                params.extend([around_step - int(window), around_step + int(window)])
            elif at_time is not None and window is not None:
                query += (
                    " AND timestamp >= datetime(?, '-' || ? || ' seconds')"
                    " AND timestamp <= datetime(?, '+' || ? || ' seconds')"
                )
                params.extend([at_time, int(window), at_time, int(window)])

            query += " ORDER BY timestamp"
            cursor.execute(query, params)

            result: dict[str, list[dict]] = {}
            for row in cursor.fetchall():
                metrics = orjson.loads(row["metrics"])
                metrics = deserialize_values(metrics)
                for key, value in metrics.items():
                    if key not in result:
                        result[key] = []
                    result[key].append(
                        {
                            "timestamp": row["timestamp"],
                            "step": row["step"],
                            "value": value,
                        }
                    )
            return result

    @staticmethod
    def get_all_metrics_for_run(
        db_path: Path, run: str | None = None, run_id: str | None = None
    ) -> list[str]:
        """Get all metric names for a specific run."""
        return SQLiteStorage._get_metric_names(
            db_path,
            run,
            "metrics",
            exclude_keys={"timestamp", "step"},
            run_id=run_id,
        )

    @staticmethod
    def _get_metric_names(
        db_path: Path,
        run: str | None,
        table: str,
        exclude_keys: set[str],
        run_id: str | None = None,
    ) -> list[str]:
        db_path = Path(db_path)
        if not db_path.exists():
            return []

        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                run_identity = SQLiteStorage._resolve_run_identity(
                    conn, run_name=run, run_id=run_id, table=table
                )
                if run_identity is None:
                    return []
                cursor.execute(
                    f"""
                    SELECT metrics
                    FROM {table}
                    WHERE {run_identity[0]} = ?
                    ORDER BY timestamp
                    """,
                    (run_identity[1],),
                )

                rows = cursor.fetchall()
                all_metrics = set()
                for row in rows:
                    metrics = orjson.loads(row["metrics"])
                    metrics = deserialize_values(metrics)
                    for key in metrics.keys():
                        if key not in exclude_keys:
                            all_metrics.add(key)
                return sorted(list(all_metrics))
            except sqlite3.OperationalError as e:
                if f"no such table: {table}" in str(e):
                    return []
                raise

    @staticmethod
    def set_project_metadata(db_path: Path, key: str, value: str) -> None:
        db_path = SQLiteStorage.init_db(db_path)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO project_metadata (key, value) VALUES (?, ?)",
                    (key, value),
                )
                conn.commit()

    @staticmethod
    def get_project_metadata(db_path: Path, key: str) -> str | None:
        db_path = Path(db_path)
        if not db_path.exists():
            return None
        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT value FROM project_metadata WHERE key = ?", (key,)
                )
                row = cursor.fetchone()
                return row[0] if row else None
            except sqlite3.OperationalError:
                return None

    @staticmethod
    def _canonical_manifest(
        manifest: Manifest,
    ) -> tuple[Manifest, Sha256Digest, int]:
        canonical: Manifest = []
        size_bytes = 0
        for entry in manifest:
            path = entry["path"]
            digest = Sha256Digest(entry["digest"])
            size = int(entry["size"])
            canonical.append({"path": path, "digest": digest, "size": size})
            size_bytes += size
        canonical.sort(key=lambda e: e["path"])
        payload = orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS)
        manifest_digest = Sha256Digest(hashlib.sha256(payload).hexdigest())
        return canonical, manifest_digest, size_bytes

    @staticmethod
    def _create_or_get_artifact_cursor(
        conn: sqlite3.Connection,
        name: str,
        type: str,
        description: str | None,
        now: str,
    ) -> int:
        """Return the id of the artifact named `name`, creating it if absent.
        For an existing artifact a non-None `description` is applied in place
        (the type is immutable and a mismatch raises)."""
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT id, type FROM artifacts WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            if row["type"] != type:
                raise ValueError(
                    f"Artifact '{name}' already exists with type "
                    f"'{row['type']}'; cannot relog with type '{type}'."
                )
            if description is not None:
                cursor.execute(
                    "UPDATE artifacts SET description = ? WHERE id = ?",
                    (description, int(row["id"])),
                )
            return int(row["id"])
        cursor.execute(
            """INSERT INTO artifacts (name, type, description, created_at)
            VALUES (?, ?, ?, ?)""",
            (name, type, description, now),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def create_or_get_artifact(
        db_path: Path,
        name: str,
        type: str,
        description: str | None,
    ) -> int:
        db_path = SQLiteStorage.init_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                artifact_id = SQLiteStorage._create_or_get_artifact_cursor(
                    conn, name, type, description, now
                )
                conn.commit()
                return artifact_id

    @staticmethod
    def _insert_artifact_version_cursor(
        conn: sqlite3.Connection,
        artifact_id: int,
        manifest: Manifest,
        metadata: dict | None,
        producer_run_id: str | None,
        producer_run_name: str | None,
        now: str,
    ) -> tuple[int, int, bool]:
        canonical, manifest_digest, size_bytes = SQLiteStorage._canonical_manifest(
            manifest
        )
        manifest_json = orjson.dumps(canonical).decode("utf-8")
        metadata_json = orjson.dumps(metadata).decode("utf-8") if metadata else None
        cursor = conn.cursor()
        existing = cursor.execute(
            """SELECT id, version FROM artifact_versions
            WHERE artifact_id = ? AND manifest_digest = ?""",
            (artifact_id, manifest_digest),
        ).fetchone()
        if existing is not None:
            if metadata:
                cursor.execute(
                    "UPDATE artifact_versions SET metadata = ? WHERE id = ?",
                    (metadata_json, int(existing["id"])),
                )
            return int(existing["id"]), int(existing["version"]), False
        row = cursor.execute(
            "SELECT MAX(version) AS m FROM artifact_versions WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        next_version = 0 if row["m"] is None else int(row["m"]) + 1
        cursor.execute(
            """INSERT INTO artifact_versions
            (artifact_id, version, manifest_digest, manifest, metadata,
             size_bytes, producer_run_id, producer_run_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                next_version,
                manifest_digest,
                manifest_json,
                metadata_json,
                size_bytes,
                producer_run_id,
                producer_run_name,
                now,
            ),
        )
        return int(cursor.lastrowid), next_version, True

    @staticmethod
    def insert_artifact_version(
        db_path: Path,
        artifact_id: int,
        manifest: Manifest,
        metadata: dict | None,
        producer_run_id: str | None,
        producer_run_name: str | None,
    ) -> tuple[int, int, bool]:
        """Returns `(version_id, version, created)`. `created` is False when an
        identical-content version already existed; its `metadata` is refreshed
        in place from the new call (when non-empty) before it is returned."""
        db_path = SQLiteStorage.init_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                result = SQLiteStorage._insert_artifact_version_cursor(
                    conn,
                    artifact_id,
                    manifest,
                    metadata,
                    producer_run_id,
                    producer_run_name,
                    now,
                )
                conn.commit()
                return result

    @staticmethod
    def _reassign_alias_cursor(
        conn: sqlite3.Connection,
        artifact_id: int,
        alias: str,
        version_id: int,
    ) -> None:
        if cas.ARTIFACT_VERSION_SPEC_RE.match(alias):
            raise ValueError(
                f"Alias '{alias}' is reserved for version pointers (vN); choose another."
            )
        conn.execute(
            """INSERT INTO artifact_aliases (artifact_id, alias, artifact_version_id)
            VALUES (?, ?, ?)
            ON CONFLICT(artifact_id, alias) DO UPDATE SET
                artifact_version_id = excluded.artifact_version_id""",
            (artifact_id, alias, version_id),
        )

    @staticmethod
    def _reassign_alias_forward_cursor(
        conn: sqlite3.Connection,
        artifact_id: int,
        alias: str,
        version_id: int,
        version_int: int,
    ) -> None:
        """Reassign `alias` only when it does not move backward."""
        current = conn.execute(
            """SELECT av.version FROM artifact_aliases aa
            JOIN artifact_versions av ON av.id = aa.artifact_version_id
            WHERE aa.artifact_id = ? AND aa.alias = ?""",
            (artifact_id, alias),
        ).fetchone()
        if current is not None and int(current["version"]) > version_int:
            return
        SQLiteStorage._reassign_alias_cursor(conn, artifact_id, alias, version_id)

    @staticmethod
    def reassign_alias(
        db_path: Path,
        artifact_id: int,
        alias: str,
        version_id: int,
    ) -> None:
        db_path = SQLiteStorage.init_db(db_path)
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                SQLiteStorage._reassign_alias_cursor(
                    conn, artifact_id, alias, version_id
                )
                conn.commit()

    @staticmethod
    def _resolve_artifact_version_cursor(
        conn: sqlite3.Connection,
        name: str,
        spec: str | None,
    ) -> dict | None:
        cursor = conn.cursor()
        art = cursor.execute(
            "SELECT id FROM artifacts WHERE name = ?", (name,)
        ).fetchone()
        if art is None:
            return None
        artifact_id = int(art["id"])
        spec = spec if spec else "latest"
        m = cas.ARTIFACT_VERSION_SPEC_RE.match(spec)
        if m:
            version_int = int(m.group(1))
            ver = cursor.execute(
                """SELECT id, version FROM artifact_versions
                WHERE artifact_id = ? AND version = ?""",
                (artifact_id, version_int),
            ).fetchone()
        else:
            ver = cursor.execute(
                """SELECT av.id, av.version FROM artifact_versions av
                JOIN artifact_aliases aa ON aa.artifact_version_id = av.id
                WHERE aa.artifact_id = ? AND aa.alias = ?""",
                (artifact_id, spec),
            ).fetchone()
        if ver is None:
            return None
        return {
            "artifact_id": artifact_id,
            "version_id": int(ver["id"]),
            "version": int(ver["version"]),
        }

    @staticmethod
    def resolve_artifact_version(
        db_path: Path,
        name: str,
        spec: str | None,
    ) -> dict | None:
        db_path = Path(db_path)
        if not db_path.exists():
            return None
        with SQLiteStorage._get_connection(db_path) as conn:
            return SQLiteStorage._resolve_artifact_version_cursor(conn, name, spec)

    @staticmethod
    def _insert_run_artifact_link_cursor(
        conn: sqlite3.Connection,
        run_name: str | None,
        run_id: str | None,
        version_id: int,
        direction: str,
        now: str,
    ) -> None:
        if direction not in ("input", "output"):
            raise ValueError(
                f"direction must be 'input' or 'output', got {direction!r}"
            )
        conn.execute(
            """INSERT OR IGNORE INTO run_artifact_links
            (run_id, run_name, artifact_version_id, direction, created_at)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM run_artifact_links
                WHERE run_id IS ? AND run_name IS ?
                  AND artifact_version_id = ? AND direction = ?
            )""",
            (
                run_id,
                run_name,
                version_id,
                direction,
                now,
                run_id,
                run_name,
                version_id,
                direction,
            ),
        )

    @staticmethod
    def insert_run_artifact_link(
        db_path: Path,
        run_name: str | None,
        run_id: str | None,
        version_id: int,
        direction: str,
    ) -> None:
        db_path = SQLiteStorage.init_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                SQLiteStorage._insert_run_artifact_link_cursor(
                    conn, run_name, run_id, version_id, direction, now
                )
                conn.commit()

    @staticmethod
    def commit_artifact_version(
        db_path: Path,
        name: str,
        type: str,
        description: str | None,
        manifest: Manifest,
        metadata: dict | None,
        aliases: list[str] | None,
        run_name: str | None,
        run_id: str | None,
    ) -> dict:
        """Commit a new artifact version and return its full manifest record.
        `latest` advances only when a new version is created, so re-logging
        identical or older content never regresses `latest`. Moving aliases are
        reassigned the same way: a content-dedup hit to an older version never
        drags an existing alias backward, but a first-time or forward tag lands.
        """
        db_path = SQLiteStorage.init_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        with SQLiteStorage._get_process_lock(db_path):
            with SQLiteStorage._get_connection(db_path) as conn:
                artifact_id = SQLiteStorage._create_or_get_artifact_cursor(
                    conn, name, type, description, now
                )
                version_id, version_int, created = (
                    SQLiteStorage._insert_artifact_version_cursor(
                        conn, artifact_id, manifest, metadata, run_id, run_name, now
                    )
                )
                if created:
                    SQLiteStorage._reassign_alias_cursor(
                        conn, artifact_id, "latest", version_id
                    )
                for alias in aliases or []:
                    SQLiteStorage._reassign_alias_forward_cursor(
                        conn, artifact_id, alias, version_id, version_int
                    )
                SQLiteStorage._insert_run_artifact_link_cursor(
                    conn, run_name, run_id, version_id, "output", now
                )
                conn.commit()
                return SQLiteStorage._get_artifact_manifest_cursor(
                    conn, name, f"v{version_int}"
                )

    @staticmethod
    def _get_artifact_manifest_cursor(
        conn: sqlite3.Connection,
        name: str,
        spec: str | None,
    ) -> dict | None:
        resolved = SQLiteStorage._resolve_artifact_version_cursor(conn, name, spec)
        if resolved is None:
            return None
        cursor = conn.cursor()
        row = cursor.execute(
            """SELECT av.id, av.version, av.manifest, av.manifest_digest,
                   av.metadata, av.size_bytes, av.producer_run_id,
                   av.producer_run_name, av.created_at,
                   a.name, a.type, a.description
            FROM artifact_versions av
            JOIN artifacts a ON a.id = av.artifact_id
            WHERE av.id = ?""",
            (resolved["version_id"],),
        ).fetchone()
        if row is None:
            return None
        alias_rows = cursor.execute(
            """SELECT alias FROM artifact_aliases
            WHERE artifact_version_id = ?""",
            (resolved["version_id"],),
        ).fetchall()
        return {
            "artifact_id": resolved["artifact_id"],
            "version_id": int(row["id"]),
            "version": int(row["version"]),
            "name": row["name"],
            "type": row["type"],
            "description": row["description"],
            "manifest": orjson.loads(row["manifest"]),
            "manifest_digest": row["manifest_digest"],
            "metadata": (
                orjson.loads(row["metadata"]) if row["metadata"] is not None else None
            ),
            "size_bytes": int(row["size_bytes"]),
            "producer_run_id": row["producer_run_id"],
            "producer_run_name": row["producer_run_name"],
            "created_at": row["created_at"],
            "aliases": [r["alias"] for r in alias_rows],
        }

    @staticmethod
    def get_artifact_manifest(
        db_path: Path,
        name: str,
        spec: str | None,
    ) -> dict | None:
        db_path = Path(db_path)
        if not db_path.exists():
            return None
        with SQLiteStorage._get_connection(db_path) as conn:
            return SQLiteStorage._get_artifact_manifest_cursor(conn, name, spec)

    @staticmethod
    def get_artifacts(db_path: Path) -> list[dict]:
        """List every artifact with its latest version, total
        version count, current aliases, and the size of the latest version."""
        db_path = Path(db_path)
        if not db_path.exists():
            return []
        with SQLiteStorage._get_connection(db_path) as conn:
            cursor = conn.cursor()
            artifacts = cursor.execute(
                "SELECT id, name, type, description, created_at FROM artifacts "
                "ORDER BY name"
            ).fetchall()
            result: list[dict] = []
            for a in artifacts:
                artifact_id = int(a["id"])
                latest = cursor.execute(
                    """SELECT version, size_bytes FROM artifact_versions
                    WHERE artifact_id = ? ORDER BY version DESC LIMIT 1""",
                    (artifact_id,),
                ).fetchone()
                num_versions = cursor.execute(
                    "SELECT COUNT(*) AS c FROM artifact_versions WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()["c"]
                alias_rows = cursor.execute(
                    "SELECT alias FROM artifact_aliases WHERE artifact_id = ? "
                    "ORDER BY alias",
                    (artifact_id,),
                ).fetchall()
                result.append(
                    {
                        "name": a["name"],
                        "type": a["type"],
                        "description": a["description"],
                        "num_versions": int(num_versions),
                        "latest_version": (
                            int(latest["version"]) if latest is not None else None
                        ),
                        "size_bytes": (
                            int(latest["size_bytes"]) if latest is not None else None
                        ),
                        "aliases": [r["alias"] for r in alias_rows],
                        "created_at": a["created_at"],
                    }
                )
            return result

    @staticmethod
    def get_run_artifacts(
        db_path: Path,
        run_name: str | None,
        run_id: str | None,
    ) -> dict[str, list[dict]]:
        empty = {"input": [], "output": []}
        db_path = Path(db_path)
        if not db_path.exists():
            return empty
        with SQLiteStorage._get_connection(db_path) as conn:
            identity = SQLiteStorage._resolve_run_identity(
                conn, run_name=run_name, run_id=run_id, table="run_artifact_links"
            )
            if identity is None:
                col, val = (
                    ("run_id", run_id) if run_id is not None else ("run_name", run_name)
                )
            else:
                col, val = identity
            if val is None:
                return empty
            cursor = conn.cursor()
            rows = cursor.execute(
                f"""SELECT ral.direction, ral.created_at,
                       av.id AS version_id, av.version, av.size_bytes,
                       a.name, a.type
                FROM run_artifact_links ral
                JOIN artifact_versions av ON av.id = ral.artifact_version_id
                JOIN artifacts a ON a.id = av.artifact_id
                WHERE ral.{col} = ?
                ORDER BY ral.created_at""",
                (val,),
            ).fetchall()
            result: dict[str, list[dict]] = {"input": [], "output": []}
            for row in rows:
                result[row["direction"]].append(
                    {
                        "version_id": int(row["version_id"]),
                        "name": row["name"],
                        "type": row["type"],
                        "version": int(row["version"]),
                        "size_bytes": int(row["size_bytes"]),
                        "created_at": row["created_at"],
                    }
                )
            return result
