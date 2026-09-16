"""Starlette dashboard app for a single Trackio project directory.

The server is bound to exactly one project directory at creation time:
`create_app(project_dir)` builds a registry of API functions that read from
`<project_dir>/trackio.db` and serve media from `<project_dir>/media/`.
"""

import os
import re
import time
import warnings
from pathlib import Path
from typing import Any

from starlette.applications import Starlette

from trackio import utils
from trackio.asgi_app import create_trackio_starlette_app
from trackio.sqlite_storage import SQLiteStorage

MAX_SERIES_LOGS = 4096

_ALLOWED_TRACE_SORTS = {
    "request_time_desc",
    "request_time_asc",
    "step_asc",
    "step_desc",
}


def _normalize_logs_batch_runs(runs: Any) -> list[dict[str, Any]]:
    if runs is None:
        return []
    if not isinstance(runs, list):
        runs = [runs]
    normalized: list[dict[str, Any]] = []
    for item in runs:
        if isinstance(item, dict):
            normalized.append({"run": item.get("run"), "run_id": item.get("run_id")})
        else:
            normalized.append({"run": item, "run_id": None})
    return normalized


def _normalize_logs_batch_max_points(max_points: Any) -> int | None:
    if max_points is None:
        return None
    if not isinstance(max_points, int | float) or isinstance(max_points, bool):
        return MAX_SERIES_LOGS
    return int(max_points)


def _normalize_bool_param(value: Any, name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean value, got {value!r}")


def filter_metrics_by_regex(metrics: list[str], filter_pattern: str) -> list[str]:
    if not filter_pattern.strip():
        return metrics
    try:
        pattern = re.compile(filter_pattern, re.IGNORECASE)
        return [metric for metric in metrics if pattern.search(metric)]
    except re.error:
        return [
            metric for metric in metrics if filter_pattern.lower() in metric.lower()
        ]


class TrackioDashboardApp:
    def __init__(self, starlette_app, uvicorn_server: Any) -> None:
        self.app = starlette_app
        self._uvicorn_server = uvicorn_server

    def close(self, verbose: bool = True) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
            deadline = time.monotonic() + 5
            while self._uvicorn_server.started and time.monotonic() < deadline:
                time.sleep(0.05)


def build_api_registry(project_dir: str | Path) -> dict[str, Any]:
    """Build the API function registry bound to a single project directory."""
    project_dir = Path(project_dir).expanduser().resolve()
    db_path = utils.resolve_db_path(project_dir)

    def get_all_projects() -> list[str]:
        if db_path.name == utils.DB_FILENAME:
            return [project_dir.name]
        return [db_path.stem]

    def get_runs_for_project() -> list[dict[str, Any]]:
        return SQLiteStorage.get_run_records(db_path)

    def get_run_configs() -> dict[str, Any]:
        return SQLiteStorage.get_all_run_configs(db_path)

    def get_metrics_for_run(
        run: str | None = None, run_id: str | None = None
    ) -> list[str]:
        return SQLiteStorage.get_all_metrics_for_run(db_path, run, run_id=run_id)

    def get_project_summary() -> dict[str, Any]:
        runs = SQLiteStorage.get_run_records(db_path)
        if not runs:
            return {
                "dir": str(project_dir),
                "num_runs": 0,
                "runs": [],
                "last_activity": None,
            }

        last_steps = SQLiteStorage.get_max_steps_for_runs(db_path)

        return {
            "dir": str(project_dir),
            "num_runs": len(runs),
            "runs": runs,
            "last_activity": max(last_steps.values()) if last_steps else None,
        }

    def get_run_summary(
        run: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        if run_id is not None:
            record = next(
                (
                    record
                    for record in SQLiteStorage.get_run_records(db_path)
                    if record["id"] == run_id
                ),
                None,
            )
            if record is not None:
                run = record["name"]

        num_logs = SQLiteStorage.get_log_count(db_path, run, run_id=run_id)
        if num_logs == 0:
            return {
                "dir": str(project_dir),
                "run": run,
                "run_id": run_id,
                "num_logs": 0,
                "metrics": [],
                "config": None,
                "last_step": None,
            }

        metrics = SQLiteStorage.get_all_metrics_for_run(db_path, run, run_id=run_id)
        config = SQLiteStorage.get_run_config(db_path, run, run_id=run_id)
        last_step = SQLiteStorage.get_last_step(db_path, run, run_id=run_id)

        return {
            "dir": str(project_dir),
            "run": run,
            "run_id": run_id,
            "num_logs": num_logs,
            "metrics": metrics,
            "config": config,
            "last_step": last_step,
        }

    def get_system_metrics_for_run(
        run: str | None = None, run_id: str | None = None
    ) -> list[str]:
        return SQLiteStorage.get_all_system_metrics_for_run(db_path, run, run_id=run_id)

    def get_system_logs(
        run: str | None = None, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        return SQLiteStorage.get_system_logs(
            db_path, run, run_id=run_id, max_points=MAX_SERIES_LOGS
        )

    def get_system_logs_batch(
        runs: list[dict[str, Any]],
        max_points: int | None = MAX_SERIES_LOGS,
    ) -> list[dict[str, Any]]:
        runs_clean = _normalize_logs_batch_runs(runs)
        mp = _normalize_logs_batch_max_points(max_points)
        return SQLiteStorage.get_system_logs_batch(db_path, runs_clean, max_points=mp)

    def get_snapshot(
        run: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        around_step: int | None = None,
        at_time: str | None = None,
        window: int | None = None,
    ) -> dict[str, Any]:
        return SQLiteStorage.get_snapshot(
            db_path,
            run,
            run_id=run_id,
            step=step,
            around_step=around_step,
            at_time=at_time,
            window=window,
        )

    def get_metric_values(
        run: str | None,
        metric_name: str,
        step: int | None = None,
        around_step: int | None = None,
        at_time: str | None = None,
        window: int | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return SQLiteStorage.get_metric_values(
            db_path,
            run,
            metric_name,
            step=step,
            around_step=around_step,
            at_time=at_time,
            window=window,
            run_id=run_id,
        )

    def get_logs(
        run: str | None = None,
        run_id: str | None = None,
        scalar_only: bool = False,
    ) -> list[dict[str, Any]]:
        return SQLiteStorage.get_logs(
            db_path,
            run,
            max_points=MAX_SERIES_LOGS,
            run_id=run_id,
            scalar_only=_normalize_bool_param(scalar_only, "scalar_only"),
        )

    def get_logs_batch(
        runs: list[dict[str, Any]],
        max_points: int | None = MAX_SERIES_LOGS,
        scalar_only: bool = False,
    ) -> list[dict[str, Any]]:
        runs_clean = _normalize_logs_batch_runs(runs)
        mp = _normalize_logs_batch_max_points(max_points)
        return SQLiteStorage.get_logs_batch(
            db_path,
            runs_clean,
            max_points=mp,
            scalar_only=_normalize_bool_param(scalar_only, "scalar_only"),
        )

    def get_traces(
        run: str | None = None,
        run_id: str | None = None,
        search: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        offset: int | None = 0,
        step: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            normalized_offset = max(0, int(offset)) if offset is not None else 0
        except (TypeError, ValueError):
            normalized_offset = 0
        normalized_limit: int | None
        if limit is None:
            normalized_limit = None
        else:
            try:
                normalized_limit = max(0, int(limit))
            except (TypeError, ValueError):
                normalized_limit = None
        normalized_step: int | None
        if step is None:
            normalized_step = None
        else:
            try:
                normalized_step = int(step)
            except (TypeError, ValueError):
                normalized_step = None
        normalized_sort = sort if sort in _ALLOWED_TRACE_SORTS else None
        return SQLiteStorage.get_traces(
            db_path,
            run,
            search=search,
            sort=normalized_sort,
            limit=normalized_limit,
            offset=normalized_offset,
            run_id=run_id,
            step=normalized_step,
        )

    def get_trace_steps(
        run: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return SQLiteStorage.get_trace_steps(db_path, run, run_id=run_id)

    def get_alerts(
        run: str | None = None,
        run_id: str | None = None,
        level: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        return SQLiteStorage.get_alerts(
            db_path, run_name=run, run_id=run_id, level=level, since=since
        )

    def query_project(query: str) -> dict[str, Any]:
        return SQLiteStorage.query(db_path, query)

    def get_settings() -> dict[str, Any]:
        return {
            "logo_urls": utils.get_logo_urls(),
            "color_palette": utils.get_color_palette(),
            "plot_order": [
                item.strip()
                for item in os.environ.get("TRACKIO_PLOT_ORDER", "").split(",")
                if item.strip()
            ],
            "table_truncate_length": int(
                os.environ.get("TRACKIO_TABLE_TRUNCATE_LENGTH", "250")
            ),
            "media_dir": str(utils.media_dir(project_dir)),
            "project_dir": str(project_dir),
            "project_name": project_dir.name,
        }

    def get_project_files() -> list[dict[str, Any]]:
        files_dir = utils.files_dir(project_dir)
        if not files_dir.exists():
            return []
        results = []
        for file_path in sorted(files_dir.rglob("*")):
            if file_path.is_file():
                relative = file_path.relative_to(files_dir)
                results.append(
                    {
                        "name": str(relative),
                        "path": str(file_path),
                        "size": file_path.stat().st_size,
                    }
                )
        return results

    def _project_has_files() -> bool:
        files_dir = utils.files_dir(project_dir)
        if not files_dir.exists():
            return False
        for entry in files_dir.rglob("*"):
            if entry.is_file():
                return True
        return False

    def get_tab_availability() -> dict[str, bool]:
        flags = SQLiteStorage.get_tab_availability_flags(db_path)
        return {
            "metrics": flags["metrics"],
            "system": flags["system"],
            "traces": flags["traces"],
            "media": flags["media"],
            "reports": flags["reports"] or flags["alerts"],
            "files": _project_has_files(),
        }

    def get_run_mutation_status() -> dict[str, Any]:
        return {"can_mutate": True}

    def delete_run(
        run: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        return SQLiteStorage.delete_run(db_path, run, run_id=run_id)

    def rename_run(
        old_name: str,
        new_name: str,
        run_id: str | None = None,
    ) -> bool:
        SQLiteStorage.rename_run(db_path, old_name, new_name, run_id=run_id)
        return True

    def get_artifacts() -> list[dict]:
        return SQLiteStorage.get_artifacts(db_path)

    def get_run_artifacts(
        run: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, list[dict]]:
        return SQLiteStorage.get_run_artifacts(db_path, run, run_id)

    def get_artifact_manifest(name: str, spec: str | None = None) -> dict | None:
        return SQLiteStorage.get_artifact_manifest(db_path, name, spec)

    return {
        "get_run_mutation_status": get_run_mutation_status,
        "get_artifact_manifest": get_artifact_manifest,
        "get_artifacts": get_artifacts,
        "get_run_artifacts": get_run_artifacts,
        "get_alerts": get_alerts,
        "get_metric_values": get_metric_values,
        "get_all_projects": get_all_projects,
        "get_runs_for_project": get_runs_for_project,
        "get_run_configs": get_run_configs,
        "get_metrics_for_run": get_metrics_for_run,
        "get_project_summary": get_project_summary,
        "get_run_summary": get_run_summary,
        "get_system_metrics_for_run": get_system_metrics_for_run,
        "get_system_logs": get_system_logs,
        "get_system_logs_batch": get_system_logs_batch,
        "get_snapshot": get_snapshot,
        "get_logs": get_logs,
        "get_logs_batch": get_logs_batch,
        "get_traces": get_traces,
        "get_trace_steps": get_trace_steps,
        "query_project": query_project,
        "get_settings": get_settings,
        "get_project_files": get_project_files,
        "get_tab_availability": get_tab_availability,
        "delete_run": delete_run,
        "rename_run": rename_run,
    }


def create_app(
    project_dir: str | Path,
    mcp_server: bool = False,
    frontend_dir: str | None = None,
) -> Starlette:
    """Create the Starlette dashboard app bound to a single project directory."""
    project_dir = Path(project_dir).expanduser().resolve()
    db_path = utils.resolve_db_path(project_dir)
    if not db_path.exists():
        SQLiteStorage.init_db(utils.get_db_path(project_dir))

    registry = build_api_registry(project_dir)

    mcp_lifespan = None
    mcp_routes: list[Any] = []
    mcp_enabled = False
    if mcp_server:
        try:
            from trackio.mcp_setup import create_mcp_integration  # noqa: PLC0415

            mcp_routes, mcp_lifespan = create_mcp_integration(registry)
            mcp_enabled = True
        except ImportError:
            warnings.warn(
                "MCP support requested, but the optional `mcp` package is not installed. "
                "Install `trackio[mcp]` to expose `/mcp`.",
                UserWarning,
                stacklevel=2,
            )

    starlette_app = create_trackio_starlette_app(
        registry,
        project_dir=project_dir,
        extra_routes=mcp_routes,
        mcp_lifespan=mcp_lifespan,
        mcp_enabled=mcp_enabled,
        allowed_file_roots=[
            utils.media_dir(project_dir),
            utils.TRACKIO_LOGO_DIR,
        ],
    )
    from trackio.compression import CompressionMiddleware  # noqa: PLC0415
    from trackio.frontend_config import resolve_frontend_dir  # noqa: PLC0415
    from trackio.frontend_server import mount_frontend  # noqa: PLC0415

    resolved_frontend = resolve_frontend_dir(frontend_dir)
    mount_frontend(starlette_app, frontend_dir=resolved_frontend.path)
    starlette_app.add_middleware(CompressionMiddleware)
    return starlette_app
