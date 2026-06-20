"""V1 application-boundary exports used by the legacy Streamlit adapter.

This compatibility surface keeps framework code from reaching into core packages while
the existing V1 interaction model remains frozen. New interfaces should use the services.
"""

# ruff: noqa: F401

from tifq.backtest import (
    BacktestPreflight,
    BacktestResult,
    persist_backtest_result,
    prepare_backtest,
    run_backtest_from_config,
)
from tifq.backtest.contracts import select_contract_bars
from tifq.backtest.engine import load_configured_bars
from tifq.backtest.metrics import MetricValue
from tifq.bars import build_bar_files, discover_tick_files
from tifq.config import ConfigLoadError, load_backtest_config
from tifq.config.models import BacktestConfig
from tifq.data import import_taifex_ticks
from tifq.data.storage import read_parquet
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexFetchError,
    TaifexFetchSummary,
    build_taifex_download_plan,
    plan_recent_taifex_csv_files,
    sync_recent_taifex_csv_files,
)
from tifq.indicators import append_basic_indicators
from tifq.runtime import (
    CleanupSummary,
    HealthReport,
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    run_environment_health_check,
)
from tifq.runtime.cleanup import CleanupAction
from tifq.runtime.locking import OperationLockError, format_lock_conflict
from tifq.runtime.progress import ProgressUpdate
from tifq.workflow import (
    WorkflowState,
    derive_workflow_state,
    discover_latest_matching_result,
    load_persisted_workflow_plan,
    persist_workflow_plan,
    raw_directory_fingerprint,
)

__all__ = [
    "BacktestConfig",
    "BacktestPreflight",
    "BacktestResult",
    "CleanupAction",
    "CleanupSummary",
    "ConfigLoadError",
    "HealthReport",
    "MetricValue",
    "OperationLockError",
    "ProgressUpdate",
    "TaifexDownloadPlan",
    "TaifexFetchError",
    "TaifexFetchSummary",
    "WorkflowState",
    "append_basic_indicators",
    "apply_confirmed_cleanup",
    "apply_safe_cleanup",
    "build_bar_files",
    "build_taifex_download_plan",
    "derive_workflow_state",
    "discover_latest_matching_result",
    "discover_tick_files",
    "format_lock_conflict",
    "import_taifex_ticks",
    "load_backtest_config",
    "load_configured_bars",
    "load_persisted_workflow_plan",
    "persist_backtest_result",
    "persist_workflow_plan",
    "plan_recent_taifex_csv_files",
    "prepare_backtest",
    "raw_directory_fingerprint",
    "read_parquet",
    "run_backtest_from_config",
    "run_environment_health_check",
    "select_contract_bars",
    "sync_recent_taifex_csv_files",
]
