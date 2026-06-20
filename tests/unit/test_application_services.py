from __future__ import annotations

from pathlib import Path

from tifq.application import create_application
from tifq.application._progress import progress_callback
from tifq.application.dto import BuildBarsRequest, ImportRequest
from tifq.runtime.progress import ProgressUpdate


def test_application_factory_wires_framework_neutral_services(tmp_path: Path) -> None:
    application = create_application(tmp_path)

    assert application.environment.repository_root == tmp_path.resolve()
    assert application.workflow.environment is application.environment
    assert application.workflow.data_pipeline is application.data_pipeline
    assert application.workflow.backtest is application.backtest
    assert application.workflow.results is application.results


def test_empty_pipeline_operations_are_stable_no_ops(tmp_path: Path) -> None:
    application = create_application(tmp_path)
    (tmp_path / "raw").mkdir()
    (tmp_path / "processed").mkdir()
    imported = application.data_pipeline.import_ticks(
        ImportRequest(tmp_path / "raw", tmp_path / "processed")
    )
    bars = application.data_pipeline.build_bars(
        BuildBarsRequest(tmp_path / "processed", "TMF", "5m")
    )

    assert imported.no_op and imported.changed == 0
    assert bars.no_op and bars.changed == 0
    assert imported.output_paths == bars.output_paths == ()


def test_environment_service_returns_serializable_data(tmp_path: Path) -> None:
    report = create_application(tmp_path).environment.check()

    assert report.status in {"healthy", "warning", "error"}
    assert isinstance(report.issues, tuple)
    assert all(isinstance(issue, dict) for issue in report.issues)


def test_core_progress_is_adapted_to_framework_neutral_status() -> None:
    updates = []
    callback = progress_callback(updates.append)

    assert callback is not None
    callback(
        ProgressUpdate(
            operation="import",
            phase="read",
            completed=1,
            total=2,
            message="one file",
            elapsed_seconds=0.5,
            eta_seconds=0.5,
            throughput=2.0,
        )
    )

    assert updates[0].operation == "import"
    assert updates[0].progress == 0.5
    assert updates[0].state == "running"
