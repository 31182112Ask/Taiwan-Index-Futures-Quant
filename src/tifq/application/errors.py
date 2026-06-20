"""Application-layer errors safe for every external interface."""


class ApplicationError(RuntimeError):
    """Base application orchestration error."""


class StalePreparedBacktestError(ApplicationError):
    """Raised when prepared data no longer matches current inputs."""
