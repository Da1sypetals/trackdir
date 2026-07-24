import pytest

from trackio import Run, Trace
from trackio.media import TrackioImage
from trackio.sqlite_storage import SQLiteStorage
from trackio.utils import get_db_path


def test_trace_to_dict(image_ndarray, temp_dir):
    image = TrackioImage(image_ndarray, caption="browser screenshot")
    trace = Trace(
        messages=[
            {"role": "system", "content": "You are a browser agent."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What do you see?"},
                    image,
                ],
            },
        ],
        metadata={"label": "demo-trace"},
    )

    payload = trace._to_dict(project_dir=temp_dir, run="run1", step=0)

    assert payload["_type"] == Trace.TYPE
    assert payload["metadata"]["label"] == "demo-trace"
    assert payload["messages"][1]["content"][1]["_type"] == "trackio.image"


def test_trace_requires_message_dicts():
    with pytest.raises(TypeError, match="list of dictionaries"):
        Trace(messages=["bad"])  # type: ignore[arg-type]


def test_trace_logging_and_query(temp_dir):
    run = Run(project_dir=temp_dir, name="trace-run")
    run.log(
        {
            "conversation": Trace(
                messages=[
                    {"role": "system", "content": "Answer directly."},
                    {"role": "user", "content": "What is the capital of Australia?"},
                    {"role": "assistant", "content": "Sydney."},
                ],
                metadata={"label": "candidate-a", "group": "capitals"},
            )
        }
    )
    run.log(
        {
            "conversation": Trace(
                messages=[
                    {"role": "system", "content": "Answer directly."},
                    {"role": "user", "content": "What is the capital of Australia?"},
                    {"role": "assistant", "content": "Canberra."},
                ],
                metadata={"label": "candidate-b", "group": "capitals"},
            )
        }
    )
    run.finish()

    db_path = get_db_path(temp_dir)
    logs = SQLiteStorage.get_logs(db_path, "trace-run")
    assert "conversation" not in logs[0]

    traces = SQLiteStorage.get_traces(db_path, "trace-run", sort="step_desc")
    assert len(traces) == 2
    assert traces[0]["messages"][2]["content"] == "Canberra."

    searched = SQLiteStorage.get_traces(db_path, "trace-run", search="canberra")
    assert len(searched) == 1
    assert searched[0]["metadata"]["label"] == "candidate-b"


def test_trace_limit_offset_are_applied_in_storage(temp_dir):
    run = Run(project_dir=temp_dir, name="trace-run")
    for index in range(5):
        run.log(
            {
                "conversation": Trace(
                    messages=[
                        {"role": "user", "content": f"question {index}"},
                        {"role": "assistant", "content": f"answer {index}"},
                    ],
                    metadata={"index": index},
                )
            }
        )
    run.finish()

    db_path = get_db_path(temp_dir)
    traces = SQLiteStorage.get_traces(
        db_path, "trace-run", sort="step_asc", limit=2, offset=2
    )
    assert [trace["metadata"]["index"] for trace in traces] == [2, 3]


def test_trace_logging_keeps_scalar_metrics_separate(temp_dir):
    run = Run(project_dir=temp_dir, name="trace-run")
    run.log(
        {
            "loss": 0.5,
            "conversation": Trace(
                messages=[
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
            ),
        }
    )
    run.finish()

    db_path = get_db_path(temp_dir)
    logs = SQLiteStorage.get_logs(db_path, "trace-run")
    assert logs[0]["loss"] == 0.5
    assert "conversation" not in logs[0]
    assert len(SQLiteStorage.get_traces(db_path, "trace-run")) == 1
