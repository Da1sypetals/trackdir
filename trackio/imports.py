import csv
from pathlib import Path

from trackio import utils
from trackio.sqlite_storage import SQLiteStorage


def import_csv(
    csv_path: str | Path,
    dir: str | Path,
    name: str | None = None,
) -> None:
    """
    Imports a CSV file into a Trackio project directory. The CSV file must contain a `"step"`
    column, may optionally contain a `"timestamp"` column, and any other columns will be
    treated as metrics. It should also include a header row with the column names.

    Args:
        csv_path (`str` or `Path`):
            The str or Path to the CSV file to import.
        dir (`str` or `Path`):
            The project directory to import the CSV file into. Must not already
            contain runs.
        name (`str`, *optional*):
            The name of the run (if not provided, the CSV file stem is used).
    """
    db_path = utils.get_db_path(dir)
    project_dir = Path(dir).expanduser().resolve()

    if SQLiteStorage.get_runs(db_path):
        raise ValueError(
            f"Project '{project_dir}' already contains runs. Cannot import CSV into an existing project."
        )

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        source_columns = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        raise ValueError("CSV file is empty")

    column_mapping = utils.simplify_column_names(source_columns)
    normalized_rows = [
        {column_mapping[key]: value for key, value in row.items()} for row in rows
    ]
    columns = list(normalized_rows[0].keys())

    step_column = None
    for col in columns:
        if col.lower() == "step":
            step_column = col
            break

    if step_column is None:
        raise ValueError("CSV file must contain a 'step' or 'Step' column")

    if name is None:
        name = csv_path.stem

    metrics_list = []
    steps = []
    timestamps = []

    numeric_columns = []
    for column in columns:
        if column == step_column:
            continue
        if column == "timestamp":
            continue

        try:
            for row in normalized_rows:
                value = row[column]
                if value in ("", None):
                    continue
                float(value)
        except (ValueError, TypeError):
            continue
        numeric_columns.append(column)

    for row in normalized_rows:
        metrics = {}
        for column in numeric_columns:
            value = row[column]
            if value not in ("", None):
                metrics[column] = float(value)

        if metrics:
            metrics_list.append(metrics)
            steps.append(int(float(row[step_column])))

            if "timestamp" in row and row["timestamp"] not in ("", None):
                timestamps.append(str(row["timestamp"]))
            else:
                timestamps.append("")

    if not metrics_list:
        raise ValueError(
            f"No numeric metric data found in CSV file: {csv_path}. Columns other "
            "than 'step' and 'timestamp' must contain numeric values."
        )

    SQLiteStorage.bulk_log(
        db_path=db_path,
        run=name,
        metrics_list=metrics_list,
        steps=steps,
        timestamps=timestamps,
    )

    print(
        f"* Imported {len(metrics_list)} rows from {csv_path} into '{project_dir}' as run '{name}'"
    )
    print(f"* Metrics found: {', '.join(metrics_list[0].keys())}")
    utils.print_dashboard_instructions(project_dir)


def import_tf_events(
    log_dir: str | Path,
    dir: str | Path,
    name: str | None = None,
) -> None:
    """
    Imports TensorFlow Events files from a directory into a Trackio project
    directory. Each subdirectory in the log directory will be imported as a
    separate run.

    Args:
        log_dir (`str` or `Path`):
            The str or Path to the directory containing TensorFlow Events files.
        dir (`str` or `Path`):
            The project directory to import the TensorFlow Events files into.
            Must not already contain runs.
        name (`str`, *optional*):
            The name prefix for runs (if not provided, will use directory names). Each
            subdirectory will create a separate run.
    """
    try:
        from tbparse import SummaryReader
    except ImportError:
        raise ImportError(
            "The `tbparse` package is not installed but is required for `import_tf_events`. Please install trackio with the `tensorboard` extra: `pip install trackio[tensorboard]`."
        )

    db_path = utils.get_db_path(dir)
    project_dir = Path(dir).expanduser().resolve()

    if SQLiteStorage.get_runs(db_path):
        raise ValueError(
            f"Project '{project_dir}' already contains runs. Cannot import TF events into an existing project."
        )

    path = Path(log_dir)
    if not path.exists():
        raise FileNotFoundError(f"TF events directory not found: {path}")

    reader = SummaryReader(str(path), extra_columns={"dir_name"})
    df = reader.scalars

    if df.empty:
        raise ValueError(f"No TensorFlow events data found in {path}")

    total_imported = 0
    imported_runs = []

    for dir_name, group_df in df.groupby("dir_name"):
        try:
            if dir_name == "":
                run_name = "main"
            else:
                run_name = dir_name

            if name:
                run_name = f"{name}_{run_name}"

            if group_df.empty:
                print(f"* Skipping directory {dir_name}: no scalar data found")
                continue

            metrics_list = []
            steps = []
            timestamps = []

            for _, row in group_df.iterrows():
                tag = str(row["tag"])
                value = float(row["value"])
                step = int(row["step"])

                metrics = {tag: value}
                metrics_list.append(metrics)
                steps.append(step)

                if "wall_time" in group_df.columns and not utils.is_missing_value(
                    row["wall_time"]
                ):
                    timestamps.append(str(row["wall_time"]))
                else:
                    timestamps.append("")

            if metrics_list:
                SQLiteStorage.bulk_log(
                    db_path=db_path,
                    run=str(run_name),
                    metrics_list=metrics_list,
                    steps=steps,
                    timestamps=timestamps,
                )

                total_imported += len(metrics_list)
                imported_runs.append(run_name)

                print(
                    f"* Imported {len(metrics_list)} scalar events from directory '{dir_name}' as run '{run_name}'"
                )
                print(f"* Metrics in this run: {', '.join(set(group_df['tag']))}")

        except Exception as e:
            print(f"* Error processing directory {dir_name}: {e}")
            continue

    if not imported_runs:
        raise ValueError("No valid TensorFlow events data could be imported")

    print(f"* Total imported events: {total_imported}")
    print(f"* Created runs: {', '.join(imported_runs)}")
    utils.print_dashboard_instructions(project_dir)
