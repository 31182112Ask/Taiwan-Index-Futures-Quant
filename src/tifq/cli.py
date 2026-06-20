"""Command line interface for the V1 Backtest Lab."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from pydantic import ValidationError

from tifq import __version__
from tifq.application import ApplicationFacade, create_application
from tifq.application.dto import (
    BuildBarsRequest,
    DownloadPlanDTO,
    ImportRequest,
    PipelineResultDTO,
    SyncRequest,
)
from tifq.application.ui_support import (
    ConfigLoadError,
    TaifexFetchError,
    load_backtest_config,
)
from tifq.runtime.locking import (
    OperationLockError,
    format_lock_conflict,
    remove_stale_operation_locks,
)
from tifq.runtime.progress import ProgressCallback, ProgressUpdate

app = typer.Typer(
    help="Taiwan Index Futures Quant - V1 Backtest Lab CLI.",
    no_args_is_help=True,
)
app_group = typer.Typer(help="Local client applications.", no_args_is_help=True)
app.add_typer(app_group, name="app")

DATA_DIRS = (
    Path("data/raw/taifex"),
    Path("data/processed/ticks"),
    Path("data/processed/bars"),
    Path("data/processed/.staging"),
    Path("data/results/backtests"),
    Path("data/quarantine"),
    Path("data/.runtime"),
    Path("logs"),
)

CONFIG_FILES = (
    Path("configs/v1_backtest.yaml"),
    Path("configs/strategies/vwap_trend.yaml"),
    Path("configs/strategies/opening_range.yaml"),
)


def _application() -> ApplicationFacade:
    return create_application(Path.cwd())


@app.command("doctor")
def doctor(
    full: Annotated[
        bool,
        typer.Option("--full", help="Include SHA-based duplicate raw file scanning."),
    ] = False,
) -> None:
    """Check local runtime structure, manifests, locks, and conflicts."""
    report = _application().environment.check(full_scan=full)
    color = typer.colors.GREEN if report.status == "healthy" else typer.colors.YELLOW
    if report.status == "error":
        color = typer.colors.RED
    typer.secho(f"Environment status: {report.status}", fg=color)
    typer.echo(f"Checked in: {report.duration_seconds:.3f}s")
    typer.echo(f"Healthy files/directories: {report.healthy_files}")
    typer.echo(f"Safe cleanup actions: {report.safe_cleanup_count}")
    typer.echo(f"Review-required actions: {report.confirmation_cleanup_count}")
    for issue in report.issues:
        path = f" [{issue['path']}]" if issue.get("path") is not None else ""
        typer.echo(f"  {str(issue['severity']).upper()} {issue['code']}{path}: {issue['message']}")
    if report.status == "error":
        raise typer.Exit(code=1)


@app.command("clean")
def clean(
    apply_safe: Annotated[
        bool,
        typer.Option("--apply-safe", help="Delete only allowlisted stale temp files."),
    ] = False,
    full_scan: Annotated[
        bool,
        typer.Option("--full-scan", help="Hash raw files to find duplicate content."),
    ] = False,
    quarantine_duplicates: Annotated[
        bool,
        typer.Option(help="Move confirmed duplicate raw files into quarantine."),
    ] = False,
    prune_results: Annotated[
        bool,
        typer.Option(help="Move old result runs into quarantine; never permanently delete."),
    ] = False,
    keep_latest: Annotated[
        int,
        typer.Option(min=0, help="Result runs to keep per strategy when pruning."),
    ] = 20,
) -> None:
    """Plan conservative cleanup; dry-run unless an explicit apply flag is present."""
    service = _application().environment
    plan = service.build_cleanup_plan(
        full_scan=full_scan or quarantine_duplicates,
        prune_results=prune_results,
        keep_latest=keep_latest,
    )
    typer.echo("Cleanup plan (dry-run):")
    typer.echo(f"  actions: {len(plan.actions)}")
    typer.echo(f"  bytes considered: {plan.total_bytes}")
    typer.echo(f"  safe: {plan.safe_action_count}")
    typer.echo(f"  confirmation required: {plan.confirmation_action_count}")
    for action in plan.actions:
        typer.echo(f"  {action.action} {action.path} ({action.size_bytes} bytes): {action.reason}")

    reclaimed = 0
    failures: list[str] = []
    try:
        if apply_safe:
            summary = service.apply_safe_cleanup()
            reclaimed += summary.bytes_reclaimed
            failures.extend(summary.failed)
            typer.echo(f"Safe actions applied: {len(summary.applied)}")
        confirmed_action_ids = tuple(
            action.action_id
            for action in plan.actions
            if (quarantine_duplicates and "duplicate raw content" in action.reason)
            or (prune_results and "old result" in action.reason)
        )
        if confirmed_action_ids:
            summary = service.apply_confirmed_cleanup(confirmed_action_ids)
            failures.extend(summary.failed)
            typer.echo(f"Items quarantined: {len(summary.applied)}")
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Bytes reclaimed: {reclaimed}")
    if failures:
        for failure in failures:
            typer.secho(f"  FAILED {failure}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _write_gitkeep(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    gitkeep = directory / ".gitkeep"
    gitkeep.touch(exist_ok=True)


def _not_implemented(feature: str, task: str) -> None:
    typer.secho(f"{feature} is planned for {task}; it is not implemented in the current task.")
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show package version and exit.",
    ),
) -> None:
    """Run the TIFQ command line interface."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command("init")
def init_project() -> None:
    """Create local data directories and print bootstrap status."""
    for directory in DATA_DIRS:
        _write_gitkeep(directory)

    typer.secho("Project directories", fg=typer.colors.CYAN)
    for directory in DATA_DIRS:
        typer.echo(f"  OK  {directory}")

    typer.secho("Configuration files", fg=typer.colors.CYAN)
    for config_file in CONFIG_FILES:
        status = "OK " if config_file.exists() else "MISS"
        typer.echo(f"  {status} {config_file}")

    typer.secho(
        "Bootstrap complete. V1 Backtest Lab modules are implemented through Task 10.",
        fg=typer.colors.GREEN,
    )


@app.command("import-taifex")
def import_taifex(
    raw_dir: Annotated[
        Path,
        typer.Option(help="Directory containing raw files."),
    ] = Path("data/raw/taifex"),
    processed_dir: Annotated[
        Path,
        typer.Option(help="Directory for processed output files."),
    ] = Path("data/processed"),
    symbol: Annotated[
        str,
        typer.Option(help="Product symbol. V1 supports TMF only."),
    ] = "TMF",
    quiet: Annotated[bool, typer.Option(help="Only print errors and final summary.")] = False,
) -> None:
    """Import raw TAIFEX CSV/ZIP files into cleaned tick Parquet files."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    try:
        summary = _application().data_pipeline.import_ticks(
            ImportRequest(raw_dir, processed_dir, symbol),
        )
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.secho(f"TAIFEX import failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("TAIFEX import completed.", fg=typer.colors.GREEN)
    typer.echo(f"Raw files discovered: {summary.details.get('files_discovered', 0)}")
    typer.echo(f"CSV files read: {summary.details.get('csv_files_read', 0)}")
    typer.echo(f"Input rows: {summary.details['input_rows']}")
    typer.echo(f"Clean TMF ticks: {summary.details['output_rows']}")
    typer.echo(f"Invalid or filtered rows: {summary.details['invalid_rows']}")
    typer.echo(f"Unchanged raw files: {summary.skipped}")
    typer.echo(f"Changed raw files: {summary.changed}")
    typer.echo(f"No changes: {summary.no_op}")
    if summary.output_paths:
        typer.echo("Output files:")
        for output_path in summary.output_paths:
            typer.echo(f"  {output_path}")
    else:
        typer.echo("Output files: none")


@app.command("build-bars")
def build_bars(
    symbol: Annotated[
        str,
        typer.Option(help="Product symbol. V1 supports TMF only."),
    ] = "TMF",
    timeframe: Annotated[
        str,
        typer.Option(help="Bar timeframe: 1m or 5m."),
    ] = "5m",
    processed_dir: Annotated[
        Path,
        typer.Option(help="Directory containing processed tick data and bar outputs."),
    ] = Path("data/processed"),
    force: Annotated[bool, typer.Option(help="Rebuild every selected tick file.")] = False,
    quiet: Annotated[bool, typer.Option(help="Only print errors and final summary.")] = False,
) -> None:
    """Build OHLCV bars from cleaned tick Parquet files."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    if timeframe not in {"1m", "5m"}:
        raise typer.BadParameter("V1 supports only 1m and 5m timeframes.")
    try:
        summary = _application().data_pipeline.build_bars(
            BuildBarsRequest(
                processed_dir, symbol, cast(Literal["1m", "5m"], timeframe), force
            ),
        )
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.secho(f"Bar build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("Bar build completed.", fg=typer.colors.GREEN)
    typer.echo(f"Tick files read: {summary.changed}")
    typer.echo(f"Input ticks: {summary.details['input_ticks']}")
    typer.echo(f"Output bars: {summary.details['output_bars']}")
    typer.echo(f"Unchanged tick files: {summary.skipped}")
    typer.echo(f"Rebuilt tick files: {summary.changed}")
    typer.echo(f"No changes: {summary.no_op}")
    if summary.output_paths:
        typer.echo("Output files:")
        for output_path in summary.output_paths:
            typer.echo(f"  {output_path}")
    else:
        typer.echo("Output files: none")


@app.command("sync-taifex")
def sync_taifex(
    raw_dir: Annotated[
        Path,
        typer.Option(help="Directory for official raw TAIFEX downloads."),
    ] = Path("data/raw/taifex"),
    processed_dir: Annotated[
        Path,
        typer.Option(help="Directory for processed tick data and bar outputs."),
    ] = Path("data/processed"),
    symbol: Annotated[
        str,
        typer.Option(help="Product symbol. V1 supports TMF only."),
    ] = "TMF",
    timeframe: Annotated[
        str,
        typer.Option(help="Bar timeframe: 1m or 5m."),
    ] = "5m",
    limit: Annotated[
        int,
        typer.Option(help="Most recent official trading days to sync, from 1 to 30."),
    ] = 30,
    overwrite: Annotated[
        bool,
        typer.Option(help="Download files again even when a valid manifest entry exists."),
    ] = False,
    download_only: Annotated[
        bool,
        typer.Option(help="Stop after downloading official files."),
    ] = False,
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Print the download plan without writing files."),
    ] = False,
    missing_only: Annotated[
        bool,
        typer.Option(help="Download missing files and skip valid existing files."),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm destructive overwrite in non-interactive use."),
    ] = False,
    quiet: Annotated[bool, typer.Option(help="Only print errors and final summary.")] = False,
) -> None:
    """Download official recent TAIFEX files, then optionally import and build bars."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    if timeframe not in {"1m", "5m"}:
        raise typer.BadParameter("V1 supports only 1m and 5m timeframes.")
    if not 1 <= limit <= 30:
        raise typer.BadParameter("limit must be between 1 and 30.")
    if overwrite and not yes:
        raise typer.BadParameter("--overwrite requires explicit --yes confirmation.")
    if not missing_only and not overwrite:
        raise typer.BadParameter("--no-missing-only requires --overwrite --yes.")

    if plan:
        try:
            download_plan = _application().data_pipeline.plan_sync(
                SyncRequest(raw_dir, limit, overwrite)
            )
        except (OSError, ValueError, TaifexFetchError) as exc:
            typer.secho(f"TAIFEX plan failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        _print_download_plan(download_plan)
        if download_plan.conflict_count:
            raise typer.Exit(code=1)
        return

    try:
        fetch_summary = _application().data_pipeline.sync(SyncRequest(raw_dir, limit, overwrite))
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError, TaifexFetchError) as exc:
        typer.secho(f"TAIFEX sync failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    _print_taifex_fetch_summary(fetch_summary)
    if _as_int(fetch_summary.details.get("failed", 0)):
        raise typer.Exit(code=1)

    import_summary = None
    bar_summary = None
    try:
        if not download_only:
            import_summary = _application().data_pipeline.import_ticks(
                ImportRequest(raw_dir, processed_dir, symbol),
            )
            bar_summary = _application().data_pipeline.build_bars(
                BuildBarsRequest(
                    processed_dir, symbol, cast(Literal["1m", "5m"], timeframe)
                ),
            )
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.secho(f"TAIFEX import or bar build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if import_summary is not None:
        typer.echo(f"Clean TMF ticks: {import_summary.details['output_rows']}")
        typer.echo(f"Invalid or filtered rows: {import_summary.details['invalid_rows']}")
    if bar_summary is not None:
        typer.echo(f"Built bars: {bar_summary.details['output_bars']}")
        import_no_op = bool(getattr(import_summary, "no_op", False))
        bar_no_op = bool(getattr(bar_summary, "no_op", False))
        typer.echo(f"Pipeline no changes: {import_no_op and bar_no_op}")


@app.command("backtest")
def backtest(
    config: Annotated[
        Path,
        typer.Option(help="Path to YAML config."),
    ] = Path("configs/v1_backtest.yaml"),
    quiet: Annotated[bool, typer.Option(help="Only print errors and final summary.")] = False,
) -> None:
    """Run a conservative next-bar-open backtest from a YAML config."""
    try:
        loaded_config = load_backtest_config(config)
    except (ConfigLoadError, ValidationError) as exc:
        typer.secho(f"Config validation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = _application().backtest.run(loaded_config)
    except OperationLockError as exc:
        typer.secho(format_lock_conflict(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.secho(f"Backtest failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho(f"Config validated successfully: {config}", fg=typer.colors.GREEN)
    typer.echo(
        "Validated scope: "
        f"{loaded_config.data.symbol} {loaded_config.data.session} session, "
        f"{loaded_config.data.timeframe} bars, "
        f"{loaded_config.data.start_date} to {loaded_config.data.end_date}."
    )
    typer.secho(
        "Backtest completed with conservative next-bar-open execution.",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"Trades: {result.metrics['trade_count']}")
    typer.echo(f"Final equity: {result.metrics['final_equity']:.2f}")
    typer.echo(f"Net PnL: {result.metrics['net_pnl']:.2f}")
    typer.echo(f"Max drawdown: {result.metrics['max_drawdown']:.2f}")
    typer.echo(f"Win rate: {result.metrics['win_rate']:.2%}")
    typer.echo(f"Result directory: {result.run_dir}")
    for name in (
        "config.yaml",
        "trades.csv",
        "equity_curve.csv",
        "metrics.json",
        "model_bars.parquet",
        "signals.csv",
        "contract_selection.csv",
        "diagnostics.json",
        "timings.json",
        "data_fingerprint.json",
    ):
        typer.echo(f"  {Path(result.run_dir) / name}")


@app.command("workflow")
def workflow(
    config: Annotated[
        Path,
        typer.Option(help="Path to YAML config."),
    ] = Path("configs/v1_backtest.yaml"),
    stop_after: Annotated[
        str | None,
        typer.Option(
            "--stop-after",
            help="Stop after doctor, plan, sync, import, bars, or preflight.",
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option(help="Only print failures and final summary.")] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable workflow summary."),
    ] = False,
) -> None:
    """Run the complete V1 doctor-to-persist pipeline in order."""
    allowed_stops = {"doctor", "plan", "sync", "import", "bars", "preflight"}
    if stop_after is not None and stop_after not in allowed_stops:
        raise typer.BadParameter(
            "--stop-after must be doctor, plan, sync, import, bars, or preflight"
        )
    try:
        loaded = load_backtest_config(config)
    except (ConfigLoadError, ValidationError) as exc:
        typer.secho(f"Workflow config failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    records: list[dict[str, object]] = []

    def record(step: str, status: str, message: str) -> None:
        records.append({"step": step, "status": status, "message": message})
        if not quiet and not json_output:
            marker = _workflow_cli_marker(status)
            typer.echo(f"[{marker}] {step}: {message}")

    try:
        application = _application()
        health = application.environment.check()
        if health.status == "error":
            raise ValueError("environment health check has blocking errors")
        record("doctor", "complete", health.status)
        if stop_after == "doctor":
            _finish_workflow(records, json_output)
            return

        plan = application.data_pipeline.plan_sync(SyncRequest(Path(loaded.data.raw_dir), 30))
        if plan.conflict_count:
            raise ValueError(f"download plan has {plan.conflict_count} local conflicts")
        record(
            "plan",
            "complete",
            f"{plan.valid_existing_count} existing, {plan.missing_count} downloads",
        )
        if stop_after == "plan":
            _finish_workflow(records, json_output)
            return

        sync = application.data_pipeline.sync(SyncRequest(Path(loaded.data.raw_dir), 30))
        if _as_int(sync.details.get("failed", 0)):
            raise ValueError(f"{sync.details['failed']} downloads failed")
        record(
            "sync",
            "complete",
            (
                "all selected data already exists"
                if sync.changed == 0
                else f"downloaded or updated {sync.changed}"
            ),
        )
        if stop_after == "sync":
            _finish_workflow(records, json_output)
            return

        imported = application.data_pipeline.import_ticks(
            ImportRequest(
                Path(loaded.data.raw_dir),
                Path(loaded.data.processed_dir),
                loaded.data.symbol,
            )
        )
        record(
            "import",
            "complete",
            "unchanged, skipped" if imported.no_op else f"changed {imported.changed}",
        )
        if stop_after == "import":
            _finish_workflow(records, json_output)
            return

        built = application.data_pipeline.build_bars(
            BuildBarsRequest(
                Path(loaded.data.processed_dir),
                loaded.data.symbol,
                loaded.data.timeframe,
            )
        )
        record(
            "bars",
            "complete",
            "unchanged, skipped" if built.no_op else f"rebuilt {built.changed}",
        )
        if stop_after == "bars":
            _finish_workflow(records, json_output)
            return

        prepared = application.backtest.preflight(loaded)
        record("preflight", "complete", f"{prepared.summary.bar_count} model bars")
        if stop_after == "preflight":
            _finish_workflow(records, json_output)
            return

        result = application.backtest.run(loaded, prepared)
        record("backtest", "complete", f"{result.metrics['trade_count']} trades")
        record("persist", "complete", result.run_dir)
    except OperationLockError as exc:
        record("workflow", "warning", format_lock_conflict(exc))
        _finish_workflow(records, json_output)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError, TaifexFetchError) as exc:
        record("workflow", "warning", str(exc))
        _finish_workflow(records, json_output)
        raise typer.Exit(code=1) from exc
    _finish_workflow(records, json_output)


def _finish_workflow(records: list[dict[str, object]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps({"steps": records}, ensure_ascii=False, indent=2))


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, str)) else 0


def _workflow_cli_marker(status: str) -> str:
    """Return terminal markers safe on Windows legacy code pages."""
    return {"complete": "OK", "warning": "WARN", "running": "..."}.get(status, "-")


def _print_taifex_fetch_summary(fetch_summary: PipelineResultDTO) -> None:
    typer.secho("Official TAIFEX sync completed.", fg=typer.colors.GREEN)
    typer.echo(f"Remote files discovered: {fetch_summary.details.get('discovered', 0)}")
    typer.echo(f"Files selected: {fetch_summary.changed + fetch_summary.skipped}")
    typer.echo(f"Downloaded or updated: {fetch_summary.changed}")
    typer.echo(f"Skipped: {fetch_summary.skipped}")
    typer.echo(f"Failed: {fetch_summary.details.get('failed', 0)}")


def _print_download_plan(download_plan: DownloadPlanDTO) -> None:
    typer.echo("TAIFEX download plan:")
    for item in download_plan.items:
        typer.echo(
            f"  {item['trading_date']} {item['status']} "
            f"{item['remote_filename']} -> {item['local_path']} "
            f"[{item['recommended_action']}]"
        )
    typer.echo(f"Valid existing: {download_plan.valid_existing_count}")
    typer.echo(f"Missing or changed: {download_plan.missing_count}")
    typer.echo(f"Conflicts: {download_plan.conflict_count}")


def _progress_callback(quiet: bool) -> ProgressCallback | None:
    if quiet:
        return None
    last_phase: list[str | None] = [None]

    def emit(update: ProgressUpdate) -> None:
        if update.phase == last_phase[0] and update.completed != update.total:
            return
        last_phase[0] = update.phase
        count = (
            f"{update.completed}/{update.total}"
            if update.total is not None
            else str(update.completed)
        )
        eta = f", ETA {update.eta_seconds:.1f}s" if update.eta_seconds is not None else ""
        typer.echo(
            f"[{update.operation}] {update.phase}: {count}, "
            f"elapsed {update.elapsed_seconds:.1f}s{eta} - {update.message}"
        )

    return emit


@app_group.command("backtest-lab")
def backtest_lab(
    host: Annotated[
        str,
        typer.Option(help="Streamlit server host."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(help="Streamlit server port."),
    ] = 8501,
) -> None:
    """Start the local Streamlit Backtest Lab client."""
    for directory in DATA_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
    remove_stale_operation_locks(Path("data/.runtime"))
    environment = _application().environment
    report = environment.check()
    cleanup_summary = environment.apply_safe_cleanup()
    typer.echo("Environment check:")
    typer.echo(f"  status: {report.status}")
    typer.echo(f"  healthy files: {report.healthy_files}")
    typer.echo(f"  stale temporary files removed: {len(cleanup_summary.applied)}")
    typer.echo(f"  duplicate candidates: {report.confirmation_cleanup_count}")
    typer.echo(f"  reclaimed: {cleanup_summary.bytes_reclaimed} bytes")
    app_path = Path(__file__).parent / "apps" / "backtest_lab.py"
    typer.secho(f"Starting Streamlit Backtest Lab at http://{host}:{port}", fg=typer.colors.GREEN)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
        "--server.port",
        str(port),
    ]
    raise typer.Exit(code=subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    app()
