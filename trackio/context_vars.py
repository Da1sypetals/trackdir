import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trackio.run import Run

current_run: contextvars.ContextVar["Run | None"] = contextvars.ContextVar(
    "current_run", default=None
)
current_project_dir: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_project_dir", default=None
)
