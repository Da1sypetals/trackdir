import argparse
import os
from pathlib import Path

import trackio
from trackio import utils
from trackio.cli_helpers import (
    error_exit,
    format_alerts,
    format_artifact,
    format_artifacts,
    format_json,
    format_list,
    format_metric_values,
    format_project_summary,
    format_query_result,
    format_run_summary,
    format_snapshot,
    format_system_metrics,
)
from trackio.markdown import Markdown
from trackio.sqlite_storage import SQLiteStorage


def _resolve_dir(args, required: bool = True) -> Path:
    raw = getattr(args, "dir", None)
    if raw is None:
        if required:
            error_exit("Provide a project directory with --dir.")
        return None
    return Path(raw).expanduser().resolve()


def _require_project(args) -> Path:
    project_dir = _resolve_dir(args)
    db_path = utils.resolve_db_path(project_dir)
    if not db_path.exists():
        error_exit(f"No trackio project found at '{project_dir}'.")
    return project_dir


def _require_run(args, db_path) -> None:
    runs = SQLiteStorage.get_runs(db_path)
    if args.run not in runs:
        error_exit(f"Run '{args.run}' not found in '{_resolve_dir(args)}'.")


def _handle_show(args):
    if args.mcp_server:
        os.environ["GRADIO_MCP_SERVER"] = "True"
    else:
        os.environ["GRADIO_MCP_SERVER"] = "False"
    trackio.show(
        dir=args.dir,
        mcp_server=args.mcp_server,
        color_palette=args.color_palette,
        host=args.host,
        server_port=args.port,
        frontend_dir=args.frontend,
    )


def _handle_list_runs(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    runs = SQLiteStorage.get_runs(db_path)
    if args.json:
        print(format_json({"dir": str(project_dir), "runs": runs}))
    else:
        print(format_list(runs, f"Runs in '{project_dir}'"))


def _handle_list_metrics(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    metrics = SQLiteStorage.get_all_metrics_for_run(db_path, args.run)
    print(f"Run: {args.run}")
    print(f"Total metrics: {len(metrics)}")
    print()
    for metric in metrics:
        print(metric)


def _handle_list_system_metrics(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    metrics = SQLiteStorage.get_all_system_metrics_for_run(db_path, args.run)
    print(f"Run: {args.run}")
    print(f"Total system metrics: {len(metrics)}")
    print()
    for metric in metrics:
        print(metric)


def _handle_list_alerts(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    alerts = SQLiteStorage.get_alerts(db_path, run_name=args.run)
    print(format_alerts(alerts))


def _handle_list_reports(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    logs = SQLiteStorage.get_logs(db_path, args.run)
    reports = _extract_reports(args.run, logs)
    report_names = sorted({r["report"] for r in reports})
    if args.json:
        print(
            format_json(
                {"dir": str(project_dir), "run": args.run, "reports": report_names}
            )
        )
    else:
        print(format_list(report_names, f"Reports in run '{args.run}'"))


def _handle_list_artifacts(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    artifacts = SQLiteStorage.get_artifacts(db_path)
    if args.json:
        print(format_json({"dir": str(project_dir), "artifacts": artifacts}))
    else:
        print(format_artifacts(artifacts, project_dir.name))


def _handle_get_project(args):
    project_dir = _require_project(args)
    summary = trackio.server.build_api_registry(project_dir)["get_project_summary"]()
    if args.json:
        print(format_json(summary))
    else:
        print(format_project_summary(summary))


def _handle_get_run(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    summary = trackio.server.build_api_registry(project_dir)["get_run_summary"](
        run=args.run
    )
    if args.json:
        print(format_json(summary))
    else:
        print(format_run_summary(summary))


def _handle_get_artifact(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    record = SQLiteStorage.get_artifact_manifest(db_path, args.artifact, args.spec)
    if record is None:
        error_exit(f"Artifact '{args.artifact}' not found in '{project_dir}'.")
    result = {
        "dir": str(project_dir),
        "name": record["name"],
        "type": record["type"],
        "version": record["version"],
        "aliases": record["aliases"],
        "manifest_digest": record["manifest_digest"],
        "size_bytes": record["size_bytes"],
        "metadata": record["metadata"],
        "created_at": record["created_at"],
    }
    if args.json:
        result["manifest"] = record["manifest"]
        print(format_json(result))
    else:
        print(format_artifact(record))


def _handle_get_metric(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    metrics = SQLiteStorage.get_all_metrics_for_run(db_path, args.run)
    if args.metric not in metrics:
        error_exit(
            f"Metric '{args.metric}' not found in run '{args.run}' of '{project_dir}'."
        )
    values = SQLiteStorage.get_metric_values(
        db_path,
        args.run,
        args.metric,
        step=args.step,
        around_step=args.around,
        at_time=args.at_time,
        window=args.window,
    )
    if args.json:
        print(
            format_json(
                {
                    "dir": str(project_dir),
                    "run": args.run,
                    "metric": args.metric,
                    "values": values,
                }
            )
        )
    else:
        print(format_metric_values(values))


def _handle_get_snapshot(args):
    if not args.step and not args.around and not args.at_time:
        error_exit(
            "Provide --step, --around (with --window), or --at-time (with --window)."
        )
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    snapshot = SQLiteStorage.get_snapshot(
        db_path,
        args.run,
        step=args.step,
        around_step=args.around,
        at_time=args.at_time,
        window=args.window,
    )
    if args.json:
        result = {
            "dir": str(project_dir),
            "run": args.run,
            "metrics": snapshot,
        }
        if args.step is not None:
            result["step"] = args.step
        if args.around is not None:
            result["around"] = args.around
            result["window"] = args.window
        if args.at_time is not None:
            result["at_time"] = args.at_time
            result["window"] = args.window
        print(format_json(result))
    else:
        print(format_snapshot(snapshot))


def _handle_get_system_metric(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    system_metrics = SQLiteStorage.get_system_logs(db_path, args.run)
    if args.metric:
        all_names = SQLiteStorage.get_all_system_metrics_for_run(db_path, args.run)
        if args.metric not in all_names:
            error_exit(
                f"System metric '{args.metric}' not found in run '{args.run}' of '{project_dir}'."
            )
        filtered = [
            {k: v for k, v in entry.items() if k == "timestamp" or k == args.metric}
            for entry in system_metrics
            if args.metric in entry
        ]
        if args.json:
            print(
                format_json(
                    {
                        "dir": str(project_dir),
                        "run": args.run,
                        "metric": args.metric,
                        "values": filtered,
                    }
                )
            )
        else:
            print(format_system_metrics(filtered))
    else:
        if args.json:
            print(
                format_json(
                    {
                        "dir": str(project_dir),
                        "run": args.run,
                        "system_metrics": system_metrics,
                    }
                )
            )
        else:
            print(format_system_metrics(system_metrics))


def _handle_get_alerts(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    alerts = SQLiteStorage.get_alerts(
        db_path, run_name=args.run, level=args.level, since=args.since
    )
    if args.json:
        print(
            format_json(
                {
                    "dir": str(project_dir),
                    "run": args.run,
                    "level": args.level,
                    "since": args.since,
                    "alerts": alerts,
                }
            )
        )
    else:
        print(format_alerts(alerts))


def _handle_get_report(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    logs = SQLiteStorage.get_logs(db_path, args.run)
    reports = _extract_reports(args.run, logs, report_name=args.report)
    if not reports:
        error_exit(
            f"Report '{args.report}' not found in run '{args.run}' of '{project_dir}'."
        )
    if args.json:
        print(
            format_json(
                {
                    "dir": str(project_dir),
                    "run": args.run,
                    "report": args.report,
                    "values": reports,
                }
            )
        )
    else:
        output = []
        for idx, entry in enumerate(reports, start=1):
            output.append(
                f"Entry {idx} | step={entry['step']} | timestamp={entry['timestamp']}"
            )
            output.append(entry["content"])
            if idx < len(reports):
                output.append("-" * 80)
        print("\n".join(output))


def _extract_reports(
    run: str, logs: list[dict], report_name: str | None = None
) -> list[dict]:
    reports = []
    for log in logs:
        timestamp = log.get("timestamp")
        step = log.get("step")
        for key, value in log.items():
            if report_name is not None and key != report_name:
                continue
            if isinstance(value, dict) and value.get("_type") == Markdown.TYPE:
                content = value.get("_value")
                if isinstance(content, str):
                    reports.append(
                        {
                            "run": run,
                            "report": key,
                            "step": step,
                            "timestamp": timestamp,
                            "content": content,
                        }
                    )
    return reports


def _handle_query(args):
    project_dir = _require_project(args)
    try:
        result = SQLiteStorage.query(utils.resolve_db_path(project_dir), args.sql)
    except FileNotFoundError as e:
        error_exit(str(e))
    except ValueError as e:
        error_exit(str(e))
    if args.json:
        print(format_json(result))
    else:
        print(format_query_result(result))


def _handle_delete_run(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    _require_run(args, db_path)
    if not args.force:
        response = input(
            f"Are you sure you want to delete run '{args.run}' from '{project_dir}'? (y/N): "
        )
        if response.lower() not in ("y", "yes"):
            print("Cancelled.")
            return
    deleted = SQLiteStorage.delete_run(db_path, args.run)
    if deleted:
        print(f"Run '{args.run}' deleted from '{project_dir}'.")
    else:
        error_exit(f"Failed to delete run '{args.run}'.")


def _handle_rename_run(args):
    project_dir = _require_project(args)
    db_path = utils.resolve_db_path(project_dir)
    try:
        SQLiteStorage.rename_run(db_path, args.old_name, args.new_name)
    except ValueError as e:
        error_exit(str(e))
    print(f"Run '{args.old_name}' renamed to '{args.new_name}' in '{project_dir}'.")


def _handle_config(args):
    from trackio.frontend_config import (
        delete_custom_frontend,
        get_custom_frontend,
        is_bundled_frontend,
        save_custom_frontend,
    )

    if args.config_command == "get":
        custom = get_custom_frontend()
        using_bundled = is_bundled_frontend(None)
        payload = {
            "frontend": {
                "custom": custom,
                "effective": "bundled" if using_bundled else custom,
            }
        }
        print(format_json(payload))
    elif args.config_command == "set":
        if args.key != "frontend":
            error_exit(f"Unknown config key '{args.key}'. Supported keys: frontend.")
        try:
            saved = save_custom_frontend(args.value)
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            error_exit(str(e))
        print(f"Custom frontend set to: {saved}")
    elif args.config_command == "unset":
        if args.key != "frontend":
            error_exit(f"Unknown config key '{args.key}'. Supported keys: frontend.")
        deleted = delete_custom_frontend()
        print("Custom frontend cleared." if deleted else "No custom frontend was set.")
    else:
        error_exit("Use: trackio config get|set|unset")


def main():
    parser = argparse.ArgumentParser(description="Trackio CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"trackio {trackio.__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    show_parser = subparsers.add_parser(
        "show", help="Launch the Trackio dashboard for a project directory."
    )
    show_parser.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project directory to show (default: current directory).",
    )
    show_parser.add_argument(
        "--host",
        help="Host to bind. Defaults to '127.0.0.1'. Use '0.0.0.0' for remote access.",
    )
    show_parser.add_argument("--port", type=int, help="Port to bind.")
    show_parser.add_argument(
        "--mcp-server",
        action="store_true",
        help="Enable the MCP server (requires the trackio[mcp] extra).",
    )
    show_parser.add_argument(
        "--color-palette",
        nargs="+",
        help="Custom hex color codes for plot lines.",
    )
    show_parser.add_argument(
        "--frontend",
        help="Path to a custom frontend directory containing index.html.",
    )
    subparsers.add_parser(
        "ui", help="Alias for 'show'.", parents=[show_parser], add_help=False
    )

    config_parser = subparsers.add_parser(
        "config", help="Manage local Trackio configuration."
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("get", help="Show current Trackio config.")
    config_set_parser = config_subparsers.add_parser(
        "set",
        help="Set a Trackio config value (e.g. trackio config set frontend /path/to/dir).",
    )
    config_set_parser.add_argument("key", help="Config key.")
    config_set_parser.add_argument("value", help="Config value.")
    config_unset_parser = config_subparsers.add_parser(
        "unset", help="Unset a Trackio config value."
    )
    config_unset_parser.add_argument("key", help="Config key.")

    list_parser = subparsers.add_parser("list", help="List Trackio resources.")
    list_subparsers = list_parser.add_subparsers(dest="list_type")

    list_runs_parser = list_subparsers.add_parser("runs", help="List all runs.")
    list_runs_parser.add_argument("--dir", required=True, help="Project directory.")
    list_runs_parser.add_argument("--json", action="store_true", help="Output as JSON.")

    list_metrics_parser = list_subparsers.add_parser(
        "metrics", help="List all metrics for a specific run."
    )
    list_metrics_parser.add_argument("--dir", required=True, help="Project directory.")
    list_metrics_parser.add_argument("--run", required=True, help="Run name.")

    list_sys_parser = list_subparsers.add_parser(
        "system-metrics", help="List all system metrics for a specific run."
    )
    list_sys_parser.add_argument("--dir", required=True, help="Project directory.")
    list_sys_parser.add_argument("--run", required=True, help="Run name.")

    list_alerts_parser = list_subparsers.add_parser(
        "alerts", help="List alerts for a run."
    )
    list_alerts_parser.add_argument("--dir", required=True, help="Project directory.")
    list_alerts_parser.add_argument("--run", required=True, help="Run name.")

    list_reports_parser = list_subparsers.add_parser(
        "reports", help="List markdown report metric names for a run."
    )
    list_reports_parser.add_argument("--dir", required=True, help="Project directory.")
    list_reports_parser.add_argument("--run", required=True, help="Run name.")
    list_reports_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    list_artifacts_parser = list_subparsers.add_parser(
        "artifacts", help="List artifacts."
    )
    list_artifacts_parser.add_argument(
        "--dir", required=True, help="Project directory."
    )
    list_artifacts_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_parser = subparsers.add_parser("get", help="Get Trackio resource details.")
    get_subparsers = get_parser.add_subparsers(dest="get_type")

    get_project_parser = get_subparsers.add_parser(
        "project", help="Get project summary."
    )
    get_project_parser.add_argument("--dir", required=True, help="Project directory.")
    get_project_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_run_parser = get_subparsers.add_parser("run", help="Get run summary.")
    get_run_parser.add_argument("--dir", required=True, help="Project directory.")
    get_run_parser.add_argument("--run", required=True, help="Run name.")
    get_run_parser.add_argument("--json", action="store_true", help="Output as JSON.")

    get_artifact_parser = get_subparsers.add_parser(
        "artifact", help="Get artifact details."
    )
    get_artifact_parser.add_argument("--dir", required=True, help="Project directory.")
    get_artifact_parser.add_argument("--artifact", required=True, help="Artifact name.")
    get_artifact_parser.add_argument(
        "--spec",
        help="Artifact version spec (e.g. 'latest', 'v0'). Defaults to 'latest'.",
    )
    get_artifact_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_metric_parser = get_subparsers.add_parser(
        "metric", help="Get metric values for a specific run."
    )
    get_metric_parser.add_argument("--dir", required=True, help="Project directory.")
    get_metric_parser.add_argument("--run", required=True, help="Run name.")
    get_metric_parser.add_argument("--metric", required=True, help="Metric name.")
    get_metric_parser.add_argument("--step", type=int, help="Exact step.")
    get_metric_parser.add_argument(
        "--around", type=int, help="Center step (with --window)."
    )
    get_metric_parser.add_argument(
        "--at-time", help="Center time ISO8601 (with --window)."
    )
    get_metric_parser.add_argument("--window", type=float, help="Window size.")
    get_metric_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_snapshot_parser = get_subparsers.add_parser(
        "snapshot", help="Get all metrics at a specific step/time."
    )
    get_snapshot_parser.add_argument("--dir", required=True, help="Project directory.")
    get_snapshot_parser.add_argument("--run", required=True, help="Run name.")
    get_snapshot_parser.add_argument("--step", type=int, help="Exact step.")
    get_snapshot_parser.add_argument(
        "--around", type=int, help="Center step (with --window)."
    )
    get_snapshot_parser.add_argument(
        "--at-time", help="Center time ISO8601 (with --window)."
    )
    get_snapshot_parser.add_argument("--window", type=float, help="Window size.")
    get_snapshot_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_sys_metric_parser = get_subparsers.add_parser(
        "system-metric", help="Get system metrics for a specific run."
    )
    get_sys_metric_parser.add_argument(
        "--dir", required=True, help="Project directory."
    )
    get_sys_metric_parser.add_argument("--run", required=True, help="Run name.")
    get_sys_metric_parser.add_argument("--metric", help="System metric name.")
    get_sys_metric_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_alerts_parser = get_subparsers.add_parser("alerts", help="Get alerts.")
    get_alerts_parser.add_argument("--dir", required=True, help="Project directory.")
    get_alerts_parser.add_argument("--run", help="Filter by run name.")
    get_alerts_parser.add_argument(
        "--level", choices=["info", "warn", "error"], help="Filter by level."
    )
    get_alerts_parser.add_argument("--since", help="ISO8601 timestamp.")
    get_alerts_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    get_report_parser = get_subparsers.add_parser(
        "report", help="Get a markdown report for a run."
    )
    get_report_parser.add_argument("--dir", required=True, help="Project directory.")
    get_report_parser.add_argument("--run", required=True, help="Run name.")
    get_report_parser.add_argument("--report", required=True, help="Report name.")
    get_report_parser.add_argument(
        "--json", action="store_true", help="Output as JSON."
    )

    query_parser = subparsers.add_parser(
        "query", help="Run a read-only SQL query against a project database."
    )
    query_parser.add_argument("dir", help="Project directory.")
    query_parser.add_argument("sql", help="SQL query to execute.")
    query_parser.add_argument("--json", action="store_true", help="Output as JSON.")

    delete_run_parser = subparsers.add_parser("delete-run", help="Delete a run.")
    delete_run_parser.add_argument("--dir", required=True, help="Project directory.")
    delete_run_parser.add_argument("--run", required=True, help="Run name to delete.")
    delete_run_parser.add_argument(
        "--force", action="store_true", help="Skip confirmation."
    )

    rename_run_parser = subparsers.add_parser("rename-run", help="Rename a run.")
    rename_run_parser.add_argument("--dir", required=True, help="Project directory.")
    rename_run_parser.add_argument(
        "--old-name", required=True, help="Current run name."
    )
    rename_run_parser.add_argument("--new-name", required=True, help="New run name.")

    args = parser.parse_args()

    if args.command in ("show", "ui"):
        _handle_show(args)
    elif args.command == "config":
        _handle_config(args)
    elif args.command == "list":
        if args.list_type == "runs":
            _handle_list_runs(args)
        elif args.list_type == "metrics":
            _handle_list_metrics(args)
        elif args.list_type == "system-metrics":
            _handle_list_system_metrics(args)
        elif args.list_type == "alerts":
            _handle_list_alerts(args)
        elif args.list_type == "reports":
            _handle_list_reports(args)
        elif args.list_type == "artifacts":
            _handle_list_artifacts(args)
        else:
            error_exit(
                "Use: trackio list runs|metrics|system-metrics|alerts|reports|artifacts"
            )
    elif args.command == "get":
        if args.get_type == "project":
            _handle_get_project(args)
        elif args.get_type == "run":
            _handle_get_run(args)
        elif args.get_type == "artifact":
            _handle_get_artifact(args)
        elif args.get_type == "metric":
            _handle_get_metric(args)
        elif args.get_type == "snapshot":
            _handle_get_snapshot(args)
        elif args.get_type == "system-metric":
            _handle_get_system_metric(args)
        elif args.get_type == "alerts":
            _handle_get_alerts(args)
        elif args.get_type == "report":
            _handle_get_report(args)
        else:
            error_exit(
                "Use: trackio get project|run|artifact|metric|snapshot|system-metric|alerts|report"
            )
    elif args.command == "query":
        _handle_query(args)
    elif args.command == "delete-run":
        _handle_delete_run(args)
    elif args.command == "rename-run":
        _handle_rename_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
