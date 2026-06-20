"""Composition root for framework-neutral application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tifq.application.backtest_service import BacktestService
from tifq.application.data_pipeline_service import DataPipelineService
from tifq.application.environment_service import EnvironmentService
from tifq.application.result_service import ResultService
from tifq.application.workflow_service import WorkflowService


@dataclass(frozen=True)
class ApplicationFacade:
    environment: EnvironmentService
    data_pipeline: DataPipelineService
    workflow: WorkflowService
    backtest: BacktestService
    results: ResultService


def create_application(repository_root: Path) -> ApplicationFacade:
    environment = EnvironmentService(repository_root)
    data_pipeline = DataPipelineService()
    backtest = BacktestService()
    results = ResultService(repository_root)
    workflow = WorkflowService(environment, data_pipeline, backtest, results)
    return ApplicationFacade(environment, data_pipeline, workflow, backtest, results)
