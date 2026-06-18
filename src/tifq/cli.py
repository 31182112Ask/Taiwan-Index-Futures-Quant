"""Command line interface for the V1 Backtest Lab."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from tifq import __version__
from tifq.backtest import run_backtest_from_config
from tifq.bars import build_bar_files
from tifq.config import ConfigLoadError, load_backtest_config
from tifq.data import import_taifex_ticks

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
        "Bootstrap complete. Next milestone: Task 9 - Metrics and Reports.",
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
) -> None:
    """Import raw TAIFEX CSV/ZIP files into cleaned tick Parquet files."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    try:
        summary = import_taifex_ticks(raw_dir, processed_dir, symbol=symbol)
    except (OSError, ValueError) as exc:
        typer.secho(f"TAIFEX import failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("TAIFEX import completed.", fg=typer.colors.GREEN)
    typer.echo(f"Raw files discovered: {summary.files_discovered}")
    typer.echo(f"CSV files read: {summary.csv_files_read}")
    typer.echo(f"Input rows: {summary.input_row_count}")
    typer.echo(f"Clean TMF ticks: {summary.output_tick_count}")
    typer.echo(f"Invalid or filtered rows: {summary.invalid_row_count}")
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
) -> None:
    """Build OHLCV bars from cleaned tick Parquet files."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    if timeframe not in {"1m", "5m"}:
        raise typer.BadParameter("V1 supports only 1m and 5m timeframes.")
    try:
        summary = build_bar_files(processed_dir, symbol=symbol, timeframe=timeframe)
    except (OSError, ValueError) as exc:
        typer.secho(f"Bar build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("Bar build completed.", fg=typer.colors.GREEN)
    typer.echo(f"Tick files read: {summary.tick_files_read}")
    typer.echo(f"Input ticks: {summary.input_tick_count}")
    typer.echo(f"Output bars: {summary.output_bar_count}")
    if summary.output_paths:
        typer.echo("Output files:")
        for output_path in summary.output_paths:
            typer.echo(f"  {output_path}")
    else:
        typer.echo("Output files: none")


@app.command("backtest")
def backtest(
    config: Annotated[
        Path,
        typer.Option(help="Path to YAML config."),
    ] = Path("configs/v1_backtest.yaml"),
) -> None:
    """Run a conservative next-bar-open backtest from a YAML config."""
    try:
        loaded_config = load_backtest_config(config)
    except (ConfigLoadError, ValidationError) as exc:
        typer.secho(f"Config validation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = run_backtest_from_config(loaded_config)
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
    typer.echo("Persisted reports are planned for Task 9 - Metrics and Reports.")


@app_group.command("backtest-lab")
def backtest_lab() -> None:
    """Start the Streamlit Backtest Lab. Placeholder until Task 10."""
    _not_implemented("Streamlit Backtest Lab", "Task 10 - Streamlit Backtest Lab")


if __name__ == "__main__":
    app()
