"""Adapt core progress events to the framework-neutral application port."""

from __future__ import annotations

from tifq.application.dto import OperationStatus
from tifq.application.ports import ProgressSink
from tifq.runtime.progress import ProgressCallback, ProgressUpdate


def progress_callback(sink: ProgressSink | None) -> ProgressCallback | None:
    if sink is None:
        return None

    def emit(update: ProgressUpdate) -> None:
        ratio = None
        total = update.total
        if total is not None and total != 0:
            ratio = update.completed / total
        sink(
            OperationStatus(
                operation=update.operation,
                state="complete" if ratio == 1 else "running",
                message=update.message,
                progress=ratio,
                completed=update.completed,
                total=update.total,
                elapsed_seconds=update.elapsed_seconds,
                eta_seconds=update.eta_seconds,
            )
        )

    return emit
