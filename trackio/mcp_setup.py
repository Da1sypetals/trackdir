from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from starlette.routing import Mount

_MCP_TOOLS = [
    ("get_runs_for_project", "List runs in this Trackio project."),
    ("get_metrics_for_run", "List metric names recorded for a given Trackio run."),
    ("get_project_summary", "Return summary metadata for this Trackio project."),
    ("get_run_summary", "Return summary metadata for a Trackio run."),
    (
        "get_metric_values",
        "Fetch metric values for a run, optionally around a step or time.",
    ),
    ("get_system_metrics_for_run", "List system metric names recorded for a run."),
    ("get_system_logs", "Fetch system metric logs for a run."),
    ("get_snapshot", "Fetch a single Trackio snapshot around a step or timestamp."),
    ("get_logs", "Fetch Trackio metric logs for a run."),
    (
        "get_alerts",
        "Fetch alerts for this project, optionally filtered by run or level.",
    ),
    ("get_artifacts", "List artifacts in this project."),
    ("get_run_artifacts", "List artifacts produced and consumed by a run."),
    ("get_artifact_manifest", "Fetch the manifest for an artifact version."),
    ("query_project", "Run a read-only SQL query against the project database."),
    ("delete_run", "Delete a run from this project."),
    ("rename_run", "Rename a run in this project."),
    ("get_settings", "Return Trackio dashboard settings and asset configuration."),
]


def create_mcp_integration(api_registry: dict[str, Any]) -> tuple[list[Any], Any]:
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    mcp = FastMCP(
        "Trackio",
        instructions="Inspect and manage Trackio experiment data.",
        streamable_http_path="/",
        log_level="WARNING",
    )

    for name, description in _MCP_TOOLS:
        fn = api_registry.get(name)
        if fn is None:
            continue
        mcp.add_tool(fn, description=description, structured_output=True)

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def mcp_lifespan_context(app):
        async with mcp.session_manager.run():
            yield

    return [Mount("/mcp", app=mcp_app)], mcp_lifespan_context
