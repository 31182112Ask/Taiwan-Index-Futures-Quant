"""Executable architecture rules for the V2 application boundary."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "tifq"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_application_layer_is_framework_neutral() -> None:
    for path in (SRC / "application").rglob("*.py"):
        imports = imported_modules(path)
        assert not any(name == "streamlit" or name.startswith("streamlit.") for name in imports)
        assert not any(name == "plotly" or name.startswith("plotly.") for name in imports)
        assert not any(name == "fastapi" or name.startswith("fastapi.") for name in imports)


def test_core_does_not_depend_on_interfaces_or_http_frameworks() -> None:
    excluded = {"application", "interfaces", "apps"}
    for path in SRC.rglob("*.py"):
        if excluded.intersection(path.relative_to(SRC).parts) or path.name == "cli.py":
            continue
        imports = imported_modules(path)
        assert not any(name.startswith("tifq.interfaces") for name in imports)
        assert not any(name.startswith(("fastapi", "streamlit")) for name in imports)


def test_streamlit_reaches_business_code_only_through_application() -> None:
    imports = imported_modules(SRC / "interfaces" / "streamlit" / "app.py")
    tifq_imports = {name for name in imports if name.startswith("tifq.")}
    assert tifq_imports == {"tifq.application.ui_support"}


def test_cli_uses_application_services_for_business_operations() -> None:
    imports = imported_modules(SRC / "cli.py")
    forbidden = (
        "tifq.backtest",
        "tifq.bars",
        "tifq.data",
        "tifq.runtime.cleanup",
        "tifq.runtime.health",
    )
    assert not any(name.startswith(forbidden) for name in imports)


def test_legacy_streamlit_entry_point_is_thin() -> None:
    path = SRC / "apps" / "backtest_lab.py"
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 15
    assert imported_modules(path) == {"tifq.interfaces.streamlit.app"}
