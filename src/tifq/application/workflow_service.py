"""Eight-step application workflow facade."""

from __future__ import annotations

from pathlib import Path

from tifq.application.backtest_service import BacktestService
from tifq.application.data_pipeline_service import DataPipelineService
from tifq.application.dto import (
    BuildBarsRequest,
    ImportRequest,
    PreparedBacktest,
    SyncRequest,
    WorkflowExecutionDTO,
    WorkflowOptions,
    WorkflowStateDTO,
    WorkflowStepDTO,
)
from tifq.application.environment_service import EnvironmentService
from tifq.application.ports import ProgressSink
from tifq.application.result_service import ResultService
from tifq.backtest.engine import BacktestPreflight
from tifq.config.models import BacktestConfig
from tifq.runtime.health import HealthReport, run_environment_health_check
from tifq.workflow import WorkflowStepState, derive_workflow_state, load_persisted_workflow_plan


class WorkflowService:
    def __init__(
        self,
        environment: EnvironmentService,
        data_pipeline: DataPipelineService,
        backtest: BacktestService,
        results: ResultService,
    ) -> None:
        self.environment = environment
        self.data_pipeline = data_pipeline
        self.backtest = backtest
        self.results = results
        self._prepared: PreparedBacktest | None = None

    def get_state(self, config: BacktestConfig) -> WorkflowStateDTO:
        health = self._core_health()
        plan, fingerprint = load_persisted_workflow_plan(config)
        latest = self.results.find_latest_matching(config)
        state = derive_workflow_state(
            config,
            health,
            plan=plan,
            plan_raw_fingerprint=fingerprint,
            preflight=(
                self._prepared._core
                if self._prepared is not None
                and isinstance(self._prepared._core, BacktestPreflight)
                else None
            ),
            latest_run_dir=latest.run_dir if latest else None,
        )
        return WorkflowStateDTO(tuple(self._step(step) for step in state.steps))

    def execute_step(
        self,
        config: BacktestConfig,
        step_number: int,
        *,
        options: WorkflowOptions,
        progress_sink: ProgressSink | None = None,
    ) -> WorkflowExecutionDTO:
        result: object | None
        if step_number == 1:
            result = self.environment.check(progress_sink=progress_sink)
        elif step_number == 2:
            result = self.data_pipeline.plan_sync(
                SyncRequest(Path(config.data.raw_dir), options.sync_limit, options.overwrite),
                progress_sink,
            )
        elif step_number == 3:
            result = self.data_pipeline.sync(
                SyncRequest(Path(config.data.raw_dir), options.sync_limit, options.overwrite),
                progress_sink,
            )
        elif step_number == 4:
            result = self.data_pipeline.import_ticks(
                ImportRequest(
                    Path(config.data.raw_dir),
                    Path(config.data.processed_dir),
                    config.data.symbol,
                    options.force,
                ),
                progress_sink,
            )
        elif step_number == 5:
            result = self.data_pipeline.build_bars(
                BuildBarsRequest(
                    Path(config.data.processed_dir),
                    config.data.symbol,
                    config.data.timeframe,
                    options.force,
                ),
                progress_sink,
            )
        elif step_number == 6:
            self._prepared = self.backtest.preflight(config, progress_sink)
            result = self._prepared.summary
        elif step_number == 7:
            result = self.backtest.run(config, self._prepared, progress_sink)
        elif step_number == 8:
            result = self.results.find_latest_matching(config)
        else:
            raise ValueError(f"Unknown workflow step: {step_number}")
        state = self.get_state(config)
        return WorkflowExecutionDTO(state.steps[step_number - 1], state, result)

    def _core_health(self) -> HealthReport:
        return run_environment_health_check(self.environment.repository_root)

    @staticmethod
    def _step(step: WorkflowStepState) -> WorkflowStepDTO:
        return WorkflowStepDTO(
            step.number,
            f"step_{step.number}",
            step.name,
            step.status,
            step.marker,
            step.enabled,
            step.blocking_reason,
        )
