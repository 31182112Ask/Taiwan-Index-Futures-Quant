"""Historical data pipeline application service."""

from __future__ import annotations

from tifq.application._progress import progress_callback
from tifq.application.dto import (
    BuildBarsRequest,
    DownloadPlanDTO,
    ImportRequest,
    PipelineResultDTO,
    SyncRequest,
)
from tifq.application.ports import ProgressSink
from tifq.bars import build_bar_files
from tifq.data import (
    import_taifex_ticks,
    plan_recent_taifex_csv_files,
    sync_recent_taifex_csv_files,
)


class DataPipelineService:
    def plan_sync(
        self, request: SyncRequest, progress_sink: ProgressSink | None = None
    ) -> DownloadPlanDTO:
        plan = plan_recent_taifex_csv_files(
            request.raw_dir,
            limit=request.limit,
            progress_callback=progress_callback(progress_sink),
        )
        return DownloadPlanDTO(
            tuple(
                {
                    "trading_date": item.remote.trading_date.isoformat(),
                    "download_url": item.remote.download_url,
                    "remote_filename": item.remote.remote_filename,
                    "local_path": str(item.local_path),
                    "status": item.status,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "recommended_action": item.recommended_action,
                }
                for item in plan.items
            ),
            plan.valid_existing_count,
            plan.missing_count,
            plan.conflict_count,
        )

    def sync(
        self, request: SyncRequest, progress_sink: ProgressSink | None = None
    ) -> PipelineResultDTO:
        summary = sync_recent_taifex_csv_files(
            request.raw_dir,
            limit=request.limit,
            overwrite=request.overwrite,
            progress_callback=progress_callback(progress_sink),
        )
        return PipelineResultDTO(
            "sync",
            summary.files_downloaded + summary.files_updated,
            summary.files_skipped,
            tuple(str(record.local_path) for record in summary.records),
            summary.files_downloaded == 0 and summary.files_updated == 0,
            details={"failed": summary.files_failed, "discovered": summary.files_discovered},
        )

    def import_ticks(
        self, request: ImportRequest, progress_sink: ProgressSink | None = None
    ) -> PipelineResultDTO:
        summary = import_taifex_ticks(
            request.raw_dir,
            request.processed_dir,
            symbol=request.symbol,
            force=request.force,
            progress_callback=progress_callback(progress_sink),
        )
        return PipelineResultDTO(
            "import",
            summary.files_changed,
            summary.files_skipped,
            tuple(str(path) for path in summary.output_paths),
            summary.no_op,
            details={
                "input_rows": summary.input_row_count,
                "output_rows": summary.output_tick_count,
                "invalid_rows": summary.invalid_row_count,
            },
        )

    def build_bars(
        self, request: BuildBarsRequest, progress_sink: ProgressSink | None = None
    ) -> PipelineResultDTO:
        summary = build_bar_files(
            request.processed_dir,
            symbol=request.symbol,
            timeframe=request.timeframe,
            force=request.force,
            progress_callback=progress_callback(progress_sink),
        )
        return PipelineResultDTO(
            "bars",
            summary.tick_files_rebuilt,
            summary.tick_files_skipped,
            tuple(str(path) for path in summary.output_paths),
            summary.no_op,
            details={
                "input_ticks": summary.input_tick_count,
                "output_bars": summary.output_bar_count,
            },
        )
