import getpass
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from trackio import cas, utils
from trackio.alerts import (
    AlertLevel,
    format_alert_terminal,
    resolve_webhook_min_level,
    send_webhook,
    should_send_webhook,
)
from trackio.apple_gpu import AppleGpuMonitor, apple_gpu_available
from trackio.artifact import Artifact
from trackio.cpu import CpuMonitor
from trackio.gpu import GpuMonitor, gpu_available
from trackio.histogram import Histogram
from trackio.markdown import Markdown
from trackio.media import TrackioMedia
from trackio.sqlite_storage import SQLiteStorage
from trackio.table import Table
from trackio.trace import Trace
from trackio.typehints import AlertEntry, LogEntry, SystemLogEntry
from trackio.utils import _emit_nonfatal_warning

BATCH_SEND_INTERVAL = 0.5


class Run:
    def __init__(
        self,
        project_dir: str | Path,
        name: str | None = None,
        run_id: str | None = None,
        group: str | None = None,
        config: dict | None = None,
        existing_runs: list[str] | None = None,
        initial_last_step: int | None = None,
        auto_log_gpu: bool = False,
        gpu_log_interval: float = 10.0,
        auto_log_cpu: bool = False,
        cpu_log_interval: float = 10.0,
        webhook_url: str | None = None,
        webhook_min_level: AlertLevel | str | None = None,
    ):
        """
        Initialize a Run for logging metrics to Trackio.

        Args:
            project_dir: The project directory. Metrics are stored in
                `<project_dir>/trackio.db`, media under `<project_dir>/media/`.
            name: The name of this run. If None, a readable name like
                "brave-sunset-0" is auto-generated.
            run_id: The run id. If None, a random id is generated. Resuming a
                run reuses its id.
            group: Optional group name to organize related runs together.
            config: A dictionary of configuration/hyperparameters for this run.
                Keys starting with '_' are reserved for internal use.
            existing_runs: Optional pre-fetched run names for this project. Used to
                avoid redundant storage lookups during init.
            initial_last_step: Optional pre-fetched last step for a resumed run.
            auto_log_gpu: Whether to automatically log GPU metrics (utilization,
                memory, temperature) at regular intervals.
            gpu_log_interval: The interval in seconds between GPU metric logs.
                Only used when auto_log_gpu is True.
            auto_log_cpu: Whether to automatically log CPU and RAM metrics
                (utilization, memory, disk I/O, network I/O, sensors) at regular
                intervals.
            cpu_log_interval: The interval in seconds between CPU metric logs.
                Only used when auto_log_cpu is True.
            webhook_url: A webhook URL to POST alert payloads to. Supports
                Slack and Discord webhook URLs natively. Can also be set via
                the TRACKIO_WEBHOOK_URL environment variable.
            webhook_min_level: Minimum alert level that should trigger webhook
                delivery. For example, `AlertLevel.WARN` sends only WARN and
                ERROR alerts to webhook destinations. Can also be set via
                `TRACKIO_WEBHOOK_MIN_LEVEL`.
        """
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.db_path = utils.get_db_path(self.project_dir)
        self._client_lock = threading.Lock()
        self._warning_lock = threading.Lock()
        self._warned_failures: set[str] = set()
        self._local_sender_thread: threading.Thread | None = None
        self.id = run_id or uuid.uuid4().hex
        self._existing_runs = existing_runs
        self._initial_last_step = initial_last_step
        if name is not None:
            self.name = name
        else:
            try:
                self.name = utils.generate_readable_name(self._safe_get_existing_runs())
            except Exception as e:
                self._warn_once(
                    "init-run-name",
                    f"trackio.init() could not generate a run name: {e}. Falling back to a random name.",
                )
                self.name = f"trackio-run-{uuid.uuid4().hex[:8]}"
        self.group = group
        try:
            self.config = utils.to_json_safe(config or {})
        except Exception as e:
            self._warn_once(
                "init-config",
                f"trackio.init() failed to serialize the run config: {e}. Continuing without config.",
            )
            self.config = {}

        if isinstance(self.config, dict):
            for key in self.config:
                if key.startswith("_"):
                    raise ValueError(
                        f"Config key '{key}' is reserved (keys starting with '_' are reserved for internal use)"
                    )

        self.config["_Username"] = self._get_username()
        self.config["_Created"] = datetime.now(timezone.utc).isoformat()
        self.config["_Group"] = self.group

        self._queued_logs: list[LogEntry] = []
        self._queued_system_logs: list[SystemLogEntry] = []
        self._queued_alerts: list[AlertEntry] = []
        self._stop_flag = threading.Event()
        self._config_logged = False
        max_step = self._safe_get_max_step_for_run()
        self._next_step = 0 if max_step is None else max_step + 1

        self._webhook_url = webhook_url or os.environ.get("TRACKIO_WEBHOOK_URL")
        self._webhook_min_level = resolve_webhook_min_level(
            webhook_min_level or os.environ.get("TRACKIO_WEBHOOK_MIN_LEVEL")
        )

        self._start_background_thread(
            "_local_sender_thread",
            self._local_batch_sender,
            warning_key="local-sender-thread",
            description="local Trackio logging thread",
        )

        self._gpu_monitor: "GpuMonitor | AppleGpuMonitor | None" = None
        if auto_log_gpu:
            try:
                if gpu_available():
                    self._gpu_monitor = GpuMonitor(self, interval=gpu_log_interval)
                    self._gpu_monitor.start()
                elif apple_gpu_available():
                    self._gpu_monitor = AppleGpuMonitor(
                        self,
                        interval=gpu_log_interval,
                        include_cpu_metrics=not auto_log_cpu,
                    )
                    self._gpu_monitor.start()
            except Exception as e:
                self._warn_once(
                    "gpu-monitor",
                    f"trackio.init() failed to start automatic GPU logging: {e}. Continuing without system metric auto-logging.",
                )

        self._cpu_monitor: "CpuMonitor | None" = None
        if auto_log_cpu:
            try:
                self._cpu_monitor = CpuMonitor(self, interval=cpu_log_interval)
                self._cpu_monitor.start()
            except Exception as e:
                self._warn_once(
                    "cpu-monitor",
                    f"trackio.init() failed to start automatic CPU logging: {e}. Continuing without CPU metric auto-logging.",
                )

    def _get_username(self) -> str | None:
        try:
            return getpass.getuser()
        except Exception:
            return None

    def _warn_once(self, key: str, message: str) -> None:
        with self._warning_lock:
            if key in self._warned_failures:
                return
            self._warned_failures.add(key)
        _emit_nonfatal_warning(message)

    def _safe_get_existing_runs(self) -> list[str]:
        if self._existing_runs is not None:
            return self._existing_runs
        try:
            return SQLiteStorage.get_runs(self.db_path)
        except Exception as e:
            self._warn_once(
                "init-existing-runs",
                f"trackio.init() could not inspect existing runs in '{self.project_dir}': {e}. Continuing without prior-run metadata.",
            )
            return []

    def _safe_get_max_step_for_run(self) -> int | None:
        if self._initial_last_step is not None:
            return self._initial_last_step
        try:
            return SQLiteStorage.get_max_step_for_run(
                self.db_path, self.name, run_id=self.id
            )
        except Exception as e:
            self._warn_once(
                "init-max-step",
                f"trackio.init() could not recover the previous step for run '{self.name}': {e}. Continuing from step 0.",
            )
            return None

    def _start_background_thread(
        self,
        attr_name: str,
        target,
        *,
        warning_key: str,
        description: str,
    ) -> bool:
        try:
            thread = threading.Thread(target=target, daemon=True)
            setattr(self, attr_name, thread)
            thread.start()
            return True
        except Exception as e:
            setattr(self, attr_name, None)
            self._warn_once(
                warning_key,
                f"trackio failed to start the {description}: {e}. Logging will continue in degraded mode.",
            )
            return False

    def _thread_is_alive(self, attr_name: str) -> bool:
        thread = getattr(self, attr_name, None)
        return isinstance(thread, threading.Thread) and thread.is_alive()

    def _flush_queues_inline(self) -> None:
        if self._queued_logs:
            logs_to_send = self._queued_logs.copy()
            self._queued_logs.clear()
            self._write_logs_to_sqlite(logs_to_send)

        if self._queued_system_logs:
            system_logs_to_send = self._queued_system_logs.copy()
            self._queued_system_logs.clear()
            self._write_system_logs_to_sqlite(system_logs_to_send)

        if self._queued_alerts:
            alerts_to_send = self._queued_alerts.copy()
            self._queued_alerts.clear()
            self._write_alerts_to_sqlite(alerts_to_send)

    def _local_batch_sender(self):
        while (
            not self._stop_flag.is_set()
            or len(self._queued_logs) > 0
            or len(self._queued_system_logs) > 0
            or len(self._queued_alerts) > 0
        ):
            if not self._stop_flag.is_set():
                self._stop_flag.wait(timeout=BATCH_SEND_INTERVAL)

            try:
                with self._client_lock:
                    if self._queued_logs:
                        logs_to_send = self._queued_logs.copy()
                        self._queued_logs.clear()
                        self._write_logs_to_sqlite(logs_to_send)

                    if self._queued_system_logs:
                        system_logs_to_send = self._queued_system_logs.copy()
                        self._queued_system_logs.clear()
                        self._write_system_logs_to_sqlite(system_logs_to_send)

                    if self._queued_alerts:
                        alerts_to_send = self._queued_alerts.copy()
                        self._queued_alerts.clear()
                        self._write_alerts_to_sqlite(alerts_to_send)
            except Exception as e:
                self._warn_once(
                    "local-sender-loop",
                    f"trackio's local logging thread hit an internal error: {e}. User code will continue, but some Trackio data may be dropped.",
                )

    def _write_logs_to_sqlite(self, logs: list[LogEntry]):
        try:
            logs_by_run: dict[tuple, dict] = {}
            for entry in logs:
                key = (entry["run"], entry.get("run_id"))
                if key not in logs_by_run:
                    logs_by_run[key] = {
                        "metrics": [],
                        "steps": [],
                        "log_ids": [],
                        "config": None,
                    }
                logs_by_run[key]["metrics"].append(entry["metrics"])
                logs_by_run[key]["steps"].append(entry.get("step"))
                logs_by_run[key]["log_ids"].append(entry.get("log_id"))
                if entry.get("config") and logs_by_run[key]["config"] is None:
                    logs_by_run[key]["config"] = entry["config"]

            for (run, run_id), data in logs_by_run.items():
                has_log_ids = any(lid is not None for lid in data["log_ids"])
                SQLiteStorage.bulk_log(
                    db_path=self.db_path,
                    run=run,
                    run_id=run_id,
                    metrics_list=data["metrics"],
                    steps=data["steps"],
                    config=data["config"],
                    log_ids=data["log_ids"] if has_log_ids else None,
                )
        except Exception as e:
            self._warn_once(
                "write-logs-to-sqlite",
                f"trackio failed to flush metric logs for run '{self.name}': {e}. User code will continue, but this batch could not be persisted.",
            )

    def _write_system_logs_to_sqlite(self, logs: list[SystemLogEntry]):
        try:
            logs_by_run: dict[tuple, dict] = {}
            for entry in logs:
                key = (entry["run"], entry.get("run_id"))
                if key not in logs_by_run:
                    logs_by_run[key] = {"metrics": [], "timestamps": [], "log_ids": []}
                logs_by_run[key]["metrics"].append(entry["metrics"])
                logs_by_run[key]["timestamps"].append(entry.get("timestamp"))
                logs_by_run[key]["log_ids"].append(entry.get("log_id"))

            for (run, run_id), data in logs_by_run.items():
                has_log_ids = any(lid is not None for lid in data["log_ids"])
                SQLiteStorage.bulk_log_system(
                    db_path=self.db_path,
                    run=run,
                    run_id=run_id,
                    metrics_list=data["metrics"],
                    timestamps=data["timestamps"],
                    log_ids=data["log_ids"] if has_log_ids else None,
                )
        except Exception as e:
            self._warn_once(
                "write-system-logs-to-sqlite",
                f"trackio failed to flush system logs for run '{self.name}': {e}. User code will continue, but this batch could not be persisted.",
            )

    def _write_alerts_to_sqlite(self, alerts: list[AlertEntry]):
        try:
            alerts_by_run: dict[tuple, dict] = {}
            for entry in alerts:
                key = (entry["run"], entry.get("run_id"))
                if key not in alerts_by_run:
                    alerts_by_run[key] = {
                        "titles": [],
                        "texts": [],
                        "levels": [],
                        "steps": [],
                        "timestamps": [],
                        "alert_ids": [],
                    }
                alerts_by_run[key]["titles"].append(entry["title"])
                alerts_by_run[key]["texts"].append(entry.get("text"))
                alerts_by_run[key]["levels"].append(entry["level"])
                alerts_by_run[key]["steps"].append(entry.get("step"))
                alerts_by_run[key]["timestamps"].append(entry.get("timestamp"))
                alerts_by_run[key]["alert_ids"].append(entry.get("alert_id"))

            for (run, run_id), data in alerts_by_run.items():
                has_alert_ids = any(aid is not None for aid in data["alert_ids"])
                SQLiteStorage.bulk_alert(
                    db_path=self.db_path,
                    run=run,
                    run_id=run_id,
                    titles=data["titles"],
                    texts=data["texts"],
                    levels=data["levels"],
                    steps=data["steps"],
                    timestamps=data["timestamps"],
                    alert_ids=data["alert_ids"] if has_alert_ids else None,
                )
        except Exception as e:
            self._warn_once(
                "write-alerts-to-sqlite",
                f"trackio failed to flush alerts for run '{self.name}': {e}. User code will continue, but this batch could not be persisted.",
            )

    def _process_media(self, value: TrackioMedia, step: int | None) -> dict:
        value._save(self.project_dir, self.name, step if step is not None else 0)
        return value._to_dict()

    def _ensure_sender_alive(self):
        if (
            not self._thread_is_alive("_local_sender_thread")
            and not self._stop_flag.is_set()
        ):
            self._start_background_thread(
                "_local_sender_thread",
                self._local_batch_sender,
                warning_key="local-sender-thread-restart",
                description="local Trackio logging thread",
            )

    def log(self, metrics: dict, step: int | None = None):
        try:
            renamed_keys = []
            new_metrics = {}

            for k, v in metrics.items():
                if k in utils.RESERVED_KEYS or k.startswith("__"):
                    new_key = f"__{k}"
                    renamed_keys.append(k)
                    new_metrics[new_key] = v
                else:
                    new_metrics[k] = v

            if renamed_keys:
                _emit_nonfatal_warning(
                    f"Reserved keys renamed: {renamed_keys} → '__{{key}}'"
                )

            metrics = new_metrics
            media_step = step if step is not None else self._next_step
            for key, value in metrics.items():
                if isinstance(value, Table):
                    metrics[key] = value._to_dict(
                        project_dir=self.project_dir, run=self.name, step=media_step
                    )
                elif isinstance(value, Trace):
                    metrics[key] = value._to_dict(
                        project_dir=self.project_dir, run=self.name, step=media_step
                    )
                elif (
                    isinstance(value, list)
                    and value
                    and all(isinstance(item, Trace) for item in value)
                ):
                    metrics[key] = [
                        item._to_dict(
                            project_dir=self.project_dir,
                            run=self.name,
                            step=media_step,
                        )
                        for item in value
                    ]
                elif isinstance(value, Histogram):
                    metrics[key] = value._to_dict()
                elif isinstance(value, Markdown):
                    metrics[key] = value._to_dict()
                elif isinstance(value, TrackioMedia):
                    metrics[key] = self._process_media(value, media_step)
            metrics = utils.serialize_values(metrics)

            if step is None:
                step = self._next_step
            self._next_step = max(self._next_step, step + 1)

            config_to_log = None
            if not self._config_logged and self.config:
                config_to_log = utils.to_json_safe(self.config)
                self._config_logged = True

            log_entry: LogEntry = {
                "run": self.name,
                "run_id": self.id,
                "metrics": metrics,
                "step": step,
                "config": config_to_log,
                "log_id": uuid.uuid4().hex,
            }

            with self._client_lock:
                self._queued_logs.append(log_entry)
                self._ensure_sender_alive()
                if not self._thread_is_alive("_local_sender_thread"):
                    self._flush_queues_inline()
        except Exception as e:
            _emit_nonfatal_warning(f"trackio.log() failed to process metrics: {e}")

    def log_artifact(
        self,
        artifact_or_path: Artifact | str | Path,
        name: str | None = None,
        type: str | None = None,
        aliases: list[str] | None = None,
    ) -> Artifact:
        if isinstance(artifact_or_path, Artifact):
            if name is not None or type is not None:
                raise ValueError(
                    "name/type can only be passed when logging a path; "
                    "set them on the Artifact instead."
                )
            artifact = artifact_or_path
        else:
            path = Path(artifact_or_path)
            artifact = Artifact(
                name=name or path.name,
                type=type or "unspecified",
            )
            if path.is_dir():
                artifact.add_dir(path)
            else:
                artifact.add_file(path)

        if artifact._logged:
            raise RuntimeError(
                "Artifact has already been logged or fetched; "
                "construct a new Artifact() to log again."
            )

        user_aliases = cas.validate_aliases(aliases)

        manifest = artifact._build_manifest(self.project_dir)

        record = SQLiteStorage.commit_artifact_version(
            db_path=self.db_path,
            name=artifact.name,
            type=artifact.type,
            description=artifact.description,
            manifest=manifest,
            metadata=artifact.metadata,
            aliases=user_aliases,
            run_name=self.name,
            run_id=self.id,
        )

        artifact._hydrate_from_db(
            project_dir=self.project_dir,
            version=record["version"],
            aliases=record["aliases"],
            manifest=record["manifest"],
            manifest_digest=record["manifest_digest"],
            size_bytes=record["size_bytes"],
        )
        return artifact

    @staticmethod
    def _check_artifact_type(
        spec: str, stored_type: str, expected_type: str | None
    ) -> None:
        if expected_type is not None and stored_type != expected_type:
            raise ValueError(
                f"Artifact {spec!r} has type {stored_type!r}, not {expected_type!r}."
            )

    def use_artifact(
        self,
        artifact_or_name: Artifact | str,
        type: str | None = None,
    ) -> Artifact:
        """Resolve an artifact and record this run as a consumer of it."""
        if isinstance(artifact_or_name, Artifact):
            if not artifact_or_name._logged or artifact_or_name._version is None:
                raise ValueError(
                    "use_artifact() with an Artifact instance requires an "
                    "artifact that has already been logged or fetched."
                )
            spec = f"{artifact_or_name.name}:v{artifact_or_name._version}"
        else:
            spec = artifact_or_name

        if ":" in spec:
            name, version_or_alias = spec.split(":", 1)
            if not version_or_alias:
                raise ValueError(
                    f"Artifact spec {spec!r} has an empty version/alias after ':'. "
                    "Use 'name:vN' or 'name:alias', or drop the colon to get latest."
                )
        else:
            name, version_or_alias = spec, None

        record = SQLiteStorage.get_artifact_manifest(
            self.db_path, name, version_or_alias
        )

        if record is None:
            raise ValueError(
                f"Artifact {spec!r} not found in project '{self.project_dir}'."
            )
        self._check_artifact_type(spec, record["type"], type)

        art = Artifact(name=record["name"], type=record["type"])
        art._hydrate_from_db(
            project_dir=self.project_dir,
            version=record["version"],
            aliases=record["aliases"],
            manifest=record["manifest"],
            manifest_digest=record["manifest_digest"],
            size_bytes=record["size_bytes"],
            description=record["description"],
            metadata=record["metadata"],
        )

        try:
            SQLiteStorage.insert_run_artifact_link(
                db_path=self.db_path,
                run_name=self.name,
                run_id=self.id,
                version_id=record["version_id"],
                direction="input",
            )
        except Exception as e:
            self._warn_once(
                "artifact-use-lineage",
                f"trackio could not record consumer lineage for {spec!r}: {e}",
            )

        return art

    def alert(
        self,
        title: str,
        text: str | None = None,
        level: AlertLevel = AlertLevel.WARN,
        step: int | None = None,
        webhook_url: str | None = None,
    ):
        try:
            if step is None:
                step = max(self._next_step - 1, 0)
            timestamp = datetime.now(timezone.utc).isoformat()

            print(format_alert_terminal(level, title, text, step))

            alert_entry: AlertEntry = {
                "run": self.name,
                "run_id": self.id,
                "title": title,
                "text": text,
                "level": level.value,
                "step": step,
                "timestamp": timestamp,
                "alert_id": uuid.uuid4().hex,
            }

            with self._client_lock:
                self._queued_alerts.append(alert_entry)
                self._ensure_sender_alive()
                if not self._thread_is_alive("_local_sender_thread"):
                    self._flush_queues_inline()

            url = webhook_url or self._webhook_url
            if url and should_send_webhook(level, self._webhook_min_level):
                t = threading.Thread(
                    target=send_webhook,
                    args=(
                        url,
                        level,
                        title,
                        text,
                        self.project_dir.name,
                        self.name,
                        step,
                        timestamp,
                    ),
                    daemon=True,
                )
                t.start()
        except Exception as e:
            _emit_nonfatal_warning(f"trackio.alert() failed: {e}")

    def log_system(self, metrics: dict):
        try:
            metrics = utils.serialize_values(metrics)
            timestamp = datetime.now(timezone.utc).isoformat()

            system_log_entry: SystemLogEntry = {
                "run": self.name,
                "run_id": self.id,
                "metrics": metrics,
                "timestamp": timestamp,
                "log_id": uuid.uuid4().hex,
            }

            with self._client_lock:
                self._queued_system_logs.append(system_log_entry)
                self._ensure_sender_alive()
                if not self._thread_is_alive("_local_sender_thread"):
                    self._flush_queues_inline()
        except Exception as e:
            _emit_nonfatal_warning(f"trackio.log_system() failed: {e}")

    def finish(self):
        try:
            if self._gpu_monitor is not None:
                try:
                    self._gpu_monitor.stop()
                except Exception as e:
                    self._warn_once(
                        "finish-gpu-monitor",
                        f"trackio.finish() could not stop automatic GPU logging cleanly: {e}.",
                    )

            if self._cpu_monitor is not None:
                try:
                    self._cpu_monitor.stop()
                except Exception as e:
                    self._warn_once(
                        "finish-cpu-monitor",
                        f"trackio.finish() could not stop automatic CPU logging cleanly: {e}.",
                    )

            self._stop_flag.set()

            if self._local_sender_thread is not None:
                print("* Run finished. Uploading logs to Trackio (please wait...)")
                self._local_sender_thread.join(timeout=30)
                if self._local_sender_thread.is_alive():
                    _emit_nonfatal_warning(
                        "Could not flush all logs within 30s. Some data may be buffered locally."
                    )
            else:
                with self._client_lock:
                    self._flush_queues_inline()
        except Exception as e:
            _emit_nonfatal_warning(f"trackio.finish() failed: {e}")
