from pathlib import Path
from typing import Iterator

from trackio import utils
from trackio.sqlite_storage import SQLiteStorage


class Run:
    def __init__(self, project_dir: str | Path, name: str, run_id: str | None = None):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._db_path = utils.get_db_path(self.project_dir)
        self.name = name
        self._id = run_id or name
        self._config = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def config(self) -> dict | None:
        if self._config is None:
            self._config = SQLiteStorage.get_run_config(
                self._db_path, self.name, run_id=self.id
            )
        return self._config

    def logs(self, max_points: int | None = None) -> list[dict]:
        return SQLiteStorage.get_logs(
            self._db_path, self.name, max_points=max_points, run_id=self.id
        )

    def alerts(self, level: str | None = None, since: str | None = None) -> list[dict]:
        return SQLiteStorage.get_alerts(
            self._db_path, run_name=self.name, run_id=self.id, level=level, since=since
        )

    def delete(self) -> bool:
        return SQLiteStorage.delete_run(self._db_path, self.name, run_id=self.id)

    def rename(self, new_name: str) -> "Run":
        SQLiteStorage.rename_run(self._db_path, self.name, new_name, run_id=self.id)
        self.name = new_name
        return self

    def __repr__(self) -> str:
        return f"<Run {self.name} in {self.project_dir}>"


class Runs:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._db_path = utils.get_db_path(self.project_dir)
        self._runs = None

    def _load_runs(self):
        if self._runs is None:
            records = SQLiteStorage.get_run_records(self._db_path)
            self._runs = [
                Run(self.project_dir, str(record["name"]), run_id=str(record["id"]))
                for record in records
            ]

    def __iter__(self) -> Iterator[Run]:
        self._load_runs()
        return iter(self._runs)

    def __getitem__(self, index: int) -> Run:
        self._load_runs()
        return self._runs[index]

    def __len__(self) -> int:
        self._load_runs()
        return len(self._runs)

    def __repr__(self) -> str:
        self._load_runs()
        return f"<Runs dir={self.project_dir} count={len(self._runs)}>"


class Api:
    def runs(self, dir: str | Path) -> Runs:
        db_path = utils.get_db_path(dir)
        if not db_path.exists():
            raise ValueError(f"No trackio project found at '{dir}'")
        return Runs(dir)

    def alerts(
        self,
        dir: str | Path,
        run: str | None = None,
        level: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        db_path = utils.get_db_path(dir)
        if not db_path.exists():
            raise ValueError(f"No trackio project found at '{dir}'")
        return SQLiteStorage.get_alerts(db_path, run_name=run, level=level, since=since)
