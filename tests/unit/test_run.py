import sqlite3

import pytest

from trackio import Markdown, Run, init, utils
from trackio.sqlite_storage import SQLiteStorage
from trackio.utils import get_db_path


def test_run_log_writes_to_sqlite_locally(temp_dir):
    run = Run(project_dir=temp_dir, name="run1")
    metrics = {"x": 1}
    run.log(metrics)
    run.finish()

    db_path = get_db_path(temp_dir)
    logs = SQLiteStorage.get_logs(db_path, "run1")
    assert len(logs) == 1
    assert logs[0]["x"] == 1
    assert logs[0]["step"] == 0

    config = SQLiteStorage.get_run_config(db_path, "run1")
    assert config is not None
    assert run.id is not None


def test_markdown_logging(temp_dir):
    run = Run(project_dir=temp_dir, name="run-report")
    run.log({"loss": 0.1, "summary": Markdown("# Training summary")})
    run.finish()

    db_path = get_db_path(temp_dir)
    logs = SQLiteStorage.get_logs(db_path, "run-report")

    markdown_entries = [
        entry
        for entry in logs
        if isinstance(entry.get("summary"), dict)
        and entry["summary"].get("_type") == Markdown.TYPE
    ]
    assert len(markdown_entries) == 1
    assert markdown_entries[0]["summary"]["_value"] == "# Training summary"


def test_init_resume_modes(temp_dir):
    project_dir = temp_dir / "proj"
    run = init(
        dir=project_dir,
        name="new-run",
        resume="never",
    )
    assert isinstance(run, Run)
    assert run.name == "new-run"

    run.log({"x": 1})
    run.finish()
    first_run_id = run.id

    run = init(
        dir=project_dir,
        name="new-run",
        resume="must",
    )
    assert isinstance(run, Run)
    assert run.name == "new-run"
    assert run.id == first_run_id

    run = init(
        dir=project_dir,
        name="new-run",
        resume="allow",
    )
    assert isinstance(run, Run)
    assert run.name == "new-run"
    assert run.id == first_run_id

    run = init(
        dir=project_dir,
        name="new-run",
        resume="never",
    )
    assert isinstance(run, Run)
    assert run.name == "new-run"
    assert run.id != first_run_id

    with pytest.raises(ValueError, match="does not exist"):
        init(
            dir=project_dir,
            name="nonexistent-run",
            resume="must",
        )

    run = init(
        dir=project_dir,
        name="nonexistent-run",
        resume="allow",
    )
    assert isinstance(run, Run)
    assert run.name == "nonexistent-run"


def test_resume_allow_and_must_use_latest_run_with_same_name(temp_dir):
    project_dir = temp_dir / "dup"
    first = init(dir=project_dir, name="same-name", resume="never")
    first.log({"x": 1})
    first.finish()

    second = init(dir=project_dir, name="same-name", resume="never")
    second.log({"x": 2})
    second.finish()

    allowed = init(dir=project_dir, name="same-name", resume="allow")
    assert allowed.id == second.id

    required = init(dir=project_dir, name="same-name", resume="must")
    assert required.id == second.id


def test_reserved_config_keys_rejected(temp_dir):
    with pytest.raises(ValueError, match="Config key '_test' is reserved"):
        Run(
            project_dir=temp_dir,
            config={"_test": "value"},
        )


def test_step_recovery_after_crash(temp_dir):
    db_path = get_db_path(temp_dir)
    SQLiteStorage.bulk_log(
        db_path,
        "run1",
        [{"loss": 0.5}, {"loss": 0.4}, {"loss": 0.3}],
        run_id="run-1-id",
    )

    run = Run(
        project_dir=temp_dir,
        name="run1",
        run_id="run-1-id",
    )
    assert run._next_step == 3

    run.log({"loss": 0.2})
    run.finish()

    logs = SQLiteStorage.get_logs(db_path, "run1", run_id="run-1-id")
    assert len(logs) == 4
    assert logs[3]["step"] == 3


def test_run_group_added(temp_dir):
    run = Run(
        project_dir=temp_dir,
        group="test_group",
        config={"learning_rate": 0.01},
    )
    assert run.config["_Group"] == "test_group"


def test_log_does_not_crash_on_bad_metrics(temp_dir, monkeypatch):
    run = Run(project_dir=temp_dir, name="safe-run")

    original = utils.serialize_values

    def exploding_serialize(metrics):
        if "bad" in metrics:
            raise RuntimeError("serialize boom")
        return original(metrics)

    monkeypatch.setattr(utils, "serialize_values", exploding_serialize)

    with pytest.warns(UserWarning, match="trackio.log\\(\\) failed to process metrics"):
        run.log({"bad": 1})

    run.log({"loss": 0.5})
    run.finish()

    db_path = get_db_path(temp_dir)
    logs = SQLiteStorage.get_logs(db_path, "safe-run")
    assert len(logs) == 1
    assert logs[0]["loss"] == 0.5


def test_init_survives_storage_read_failures(temp_dir, monkeypatch):
    def raise_db_error(*args, **kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(SQLiteStorage, "get_runs", raise_db_error)
    monkeypatch.setattr(SQLiteStorage, "get_latest_run_record_by_name", raise_db_error)
    monkeypatch.setattr(SQLiteStorage, "get_max_step_for_run", raise_db_error)

    with pytest.warns(UserWarning) as record:
        run = init(dir=temp_dir / "broken", name="safe-run")

    messages = [str(item.message) for item in record]
    assert any("could not inspect existing runs" in message for message in messages)
    assert any("could not recover the previous step" in message for message in messages)
    assert isinstance(run, Run)
    assert run.name == "safe-run"
    assert run._next_step == 0

    run.log({"loss": 0.5})
    run.finish()


def test_local_flush_failure_does_not_crash(temp_dir, monkeypatch):
    run = Run(project_dir=temp_dir, name="safe-run")

    def raise_db_error(*args, **kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(SQLiteStorage, "bulk_log", raise_db_error)

    run.log({"loss": 0.5})

    with pytest.warns(UserWarning, match="trackio failed to flush metric logs"):
        run.finish()
