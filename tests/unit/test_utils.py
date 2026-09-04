import os
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from trackio import utils


def test_generate_readable_names_are_unique_even_with_seed():
    names = []
    for _ in range(10):
        random.seed(42)
        names.append(utils.generate_readable_name(names))
    assert len(names) == len(set(names))


def test_sort_metrics_by_prefix():
    metrics = ["train/loss", "loss", "train/acc", "val/loss", "accuracy"]
    result = utils.sort_metrics_by_prefix(metrics)
    expected = ["accuracy", "loss", "train/acc", "train/loss", "val/loss"]
    assert result == expected


def test_group_metrics_by_prefix():
    metrics = ["loss", "accuracy", "train/loss", "train/acc", "val/loss", "test/f1"]
    result = utils.group_metrics_by_prefix(metrics)
    expected = {
        "charts": ["accuracy", "loss"],
        "test": ["test/f1"],
        "train": ["train/acc", "train/loss"],
        "val": ["val/loss"],
    }
    assert result == expected


def test_group_metrics_with_subprefixes():
    metrics = [
        "loss",
        "train/acc",
        "train/loss/normalized",
        "train/loss/unnormalized",
        "val/loss",
        "test/f1/micro",
        "test/f1/macro",
    ]
    _, result = utils.order_metrics_by_plot_preference(metrics)
    expected = {
        "charts": {"direct_metrics": ["loss"], "subgroups": {}},
        "train": {
            "direct_metrics": ["train/acc"],
            "subgroups": {"loss": ["train/loss/normalized", "train/loss/unnormalized"]},
        },
        "val": {"direct_metrics": ["val/loss"], "subgroups": {}},
        "test": {
            "direct_metrics": [],
            "subgroups": {"f1": ["test/f1/macro", "test/f1/micro"]},
        },
    }
    assert result == expected


def test_format_timestamp():
    now = datetime.now(timezone.utc)

    two_minutes_ago = (now - timedelta(minutes=2)).isoformat()
    assert utils.format_timestamp(two_minutes_ago) == "2 minutes ago"

    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    assert utils.format_timestamp(one_hour_ago) == "1 hour ago"

    two_days_ago = (now - timedelta(days=2)).isoformat()
    assert utils.format_timestamp(two_days_ago) == "2 days ago"

    thirty_seconds_ago = (now - timedelta(seconds=30)).isoformat()
    assert utils.format_timestamp(thirty_seconds_ago) == "Just now"

    assert utils.format_timestamp(None) == "Unknown"
    assert utils.format_timestamp("invalid") == "Unknown"


@pytest.mark.parametrize(
    "obj, expected",
    [
        ("hello", "hello"),
        ({"key": "value", "num": 123}, {"key": "value", "num": 123}),
        ([1, 2, "three"], [1, 2, "three"]),
        ((4, 5, 6), [4, 5, 6]),
        ({7, 8, 9}, {7, 8, 9}),
        ({"nested": {"dict": [1, 2]}}, {"nested": {"dict": [1, 2]}}),
    ],
)
def test_to_json_safe(obj, expected):
    result = utils.to_json_safe(obj)
    if isinstance(obj, set):
        assert set(result) == expected
    else:
        assert result == expected


def test_to_json_safe_with_object():
    class LoraConfig:
        def __init__(self):
            self.r = 8
            self.lora_alpha = 16
            self.target_modules = ["q_proj", "v_proj"]
            self.lora_dropout = 0.1
            self.bias = "none"
            self.task_type = "CAUSAL_LM"
            self._private_config = "hidden"

    lora_config = LoraConfig()
    assert utils.to_json_safe(lora_config) == {
        "r": 8,
        "lora_alpha": 16,
        "target_modules": ["q_proj", "v_proj"],
        "lora_dropout": 0.1,
        "bias": "none",
        "task_type": "CAUSAL_LM",
    }


def test_plot_ordering():
    """Test that TRACKIO_PLOT_ORDER environment variable correctly orders metrics."""
    metrics = [
        "test/f1",
        "train/accuracy",
        "val/loss",
        "train/loss",
        "val/accuracy",
        "charts_metric",
        "test/precision",
        "eval/metric",
    ]

    # Test 1: No environment variable set - should be alphabetical by group
    with patch.dict(os.environ, {}, clear=True):
        group_order, result = utils.order_metrics_by_plot_preference(metrics)
        assert group_order == ["charts", "eval", "test", "train", "val"]
        assert result["train"]["direct_metrics"] == ["train/accuracy", "train/loss"]
        assert result["val"]["direct_metrics"] == ["val/accuracy", "val/loss"]

    # Test 2: Specific metric order
    with patch.dict(
        os.environ, {"TRACKIO_PLOT_ORDER": "train/loss,val/loss"}, clear=True
    ):
        group_order, result = utils.order_metrics_by_plot_preference(metrics)
        assert group_order == ["train", "val", "charts", "eval", "test"]
        assert result["train"]["direct_metrics"] == ["train/loss", "train/accuracy"]
        assert result["val"]["direct_metrics"] == ["val/loss", "val/accuracy"]

    # Test 3: Group wildcards with specific metrics first
    with patch.dict(
        os.environ, {"TRACKIO_PLOT_ORDER": "val/loss,train/*,val/*"}, clear=True
    ):
        group_order, result = utils.order_metrics_by_plot_preference(metrics)
        assert group_order == ["val", "train", "charts", "eval", "test"]
        assert result["train"]["direct_metrics"] == ["train/accuracy", "train/loss"]
        assert result["val"]["direct_metrics"] == ["val/loss", "val/accuracy"]

    # Test 4: Mixed priorities
    with patch.dict(
        os.environ, {"TRACKIO_PLOT_ORDER": "test/*,train/loss,val/*"}, clear=True
    ):
        group_order, result = utils.order_metrics_by_plot_preference(metrics)
        assert group_order == ["test", "train", "val", "charts", "eval"]
        assert result["train"]["direct_metrics"] == ["train/loss", "train/accuracy"]
        assert result["test"]["direct_metrics"] == ["test/f1", "test/precision"]

    # Test 5: With spaces in environment variable
    with patch.dict(
        os.environ, {"TRACKIO_PLOT_ORDER": " val/loss , train/* , test/f1 "}, clear=True
    ):
        group_order, result = utils.order_metrics_by_plot_preference(metrics)
        assert group_order == ["val", "train", "test", "charts", "eval"]
        assert result["val"]["direct_metrics"] == ["val/loss", "val/accuracy"]
        assert result["test"]["direct_metrics"] == ["test/f1", "test/precision"]


def test_downsample_with_none_x_lim():
    """Test downsample function handles None values in x_lim correctly."""
    rows = [
        {"x": 0, "y": 1},
        {"x": 1, "y": 2},
        {"x": 2, "y": 3},
        {"x": 3, "y": 4},
        {"x": 4, "y": 5},
        {"x": 5, "y": 6},
        {"x": 6, "y": 7},
        {"x": 7, "y": 8},
        {"x": 8, "y": 9},
        {"x": 9, "y": 10},
        {"x": 10, "y": 11},
    ]

    result_df, result_x_lim = utils.downsample(rows, "x", "y", None, None)
    assert result_x_lim is None
    assert len(result_df) <= len(rows)

    result_df, result_x_lim = utils.downsample(rows, "x", "y", None, (None, 5))
    assert result_x_lim == (0, 5)
    assert len(result_df) <= len(rows)

    result_df, result_x_lim = utils.downsample(rows, "x", "y", None, (2, None))
    assert result_x_lim == (2, 10)
    assert len(result_df) <= len(rows)

    result_df, result_x_lim = utils.downsample(rows, "x", "y", None, (None, None))
    assert result_x_lim == (0, 10)
    assert len(result_df) <= len(rows)

    empty_df = []
    result_df, result_x_lim = utils.downsample(empty_df, "x", "y", None, (2, None))
    assert result_x_lim == (2, 0)
    assert len(result_df) == 0


def _log_one_metric(db_path, run="mel_ae", loss=0.5, step=0):
    from trackio.sqlite_storage import SQLiteStorage

    SQLiteStorage.bulk_log(
        db_path=db_path,
        run=run,
        metrics_list=[{"loss": loss}],
        steps=[step],
    )


def test_get_db_path_is_canonical_trackio_db(temp_dir):
    project_dir = temp_dir / "track"
    assert utils.get_db_path(project_dir) == (project_dir / "trackio.db")


def test_resolve_db_path_empty_directory_uses_canonical(temp_dir):
    project_dir = temp_dir / "track"
    project_dir.mkdir()
    assert utils.resolve_db_path(project_dir) == project_dir / "trackio.db"


def test_resolve_db_path_missing_directory_uses_canonical(temp_dir):
    project_dir = temp_dir / "does_not_exist"
    assert utils.resolve_db_path(project_dir) == project_dir / "trackio.db"


def test_resolve_db_path_canonical_with_data(temp_dir):
    project_dir = temp_dir / "track"
    project_dir.mkdir()
    _log_one_metric(project_dir / "trackio.db")
    assert utils.resolve_db_path(project_dir) == project_dir / "trackio.db"


def test_resolve_db_path_legacy_project_named_db(temp_dir):
    project_dir = temp_dir / "track"
    project_dir.mkdir()
    legacy = project_dir / "mel_ae.db"
    _log_one_metric(legacy)
    assert utils.resolve_db_path(project_dir) == legacy


def test_resolve_db_path_skips_empty_canonical_when_legacy_has_data(temp_dir):
    from trackio.sqlite_storage import SQLiteStorage

    project_dir = temp_dir / "track"
    project_dir.mkdir()
    SQLiteStorage.init_db(project_dir / "trackio.db")
    legacy = project_dir / "mel_ae.db"
    _log_one_metric(legacy, step=12)
    assert utils.resolve_db_path(project_dir) == legacy


def test_resolve_db_path_configs_only_counts_as_data(temp_dir):
    import sqlite3

    from trackio.sqlite_storage import SQLiteStorage

    project_dir = temp_dir / "track"
    project_dir.mkdir()
    legacy = project_dir / "mel_ae.db"
    SQLiteStorage.init_db(legacy)
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "INSERT INTO configs (run_id, run_name, config, created_at) VALUES (?, ?, ?, ?)",
            ("run1", "run1", "{}", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    assert utils.resolve_db_path(project_dir) == legacy


def test_resolve_db_path_ignores_unrelated_sqlite_file(temp_dir):
    import sqlite3

    project_dir = temp_dir / "track"
    project_dir.mkdir()
    other = project_dir / "other.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()
    legacy = project_dir / "mel_ae.db"
    _log_one_metric(legacy)
    assert utils.resolve_db_path(project_dir) == legacy


def test_resolve_db_path_multiple_populated_databases_raises(temp_dir):
    project_dir = temp_dir / "track"
    project_dir.mkdir()
    _log_one_metric(project_dir / "mel_ae.db")
    _log_one_metric(project_dir / "other.db", run="other")
    with pytest.raises(RuntimeError, match="multiple Trackio databases with data"):
        utils.resolve_db_path(project_dir)
