"""Command line interface for the V1 Backtest Lab."""

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from tifq import __version__
from tifq.backtest import persist_backtest_result, run_backtest_from_config
from tifq.bars import build_bar_files
from tifq.config import ConfigLoadError, load_backtest_config
from tifq.data import (
    TaifexDownloadPlan,
    TaifexFetchError,
    TaifexFetchSummary,
    import_taifex_ticks,
    plan_recent_taifex_csv_files,
    sync_recent_taifex_csv_files,
)
from tifq.runtime import (
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    build_cleanup_plan,
    run_environment_health_check,
)
from tifq.runtime.locking import remove_stale_operation_locks
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
    Path("data/results/backtests"),
    Path("logs"),
)

CONFIG_FILES = (
    Path("configs/v1_backtest.yaml"),
    Path("configs/strategies/vwap_trend.yaml"),
    Path("configs/strategies/opening_range.yaml"),
)


@app.command("doctor")
def doctor(
    full: Annotated[
        bool,
        typer.Option("--full", help="Include SHA-based duplicate raw file scanning."),
    ] = False,
) -> None:
    """Check local runtime structure, manifests, locks, and conflicts."""
    report = run_environment_health_check(Path.cwd(), full_scan=full)
    color = typer.colors.GREEN if report.status == "healthy" else typer.colors.YELLOW
    if report.status == "error":
        color = typer.colors.RED
    typer.secho(f"Environment status: {report.status}", fg=color)
    typer.echo(f"Checked in: {report.duration_seconds:.3f}s")
    typer.echo(f"Healthy files/directories: {report.healthy_files}")
    typer.echo(f"Safe cleanup actions: {report.cleanup_plan.safe_action_count}")
    typer.echo(
        f"Review-required actions: {report.cleanup_plan.confirmation_action_count}"
    )
    for issue in report.issues:
        path = f" [{issue.path}]" if issue.path is not None else ""
        typer.echo(f"  {issue.severity.upper()} {issue.code}{path}: {issue.message}")
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
    plan = build_cleanup_plan(
        Path.cwd(),
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
        typer.echo(
            f"  {action.action} {action.path} ({action.size_bytes} bytes): {action.reason}"
        )

    reclaimed = 0
    failures: list[str] = []
    if apply_safe:
        summary = apply_safe_cleanup(plan, Path.cwd())
        reclaimed += summary.bytes_reclaimed
        failures.extend(summary.failed)
        typer.echo(f"Safe actions applied: {len(summary.applied)}")
    confirmed_actions = tuple(
        action
        for action in plan.actions
        if (quarantine_duplicates and "duplicate raw content" in action.reason)
        or (prune_results and "old result" in action.reason)
    )
    if confirmed_actions:
        summary = apply_confirmed_cleanup(confirmed_actions, Path.cwd())
        failures.extend(summary.failed)
        typer.echo(f"Items quarantined: {len(summary.applied)}")
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
        summary = import_taifex_ticks(
            raw_dir,
            processed_dir,
            symbol=symbol,
            progress_callback=_progress_callback(quiet),
        )
    except (OSError, ValueError) as exc:
        typer.secho(f"TAIFEX import failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("TAIFEX import completed.", fg=typer.colors.GREEN)
    typer.echo(f"Raw files discovered: {summary.files_discovered}")
    typer.echo(f"CSV files read: {summary.csv_files_read}")
    typer.echo(f"Input rows: {summary.input_row_count}")
    typer.echo(f"Clean TMF ticks: {summary.output_tick_count}")
    typer.echo(f"Invalid or filtered rows: {summary.invalid_row_count}")
    typer.echo(f"Unchanged raw files: {summary.files_skipped}")
    typer.echo(f"Changed raw files: {summary.files_changed}")
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
        summary = build_bar_files(
            processed_dir,
            symbol=symbol,
            timeframe=timeframe,
            force=force,
            progress_callback=_progress_callback(quiet),
        )
    except (OSError, ValueError) as exc:
        typer.secho(f"Bar build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("Bar build completed.", fg=typer.colors.GREEN)
    typer.echo(f"Tick files read: {summary.tick_files_read}")
    typer.echo(f"Input ticks: {summary.input_tick_count}")
    typer.echo(f"Output bars: {summary.output_bar_count}")
    typer.echo(f"Unchanged tick files: {summary.tick_files_skipped}")
    typer.echo(f"Rebuilt tick files: {summary.tick_files_rebuilt}")
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
            download_plan = plan_recent_taifex_csv_files(
                raw_dir,
                limit=limit,
                progress_callback=_progress_callback(quiet),
            )
        except (OSError, ValueError, TaifexFetchError) as exc:
            typer.secho(f"TAIFEX plan failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        _print_download_plan(download_plan)
        if download_plan.conflict_count:
            raise typer.Exit(code=1)
        return

    try:
        fetch_summary = sync_recent_taifex_csv_files(
            raw_dir,
            limit=limit,
            overwrite=overwrite,
            progress_callback=_progress_callback(quiet),
        )
    except (OSError, ValueError, TaifexFetchError) as exc:
        typer.secho(f"TAIFEX sync failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    _print_taifex_fetch_summary(fetch_summary)
    if fetch_summary.files_failed:
        raise typer.Exit(code=1)

    import_summary = None
    bar_summary = None
    try:
        if not download_only:
            progress = _progress_callback(quiet)
            import_summary = import_taifex_ticks(
                raw_dir,
                processed_dir,
                symbol=symbol,
                progress_callback=progress,
            )
            bar_summary = build_bar_files(
                processed_dir,
                symbol=symbol,
                timeframe=timeframe,
                progress_callback=progress,
            )
    except (OSError, ValueError) as exc:
        typer.secho(f"TAIFEX import or bar build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if import_summary is not None:
        typer.echo(f"Clean TMF ticks: {import_summary.output_tick_count}")
        typer.echo(f"Invalid or filtered rows: {import_summary.invalid_row_count}")
    if bar_summary is not None:
        typer.echo(f"Built bars: {bar_summary.output_bar_count}")
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
        progress_callback = _progress_callback(quiet)
        result = run_backtest_from_config(
            loaded_config,
            progress_callback=progress_callback,
        )
        report_paths = persist_backtest_result(
            loaded_config,
            result,
            progress_callback=progress_callback,
        )
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
    typer.echo(f"Result directory: {report_paths.run_dir}")
    typer.echo(f"  {report_paths.config_path}")
    typer.echo(f"  {report_paths.trades_path}")
    typer.echo(f"  {report_paths.equity_curve_path}")
    typer.echo(f"  {report_paths.metrics_path}")
    typer.echo(f"  {report_paths.model_bars_path}")
    typer.echo(f"  {report_paths.signals_path}")
    typer.echo(f"  {report_paths.contract_selection_path}")
    typer.echo(f"  {report_paths.diagnostics_path}")
    typer.echo(f"  {report_paths.timings_path}")
    typer.echo(f"  {report_paths.data_fingerprint_path}")


def _print_taifex_fetch_summary(fetch_summary: TaifexFetchSummary) -> None:
    typer.secho("Official TAIFEX sync completed.", fg=typer.colors.GREEN)
    typer.echo(f"Remote files discovered: {fetch_summary.files_discovered}")
    typer.echo(f"Files selected: {fetch_summary.files_selected}")
    typer.echo(f"Downloaded: {fetch_summary.files_downloaded}")
    typer.echo(f"Skipped: {fetch_summary.files_skipped}")
    typer.echo(f"Updated: {fetch_summary.files_updated}")
    typer.echo(f"Failed: {fetch_summary.files_failed}")
    if fetch_summary.records:
        typer.echo("Download records:")
        for record in fetch_summary.records:
            typer.echo(f"  {record.trading_date} {record.status} {record.local_path}")
    if fetch_summary.failures:
        typer.echo("Download failures:")
        for failure in fetch_summary.failures:
            typer.echo(f"  {failure.trading_date} failed {failure.local_path}: {failure.error}")


def _print_download_plan(download_plan: TaifexDownloadPlan) -> None:
    typer.echo("TAIFEX download plan:")
    for item in download_plan.items:
        typer.echo(
            f"  {item.remote.trading_date} {item.status} "
            f"{item.remote.remote_filename} -> {item.local_path} "
            f"[{item.recommended_action}]"
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
    report = run_environment_health_check(Path.cwd())
    cleanup_summary = apply_safe_cleanup(report.cleanup_plan, Path.cwd())
    typer.echo("Environment check:")
    typer.echo(f"  status: {report.status}")
    typer.echo(f"  healthy files: {report.healthy_files}")
    typer.echo(f"  stale temporary files removed: {len(cleanup_summary.applied)}")
    typer.echo(f"  duplicate candidates: {report.cleanup_plan.confirmation_action_count}")
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
