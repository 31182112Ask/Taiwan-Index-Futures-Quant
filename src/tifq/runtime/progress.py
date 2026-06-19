"""Framework-neutral progress updates with elapsed time and conservative ETA."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from time import perf_counter


@dataclass(frozen=True)
class ProgressUpdate:
    """One immutable operation progress event."""

    operation: str
    phase: str
    completed: int
    total: int | None
    message: str
    elapsed_seconds: float
    eta_seconds: float | None
    throughput: float | None

    @property
    def percent(self) -> float | None:
        """Return a bounded completion fraction when total is known."""
        if self.total is None or self.total <= 0:
            return None
        return min(1.0, max(0.0, self.completed / self.total))


ProgressCallback = Callable[[ProgressUpdate], None]


class ProgressReporter:
    """Create consistent progress events without affecting core operation results."""

    def __init__(
        self,
        operation: str,
        callback: ProgressCallback | None = None,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.operation = operation
        self.callback = callback
        self._clock = clock
        self._started = clock()
        self._last_event_time = self._started
        self._last_completed = 0
        self._unit_durations: deque[float] = deque(maxlen=20)

    def update(
        self,
        phase: str,
        completed: int,
        total: int | None,
        message: str,
    ) -> ProgressUpdate:
        """Build and safely emit an update; callback failures are isolated."""
        bounded_completed = max(0, completed)
        if total is not None:
            bounded_completed = min(bounded_completed, max(0, total))

        now = self._clock()
        delta_units = bounded_completed - self._last_completed
        if delta_units > 0:
            duration = max(0.0, now - self._last_event_time) / delta_units
            self._unit_durations.extend([duration] * delta_units)
        self._last_event_time = now
        self._last_completed = bounded_completed

        elapsed = max(0.0, now - self._started)
        throughput = bounded_completed / elapsed if bounded_completed > 0 and elapsed > 0 else None
        eta = self._eta(bounded_completed, total)
        update = ProgressUpdate(
            operation=self.operation,
            phase=phase,
            completed=bounded_completed,
            total=total,
            message=message,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            throughput=throughput,
        )
        if self.callback is not None:
            try:
                self.callback(update)
            except Exception:
                # Progress presentation must never change deterministic core results.
                pass
        return update

    def _eta(self, completed: int, total: int | None) -> float | None:
        if total is None or total <= completed or len(self._unit_durations) < 2:
            return None
        return max(0.0, median(self._unit_durations) * (total - completed))
