import atexit
import glob
import json
import logging
import os
import shutil
import webbrowser
from pathlib import Path
from typing import Any

from trackio import context_vars, utils
from trackio.alerts import AlertLevel
from trackio.api import Api
from trackio.apple_gpu import apple_gpu_available
from trackio.apple_gpu import log_apple_gpu as _log_apple_gpu
from trackio.artifact import Artifact
from trackio.cpu import cpu_available
from trackio.cpu import log_cpu as _log_cpu
from trackio.frontend_config import resolve_frontend_dir
from trackio.gpu import gpu_available
from trackio.gpu import log_gpu as _log_nvidia_gpu
from trackio.histogram import Histogram
from trackio.imports import import_csv, import_tf_events
from trackio.launch import start_server
from trackio.markdown import Markdown
from trackio.media import (
    TrackioAudio,
    TrackioImage,
    TrackioVideo,
    get_project_media_path,
)
from trackio.run import Run
from trackio.server import TrackioDashboardApp, create_app
from trackio.sqlite_storage import SQLiteStorage
from trackio.table import Table
from trackio.trace import Trace
from trackio.utils import TRACKIO_LOGO_DIR, _emit_nonfatal_warning

logging.getLogger("httpx").setLevel(logging.WARNING)

__version__ = json.loads(Path(__file__).parent.joinpath("package.json").read_text())[
    "version"
]


class _TupleNoPrint(tuple):
    def __repr__(self) -> str:
        return ""


__all__ = [
    "init",
    "log",
    "log_system",
    "log_gpu",
    "log_artifact",
    "use_artifact",
    "log_cpu",
    "finish",
    "alert",
    "AlertLevel",
    "show",
    "import_csv",
    "import_tf_events",
    "save",
    "Artifact",
    "Image",
    "Video",
    "Audio",
    "Table",
    "Trace",
    "Histogram",
    "Markdown",
    "Api",
    "TRACKIO_LOGO_DIR",
    "get_project_media_path",
]

Audio = TrackioAudio
Image = TrackioImage
Video = TrackioVideo

config = {}

_atexit_registered = False
_dirs_notified_auto_log_hw: set[str] = set()


def _cleanup_current_run():
    run = context_vars.current_run.get()
    if run is not None:
        try:
            run.finish()
        except Exception:
            pass


def init(
    dir: str | Path = ".",
    name: str | None = None,
    group: str | None = None,
    config: dict | None = None,
    resume: str = "never",
    settings: Any = None,
    embed: bool = True,
    auto_log_gpu: bool | None = None,
    gpu_log_interval: float = 10.0,
    auto_log_cpu: bool | None = None,
    cpu_log_interval: float = 10.0,
    webhook_url: str | None = None,
    webhook_min_level: AlertLevel | str | None = None,
) -> Run:
    """
    Creates (or opens) a Trackio project directory and returns a [`Run`] object.

    A Trackio project is a directory on disk. Metrics are stored in
    `<dir>/trackio.db`, media files under `<dir>/media/`, and artifacts under
    `<dir>/artifacts/`. View the dashboard for a project with
    `python -m trackio.show <dir>`.

    Args:
        dir (`str` or `Path`, *optional*, defaults to `"."`):
            The project directory. It is created if it does not exist.
        name (`str`, *optional*):
            The name of the run (if not provided, a default name will be generated).
        group (`str`, *optional*):
            The name of the group which this run belongs to in order to help organize
            related runs together. You can toggle the entire group's visibility in the
            dashboard.
        config (`dict`, *optional*):
            A dictionary of configuration options. Provided for compatibility with
            `wandb.init()`.
        resume (`str`, *optional*, defaults to `"never"`):
            Controls how to handle resuming a run. Can be one of:

            - `"must"`: Must resume the run with the given name, raises error if run
              doesn't exist
            - `"allow"`: Resume the run if it exists, otherwise create a new run
            - `"never"`: Never resume a run, always create a new one
        settings (`Any`, *optional*):
            Not used. Provided for compatibility with `wandb.init()`.
        embed (`bool`, *optional*, defaults to `True`):
            If running inside a Jupyter notebook, whether the dashboard should
            automatically be embedded in the cell when trackio.init() is called.
        auto_log_gpu (`bool` or `None`, *optional*, defaults to `None`):
            Controls automatic GPU metrics logging. If `None` (default), GPU logging
            is automatically enabled when `nvidia-ml-py` is installed and an NVIDIA
            GPU or Apple M series is detected. Set to `True` to force enable or
            `False` to disable.
        gpu_log_interval (`float`, *optional*, defaults to `10.0`):
            The interval in seconds between automatic GPU metric logs.
            Only used when `auto_log_gpu=True`.
        auto_log_cpu (`bool` or `None`, *optional*, defaults to `None`):
            Controls automatic CPU and RAM metrics logging (utilization, memory,
            disk I/O, network I/O, and sensors). If `None` (default), CPU logging
            is automatically enabled when `psutil` is installed. Set to `True` to
            force enable or `False` to disable.
        cpu_log_interval (`float`, *optional*, defaults to `10.0`):
            The interval in seconds between automatic CPU metric logs.
            Only used when CPU auto-logging is enabled.
        webhook_url (`str`, *optional*):
            A webhook URL to POST alert payloads to when `trackio.alert()` is
            called. Supports Slack and Discord webhook URLs natively (payloads
            are formatted automatically). Can also be set via the
            `TRACKIO_WEBHOOK_URL` environment variable. Individual alerts can
            override this URL by passing `webhook_url` to `trackio.alert()`.
        webhook_min_level (`AlertLevel` or `str`, *optional*):
            Minimum alert level that should trigger webhook delivery.
            For example, `AlertLevel.WARN` sends only `WARN` and `ERROR`
            alerts to the webhook destination. Can also be set via
            `TRACKIO_WEBHOOK_MIN_LEVEL`.
    Returns:
        `Run`: A [`Run`] object that can be used to log metrics and finish the run.
    """
    if settings is not None:
        _emit_nonfatal_warning(
            "* Warning: settings is not used. Provided for compatibility with wandb.init()."
        )

    project_dir = Path(dir).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    db_path = utils.get_db_path(project_dir)

    previous_run = context_vars.current_run.get()
    if previous_run is not None:
        try:
            previous_run.finish()
        except Exception as e:
            _emit_nonfatal_warning(
                f"trackio.init() could not finish the previous run '{previous_run.name}': {e}. Continuing with new run."
            )
        context_vars.current_run.set(None)

    if context_vars.current_project_dir.get() != str(project_dir):
        print(f"* Trackio project directory: {project_dir}")
        if not utils.is_in_notebook():
            utils.print_dashboard_instructions(project_dir)
    context_vars.current_project_dir.set(str(project_dir))

    try:
        existing_run_records = SQLiteStorage.get_run_records(db_path)
    except Exception as e:
        _emit_nonfatal_warning(
            f"trackio.init() could not inspect existing runs in '{project_dir}': {e}. Continuing without resume metadata."
        )
        existing_run_records = []
    existing_runs = [r["name"] for r in existing_run_records]

    existing_run = None
    if name is not None:
        try:
            existing_run = SQLiteStorage.get_latest_run_record_by_name(db_path, name)
        except Exception as e:
            _emit_nonfatal_warning(
                f"trackio.init() could not inspect existing runs in '{project_dir}': {e}. Continuing without resume metadata."
            )

    resolved_run_id = None
    if resume == "must":
        if name is None:
            raise ValueError("Must provide a run name when resume='must'")
        if existing_run is None:
            raise ValueError(f"Run '{name}' does not exist in '{project_dir}'")
        resumed = True
        resolved_run_id = existing_run["id"]
    elif resume == "allow":
        resumed = existing_run is not None
        if resumed:
            resolved_run_id = existing_run["id"]
    elif resume == "never":
        resumed = False
    else:
        raise ValueError("resume must be one of: 'must', 'allow', or 'never'")

    initial_last_step = None
    if resumed and name is not None:
        try:
            initial_last_step = SQLiteStorage.get_max_step_for_run(
                db_path, name, run_id=resolved_run_id
            )
        except Exception as e:
            _emit_nonfatal_warning(
                f"trackio.init() could not recover the previous step for run '{name}': {e}. Continuing from step 0."
            )

    auto_log_cpu_detected = False
    if auto_log_cpu is None:
        auto_log_cpu_detected = cpu_available()
        auto_log_cpu = auto_log_cpu_detected

    auto_log_gpu_detected = False
    nvidia_available = False
    apple_available = False
    if auto_log_gpu is None:
        nvidia_available = gpu_available()
        apple_available = apple_gpu_available()
        auto_log_gpu_detected = nvidia_available or apple_available
        auto_log_gpu = auto_log_gpu_detected

    dir_key = str(project_dir)
    if dir_key not in _dirs_notified_auto_log_hw:
        if nvidia_available:
            print("* NVIDIA GPU detected, enabling automatic GPU metrics logging")
        elif apple_available:
            print(
                "* Apple Silicon detected, enabling automatic GPU/system metrics logging"
            )
        if auto_log_cpu_detected:
            print("* psutil detected, enabling automatic CPU/system metrics logging")
        if auto_log_gpu_detected or auto_log_cpu_detected:
            _dirs_notified_auto_log_hw.add(dir_key)

    run = Run(
        project_dir=project_dir,
        name=name,
        run_id=resolved_run_id,
        group=group,
        config=config,
        existing_runs=existing_runs,
        initial_last_step=initial_last_step,
        auto_log_gpu=auto_log_gpu,
        gpu_log_interval=gpu_log_interval,
        auto_log_cpu=auto_log_cpu,
        cpu_log_interval=cpu_log_interval,
        webhook_url=webhook_url,
        webhook_min_level=webhook_min_level,
    )

    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_cleanup_current_run)
        _atexit_registered = True

    if resumed:
        print(f"* Resumed existing run: {run.name}")
    else:
        print(f"* Created new run: {run.name}")

    context_vars.current_run.set(run)
    globals()["config"] = run.config

    if embed and utils.is_in_notebook():
        try:
            show(dir=project_dir, open_browser=False, block_thread=False)
        except Exception as e:
            _emit_nonfatal_warning(
                f"trackio.init() could not auto-launch the dashboard: {e}. Logging will continue."
            )

    return run


def log(metrics: dict, step: int | None = None) -> None:
    """
    Logs metrics to the current run.

    Args:
        metrics (`dict`):
            A dictionary of metrics to log.
        step (`int`, *optional*):
            The step number. If not provided, the step will be incremented
            automatically.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.log().")
    run.log(
        metrics=metrics,
        step=step,
    )


def log_system(metrics: dict) -> None:
    """
    Logs system metrics (GPU, etc.) to the current run using timestamps instead of steps.

    Args:
        metrics (`dict`):
            A dictionary of system metrics to log.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.log_system().")
    run.log_system(metrics=metrics)


def log_artifact(
    artifact_or_path: Artifact | str | Path,
    name: str | None = None,
    type: str | None = None,
    aliases: list[str] | None = None,
) -> Artifact:
    """
    Logs an artifact as an output of the current run.

    Args:
        artifact_or_path (`Artifact`, `str`, or `Path`):
            The artifact to log (must have at least one file added via
            `add_file` or `add_dir`), or a path to a file or directory to
            log as a new artifact.
        name (`str`, *optional*):
            Artifact name when logging a path. Defaults to the basename of
            the path. Must not be passed with an `Artifact`.
        type (`str`, *optional*):
            Artifact type when logging a path (e.g. `"model"`, `"dataset"`).
            Defaults to `"unspecified"`. Must not be passed with an
            `Artifact`.
        aliases (`list[str]`, *optional*):
            Aliases to rotate onto the resulting version, alongside `latest`
            (assigned automatically whenever a new version is created). Your
            aliases rotate onto the version even when identical content is
            de-duplicated.

    Returns:
        The logged `Artifact` instance, hydrated with `version`, `aliases`,
        `size`, and `manifest` set.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.log_artifact().")
    return run.log_artifact(artifact_or_path, name=name, type=type, aliases=aliases)


def use_artifact(
    artifact_or_name: Artifact | str,
    type: str | None = None,
) -> Artifact:
    """
    Fetches an artifact and records it as an input to the current run.

    Args:
        artifact_or_name (`Artifact` or `str`):
            An already-logged `Artifact`, or an artifact name. A bare name
            (`"my-model"`) resolves to `:latest`; you can also pin a version
            (`"my-model:v3"`) or resolve an alias (`"my-model:prod"`).
        type (`str`, *optional*):
            If given, checked against the stored artifact type, raising if it
            does not match.

    Returns:
        The fetched `Artifact`, hydrated and ready to `download()`.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.use_artifact().")
    return run.use_artifact(artifact_or_name, type=type)


def log_gpu(run: Run | None = None, device: int | None = None) -> dict:
    """
    Log GPU metrics to the current or specified run as system metrics.
    Automatically detects whether an NVIDIA or Apple GPU is available and calls
    the appropriate logging method.

    Args:
        run: Optional Run instance. If None, uses current run from context.
        device: CUDA device index to collect metrics from (NVIDIA GPUs only).
                If None, collects from all GPUs visible to this process.
                This parameter is ignored for Apple GPUs.

    Returns:
        dict: The GPU metrics that were logged.
    """
    if run is None:
        run = context_vars.current_run.get()
        if run is None:
            raise RuntimeError("Call trackio.init() before trackio.log_gpu().")

    if gpu_available():
        return _log_nvidia_gpu(run=run, device=device)
    elif apple_gpu_available():
        return _log_apple_gpu(run=run)
    else:
        _emit_nonfatal_warning(
            "No GPU detected. Install nvidia-ml-py for NVIDIA GPU support "
            "or psutil for Apple Silicon support."
        )
        return {}


def log_cpu(run: Run | None = None) -> dict:
    """
    Log CPU, RAM, disk, network, and sensor metrics to the current or specified run
    as system metrics.

    Args:
        run: Optional Run instance. If None, uses current run from context.

    Returns:
        dict: The CPU and system metrics that were logged.
    """
    if run is None:
        run = context_vars.current_run.get()
        if run is None:
            raise RuntimeError("Call trackio.init() before trackio.log_cpu().")

    return _log_cpu(run=run)


def finish():
    """
    Finishes the current run.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.finish().")
    try:
        run.finish()
    finally:
        context_vars.current_run.set(None)


def alert(
    title: str,
    text: str | None = None,
    level: AlertLevel = AlertLevel.WARN,
    webhook_url: str | None = None,
) -> None:
    """
    Fires an alert immediately on the current run. The alert is printed to the
    terminal, stored in the database, and displayed in the dashboard. If a
    webhook URL is configured (via `trackio.init()`, the `TRACKIO_WEBHOOK_URL`
    environment variable, or the `webhook_url` parameter here), the alert is
    also POSTed to that URL.

    Args:
        title (`str`):
            A short title for the alert.
        text (`str`, *optional*):
            A longer description with details about the alert.
        level (`AlertLevel`, *optional*, defaults to `AlertLevel.WARN`):
            The severity level. One of `AlertLevel.INFO`, `AlertLevel.WARN`,
            or `AlertLevel.ERROR`.
        webhook_url (`str`, *optional*):
            A webhook URL to send this specific alert to. Overrides any
            URL set in `trackio.init()` or the `TRACKIO_WEBHOOK_URL`
            environment variable. Supports Slack and Discord webhook
            URLs natively.
    """
    run = context_vars.current_run.get()
    if run is None:
        raise RuntimeError("Call trackio.init() before trackio.alert().")
    run.alert(title=title, text=text, level=level, webhook_url=webhook_url)


def save(
    glob_str: str | Path,
    dir: str | Path | None = None,
) -> str:
    """
    Saves files to a project directory (not linked to a specific run). The
    file(s) will be copied into the project's `media/files/` directory.

    Args:
        glob_str (`str` or `Path`):
            The file path or glob pattern to save. Can be a single file or a pattern
            matching multiple files (e.g., `"*.py"`, `"models/**/*.pth"`).
        dir (`str` or `Path`, *optional*):
            The project directory to save files to. If not provided, uses the current
            project directory from `trackio.init()`. If no project is initialized,
            raises an error.

    Returns:
        `str`: The path where the file(s) were saved (project's files directory).

    Example:
        ```python
        import trackio

        trackio.init(dir="runs/my-project")
        trackio.save("config.yaml")
        trackio.save("models/*.pth")
        ```
    """
    if dir is None:
        dir = context_vars.current_project_dir.get()
        if dir is None:
            raise RuntimeError(
                "No project directory specified. Either call trackio.init() first or provide a "
                "dir parameter to trackio.save()."
            )

    project_dir = Path(dir).expanduser().resolve()
    glob_str = Path(glob_str)
    base_path = Path.cwd().resolve()

    matched_files = []
    if glob_str.is_file():
        matched_files = [glob_str.resolve()]
    else:
        pattern = str(glob_str)
        if not glob_str.is_absolute():
            pattern = str((Path.cwd() / glob_str).resolve())
        matched_files = [
            Path(f).resolve()
            for f in glob.glob(pattern, recursive=True)
            if Path(f).is_file()
        ]

    if not matched_files:
        raise ValueError(f"No files found matching pattern: {glob_str}")

    files_root = utils.files_dir(project_dir)
    for file_path in matched_files:
        try:
            relative_to_base = file_path.relative_to(base_path)
        except ValueError:
            relative_to_base = Path(file_path.name)

        target_path = files_root / relative_to_base
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(file_path), str(target_path))

    return str(files_root)


def show(
    dir: str | Path = ".",
    *,
    mcp_server: bool | None = None,
    color_palette: list[str] | None = None,
    open_browser: bool = True,
    block_thread: bool | None = None,
    host: str | None = None,
    server_port: int | None = None,
    frontend_dir: str | Path | None = None,
):
    """
    Launches the Trackio dashboard for a project directory.

    Args:
        dir (`str` or `Path`, *optional*, defaults to `"."`):
            The project directory whose runs to show. Must contain a
            `trackio.db` database (created by `trackio.init(dir=...)`); an
            empty one is created if missing.
        mcp_server (`bool`, *optional*):
            If `True`, the dashboard exposes an MCP server at `/mcp` when the optional
            `trackio[mcp]` dependency is installed.
        color_palette (`list[str]`, *optional*):
            A list of hex color codes to use for plot lines. If not provided, the
            `TRACKIO_COLOR_PALETTE` environment variable will be used (comma-separated
            hex codes), or if that is not set, the default color palette will be used.
            Example: `['#FF0000', '#00FF00', '#0000FF']`
        open_browser (`bool`, *optional*, defaults to `True`):
            If `True` and not in a notebook, a new browser tab will be opened with the
            dashboard. If `False`, the browser will not be opened.
        block_thread (`bool`, *optional*):
            If `True`, the main thread will be blocked until the dashboard is closed.
            If `None` (default behavior), then the main thread will not be blocked if the
            dashboard is launched in a notebook, otherwise the main thread will be blocked.
        host (`str`, *optional*):
            The host to bind the server to. If not provided, defaults to `'127.0.0.1'`
            (localhost only). Set to `'0.0.0.0'` to allow remote access.
        server_port (`int`, *optional*):
            Port to bind. If not set, scans from `GRADIO_SERVER_PORT` (default 7720).
        frontend_dir (`str | Path`, *optional*):
            Directory containing a custom static frontend. Must contain `index.html`.
            If not provided, Trackio checks `TRACKIO_FRONTEND_DIR`, then the persistent
            Trackio config, then the bundled frontend.

    Returns:
        `app`: The dashboard handle (`.close()` stops the server).
        `url`: The local URL of the dashboard.
    """
    if color_palette is not None:
        os.environ["TRACKIO_COLOR_PALETTE"] = ",".join(color_palette)

    _mcp_server = (
        mcp_server
        if mcp_server is not None
        else os.environ.get("GRADIO_MCP_SERVER", "False") == "True"
    )

    project_dir = Path(dir).expanduser().resolve()
    resolved_frontend = resolve_frontend_dir(frontend_dir, announce=True)
    starlette_app = create_app(
        project_dir=project_dir,
        mcp_server=_mcp_server,
        frontend_dir=str(resolved_frontend.path),
    )
    local_url, uv_server = start_server(
        starlette_app,
        server_name=host,
        server_port=server_port,
    )
    server = TrackioDashboardApp(starlette_app, uv_server)

    if not utils.is_in_notebook():
        print(f"\033[1m\033[38;5;208m* Trackio UI launched at: {local_url}\033[0m")
        if open_browser:
            webbrowser.open(local_url)
        block_thread = block_thread if block_thread is not None else True
    else:
        utils.embed_url_in_notebook(local_url)
        block_thread = block_thread if block_thread is not None else False

    if block_thread:
        utils.block_main_thread_until_keyboard_interrupt()
    return _TupleNoPrint((server, local_url))
