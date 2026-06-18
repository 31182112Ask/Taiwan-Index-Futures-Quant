"""Command line interface for the V1 Backtest Lab."""

from pathlib import Path

import typer

from tifq import __version__

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
    typer.secho(f"{feature} is planned for {task}; Task 1 only provides the bootstrap CLI.")
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

    typer.secho("Bootstrap complete. Next milestone: Task 2 - Config System.", fg=typer.colors.GREEN)


@app.command("import-taifex")
def import_taifex(
    raw_dir: Path = typer.Option(Path("data/raw/taifex"), help="Directory containing raw files."),
    symbol: str = typer.Option("TMF", help="Product symbol. V1 supports TMF only."),
) -> None:
    """Import raw TAIFEX data. Placeholder until Task 4."""
    _ = raw_dir
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    _not_implemented("TAIFEX import", "Task 4 - TAIFEX Importer")


@app.command("build-bars")
def build_bars(
    symbol: str = typer.Option("TMF", help="Product symbol. V1 supports TMF only."),
    timeframe: str = typer.Option("5m", help="Bar timeframe: 1m or 5m."),
) -> None:
    """Build OHLCV bars from cleaned ticks. Placeholder until Task 5."""
    if symbol != "TMF":
        raise typer.BadParameter("V1 supports TMF only.")
    if timeframe not in {"1m", "5m"}:
        raise typer.BadParameter("V1 supports only 1m and 5m timeframes.")
    _not_implemented("Bar building", "Task 5 - Bar Builder")


@app.command("backtest")
def backtest(
    config: Path = typer.Option(Path("configs/v1_backtest.yaml"), help="Path to YAML config."),
) -> None:
    """Run a backtest from config. Placeholder until Task 8."""
    _ = config
    _not_implemented("Backtesting", "Task 8 - Backtest Engine")


@app_group.command("backtest-lab")
def backtest_lab() -> None:
    """Start the Streamlit Backtest Lab. Placeholder until Task 10."""
    _not_implemented("Streamlit Backtest Lab", "Task 10 - Streamlit Backtest Lab")


if __name__ == "__main__":
    app()

