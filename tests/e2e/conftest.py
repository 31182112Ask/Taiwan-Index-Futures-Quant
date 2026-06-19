from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.helpers import REPOSITORY_ROOT


@pytest.fixture(scope="session")
def browser_artifact_root() -> Path:
    root = REPOSITORY_ROOT / "artifacts" / "ui" / "v1-final"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def browser_evidence(browser_artifact_root: Path) -> Iterator[dict[str, Any]]:
    del browser_artifact_root
    report_path = REPOSITORY_ROOT / "artifacts" / "validation" / "v1-final"
    report_path.mkdir(parents=True, exist_ok=True)
    terminal = report_path / "browser-terminal.log"
    terminal.write_text("", encoding="utf-8")
    evidence: dict[str, Any] = {
        "browser": "chromium",
        "headless": True,
        "viewport": {"width": 1920, "height": 1080, "device_scale_factor": 1},
        "steps_clicked": [],
        "step_completion_markers": {},
        "running_markers_observed": [],
        "button_bounding_boxes": [],
        "horizontal_alignment": False,
        "screenshots": [],
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
        "trace": "artifacts/validation/v1-final/playwright-trace.zip",
        "terminal_log": "artifacts/validation/v1-final/browser-terminal.log",
        "streamlit_traceback_count": 0,
        "restart_state_restoration": False,
        "second_run_noop": False,
        "zero_trade_ui": False,
        "trading_ui": False,
        "partial_sync_ui": False,
        "duration_seconds": 0.0,
    }
    yield evidence
    evidence["streamlit_traceback_count"] = terminal.read_text(
        encoding="utf-8", errors="replace"
    ).count("Traceback")
    (report_path / "browser-report.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
    )
