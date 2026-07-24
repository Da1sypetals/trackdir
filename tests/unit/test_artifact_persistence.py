import sqlite3

from trackio.sqlite_storage import SQLiteStorage
from trackio.utils import get_db_path


def _build_sample_artifacts(db_path):
    model_id = SQLiteStorage.create_or_get_artifact(
        db_path, "model", "model", "a model"
    )
    v0_id, _, _ = SQLiteStorage.insert_artifact_version(
        db_path,
        model_id,
        [{"path": "w.bin", "digest": "a" * 64, "size": 5}],
        {"epoch": 1},
        "rid-train",
        "train",
    )
    SQLiteStorage.reassign_alias(db_path, model_id, "latest", v0_id)
    SQLiteStorage.insert_run_artifact_link(
        db_path, "train", "rid-train", v0_id, "output"
    )
    v1_id, _, _ = SQLiteStorage.insert_artifact_version(
        db_path,
        model_id,
        [{"path": "w.bin", "digest": "b" * 64, "size": 7}],
        {"epoch": 2},
        "rid-train",
        "train",
    )
    SQLiteStorage.reassign_alias(db_path, model_id, "latest", v1_id)
    SQLiteStorage.reassign_alias(db_path, model_id, "best", v1_id)
    SQLiteStorage.insert_run_artifact_link(
        db_path, "train", "rid-train", v1_id, "output"
    )
    SQLiteStorage.insert_run_artifact_link(db_path, "eval", "rid-eval", v1_id, "input")

    data_id = SQLiteStorage.create_or_get_artifact(db_path, "data", "dataset", None)
    d0_id, _, _ = SQLiteStorage.insert_artifact_version(
        db_path,
        data_id,
        [{"path": "d.csv", "digest": "c" * 64, "size": 3}],
        None,
        "rid-prep",
        "prep",
    )
    SQLiteStorage.reassign_alias(db_path, data_id, "latest", d0_id)
    SQLiteStorage.insert_run_artifact_link(db_path, "prep", "rid-prep", d0_id, "output")


def _snapshot(db_path):
    snap = {"versions": {}, "manifests": {}, "lineage": {}}
    for name in ("data", "model"):
        latest = SQLiteStorage.get_artifact_manifest(db_path, name, None)
        if latest is None:
            continue
        latest["aliases"] = sorted(latest["aliases"])
        snap["manifests"][name] = latest
        versions = []
        v = 0
        while (
            record := SQLiteStorage.get_artifact_manifest(db_path, name, f"v{v}")
        ) is not None:
            record["aliases"] = sorted(record["aliases"])
            versions.append(record)
            v += 1
        snap["versions"][name] = versions
    for run_name, run_id in (
        ("train", "rid-train"),
        ("eval", "rid-eval"),
        ("prep", "rid-prep"),
    ):
        snap["lineage"][run_name] = SQLiteStorage.get_run_artifacts(
            db_path, run_name, run_id
        )
    return snap


def test_artifact_metadata_survives_db_reopen(temp_dir):
    """Artifact records persist across connections to the same database file."""
    db_path = get_db_path(temp_dir / "proj")
    SQLiteStorage.init_db(db_path)
    SQLiteStorage.bulk_log(db_path=db_path, run="train", metrics_list=[{"loss": 0.1}])
    _build_sample_artifacts(db_path)

    before = _snapshot(db_path)
    assert set(before["manifests"]) == {"data", "model"}
    assert before["manifests"]["model"]["version"] == 1
    assert "best" in before["manifests"]["model"]["aliases"]
    assert len(before["lineage"]["train"]["output"]) == 2
    assert len(before["lineage"]["eval"]["input"]) == 1

    # Force data out of page cache by reading through a fresh raw connection.
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0]
    assert count == 3

    assert _snapshot(db_path) == before
