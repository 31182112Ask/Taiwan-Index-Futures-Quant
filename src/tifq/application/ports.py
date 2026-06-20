"""Small replaceable ports used by application services."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from tifq.application.dto import OperationStatus, ResultSummaryDTO


class ProgressSink(Protocol):
    def __call__(self, update: OperationStatus) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ResultRepository(Protocol):
    def list_runs(self) -> Sequence[ResultSummaryDTO]: ...

    def load_run(self, run_id: str) -> Any: ...
