import pytest

from trackio.media import TrackioImage
from trackio.table import Table

RUN_NAME = "test_run"


def test_table_to_dict_with_images(image_ndarray, temp_dir):
    img = TrackioImage(image_ndarray, caption="Mixed Test")
    table = Table(
        columns=["step", "image", "text", "number"],
        data=[
            [1, img, "hello", 1.5],
            [2, None, "world", 2.5],
            [3, img, "test", 3.5],
        ],
    )
    result = table._to_dict(project_dir=temp_dir, run=RUN_NAME, step=5)

    assert result["_type"] == Table.TYPE
    assert len(result["_value"]) == 3

    row1 = result["_value"][0]
    assert row1["step"] == 1
    assert row1["text"] == "hello"
    assert row1["number"] == 1.5
    assert isinstance(row1["image"], dict)
    assert row1["image"]["_type"] == TrackioImage.TYPE

    row2 = result["_value"][1]
    assert row2["step"] == 2
    assert row2["text"] == "world"
    assert row2["number"] == 2.5
    assert row2["image"] is None

    row3 = result["_value"][2]
    assert row3["step"] == 3
    assert row3["text"] == "test"
    assert row3["number"] == 3.5
    assert isinstance(row3["image"], dict)
    assert row3["image"]["_type"] == TrackioImage.TYPE


def test_table_accepts_pandas_dataframe_if_installed(temp_dir):
    pd = pytest.importorskip("pandas")

    df = pd.DataFrame({"step": [1], "text": ["hello"]})
    table = Table(dataframe=df)

    assert table._to_dict(project_dir=temp_dir, run=RUN_NAME)["_value"] == [
        {"step": 1, "text": "hello"}
    ]
