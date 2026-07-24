from typing import Any, NewType, TypedDict

Sha256Digest = NewType("Sha256Digest", str)


class ManifestEntry(TypedDict):
    path: str
    digest: Sha256Digest
    size: int


Manifest = list[ManifestEntry]


class LogEntry(TypedDict, total=False):
    run: str
    run_id: str | None
    metrics: dict[str, Any]
    step: int | None
    config: dict[str, Any] | None
    log_id: str | None


class SystemLogEntry(TypedDict, total=False):
    run: str
    run_id: str | None
    metrics: dict[str, Any]
    timestamp: str
    log_id: str | None


class AlertEntry(TypedDict, total=False):
    run: str
    run_id: str | None
    title: str
    text: str | None
    level: str
    step: int | None
    timestamp: str
    alert_id: str | None
