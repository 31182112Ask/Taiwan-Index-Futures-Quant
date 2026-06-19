from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

from tests.e2e.helpers import (
    REPOSITORY_ROOT,
    FixtureServer,
    StreamlitProcess,
    assert_browser_clean,
    assert_horizontal_layout,
    click_workflow_step,
    collect_button_boxes,
    create_test_project,
    reserve_port,
    snapshot_pipeline_mtimes,
    synthetic_taifex_csv,
    workflow_button,
)

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _attach_observers(
    page: Page,
    console_errors: list[str],
    page_errors: list[str],
    failed_requests: list[str],
) -> None:
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(f"{request.url}: {request.failure}"),
    )


def _open_app(context: BrowserContext, url: str, evidence: dict[str, Any]) -> Page:
    page = context.new_page()
    _attach_observers(
        page,
        evidence["console_errors"],
        evidence["page_errors"],
        evidence["failed_requests"],
    )
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("heading", name="Taiwan Index Futures Quant")).to_be_visible(
        timeout=30_000
    )
    expect(page.get_by_text("V1 Workflow", exact=True)).to_be_visible()
    return page


def _screenshot(
    page: Page,
    artifact_root: Path,
    evidence: dict[str, Any],
    filename: str,
) -> None:
    path = artifact_root / filename
    page.screenshot(path=path, full_page=True)
    evidence["screenshots"].append(path.relative_to(REPOSITORY_ROOT).as_posix())


def _record_step(
    page: Page,
    number: int,
    evidence: dict[str, Any],
    *,
    warning: bool = False,
) -> None:
    result = click_workflow_step(page, number, expect_warning=warning)
    evidence["steps_clicked"].append(result["step"])
    markers = evidence["step_completion_markers"].setdefault(str(number), [])
    markers.append(result["marker"])
    if result["running_marker_observed"]:
        evidence["running_markers_observed"].append(number)


def _assert_trading_result_ui(page: Page, project_root: Path) -> None:
    expect(page.get_by_text("Latest persisted result", exact=True)).to_be_visible()
    for label in ("Final equity", "Net PnL", "Trades", "Fee", "Tax", "Slippage"):
        expect(page.get_by_text(label, exact=True).first).to_be_visible()
    for chart_title in (
        "Equity Curve",
        "Daily PnL",
        "K-line with VWAP, EMA, Entries, and Exits",
    ):
        expect(page.get_by_text(chart_title, exact=True).first).to_be_visible()
    page.get_by_role("tab", name="Results").click()
    expect(page.get_by_role("heading", name="Result Browser")).to_be_visible()
    expect(page.locator("[data-testid='stDataFrame']:visible").first).to_be_visible()
    expect(page.locator("body")).to_contain_text("session_end")

    runs = sorted(
        (project_root / "data" / "results" / "backtests" / "vwap_trend").iterdir(),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    latest = runs[0]
    metrics = json.loads((latest / "metrics.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(latest / "trades.csv")
    assert metrics["trade_count"] > 0
    assert not trades.empty
    assert (
        pd.to_datetime(trades["entry_time"]).dt.date
        == pd.to_datetime(trades["exit_time"]).dt.date
    ).all()
    assert trades["exit_reason"].isin(["session_end", "session_end_fallback"]).any()
    assert (trades[["fee", "tax", "slippage"]].sum() > 0).all()


def test_v1_eight_step_browser_restart_and_noop(
    tmp_path: Path,
    browser_artifact_root: Path,
    browser_evidence: dict[str, Any],
) -> None:
    started = time.perf_counter()
    project_root = tmp_path / "trading"
    create_test_project(project_root)
    terminal = REPOSITORY_ROOT / "artifacts" / "validation" / "v1-final" / "browser-terminal.log"
    trace = REPOSITORY_ROOT / "artifacts" / "validation" / "v1-final" / "playwright-trace.zip"

    with FixtureServer(synthetic_taifex_csv()) as fixture, sync_playwright() as playwright:
        app = StreamlitProcess(project_root, fixture.recent_url, terminal, reserve_port())
        app.start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080}, device_scale_factor=1
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page = _open_app(context, app.url, browser_evidence)
            _screenshot(page, browser_artifact_root, browser_evidence, "01_startup.png")
            boxes = collect_button_boxes(page)
            assert_horizontal_layout(boxes)
            browser_evidence["button_bounding_boxes"] = boxes
            browser_evidence["horizontal_alignment"] = True
            workflow_row = page.locator("div[data-testid='stHorizontalBlock']").filter(
                has=workflow_button(page, 1)
            ).first
            workflow_row.screenshot(path=browser_artifact_root / "workflow_buttons.png")
            browser_evidence["screenshots"].append(
                "artifacts/ui/v1-final/workflow_buttons.png"
            )

            screenshots = {
                1: "02_environment_complete.png",
                2: "03_plan_complete.png",
                3: "04_sync_complete.png",
                4: "05_import_complete.png",
                5: "06_bars_complete.png",
                6: "07_preflight_complete.png",
                7: "08_backtest_complete.png",
                8: "09_results_complete.png",
            }
            for number in range(1, 9):
                _record_step(page, number, browser_evidence)
                _screenshot(
                    page,
                    browser_artifact_root,
                    browser_evidence,
                    screenshots[number],
                )
            _assert_trading_result_ui(page, project_root)
            browser_evidence["trading_ui"] = True
            assert_browser_clean(
                browser_evidence["console_errors"],
                browser_evidence["page_errors"],
                browser_evidence["failed_requests"],
            )
            app.assert_clean_log()
        finally:
            context.tracing.stop(path=trace)
            context.close()
            browser.close()
            app.stop()

        app = StreamlitProcess(project_root, fixture.recent_url, terminal, reserve_port())
        app.start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080}, device_scale_factor=1
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page = _open_app(context, app.url, browser_evidence)
            for number in (1, 2, 3, 4, 5, 7, 8):
                expect(workflow_button(page, number)).to_contain_text("✅", timeout=30_000)
            expect(workflow_button(page, 6)).not_to_contain_text("✅")
            browser_evidence["restart_state_restoration"] = True
            _screenshot(
                page,
                browser_artifact_root,
                browser_evidence,
                "10_restart_state_restored.png",
            )

            before = snapshot_pipeline_mtimes(project_root)
            for number in range(2, 9):
                _record_step(page, number, browser_evidence)
                if number == 3:
                    expect(
                        page.get_by_text(
                            "Selected data already exists; no download required."
                        )
                    ).to_be_visible()
                elif number == 4:
                    expect(page.get_by_text("Raw data unchanged; import skipped.")).to_be_visible()
                elif number == 5:
                    expect(
                        page.get_by_text("Tick data unchanged; bar rebuild skipped.")
                    ).to_be_visible()
            after = snapshot_pipeline_mtimes(project_root)
            assert before == after
            browser_evidence["second_run_noop"] = True
            _assert_trading_result_ui(page, project_root)
            _screenshot(
                page,
                browser_artifact_root,
                browser_evidence,
                "11_second_run_noop.png",
            )
            assert_browser_clean(
                browser_evidence["console_errors"],
                browser_evidence["page_errors"],
                browser_evidence["failed_requests"],
            )
            app.assert_clean_log()
        finally:
            context.tracing.stop(path=trace)
            context.close()
            browser.close()
            app.stop()

    browser_evidence["duration_seconds"] += time.perf_counter() - started


def test_zero_trade_diagnostics_in_real_browser(
    tmp_path: Path,
    browser_artifact_root: Path,
    browser_evidence: dict[str, Any],
) -> None:
    started = time.perf_counter()
    project_root = tmp_path / "zero-trade"
    create_test_project(project_root, zero_trade=True)
    terminal = REPOSITORY_ROOT / "artifacts" / "validation" / "v1-final" / "browser-terminal.log"

    with FixtureServer(synthetic_taifex_csv(zero_trade=True)) as fixture:
        app = StreamlitProcess(project_root, fixture.recent_url, terminal, reserve_port())
        app.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}, device_scale_factor=1
                )
                page = _open_app(context, app.url, browser_evidence)
                try:
                    for number in range(1, 9):
                        _record_step(page, number, browser_evidence)
                    page.get_by_role("tab", name="Run Backtest").click()
                    expect(
                        page.get_by_text("No trades were generated.", exact=True)
                    ).to_be_visible()
                    body = page.locator("body")
                    for label in (
                        "primary_reason",
                        "bars_loaded",
                        "trading_days",
                        "atr_valid_ratio",
                        "atr_in_range_ratio",
                        "bars_inside_entry_window",
                        "long_candidates",
                        "short_candidates",
                        "buy_signals",
                        "sell_signals",
                    ):
                        expect(body).to_contain_text(label)
                    _screenshot(
                        page,
                        browser_artifact_root,
                        browser_evidence,
                        "12_zero_trade_diagnostics.png",
                    )
                    browser_evidence["zero_trade_ui"] = True
                finally:
                    context.close()
                    browser.close()
            app.assert_clean_log()
        finally:
            app.stop()
    browser_evidence["duration_seconds"] += time.perf_counter() - started


def test_partial_sync_warning_in_real_browser(
    tmp_path: Path,
    browser_artifact_root: Path,
    browser_evidence: dict[str, Any],
) -> None:
    started = time.perf_counter()
    project_root = tmp_path / "partial-sync"
    create_test_project(project_root)
    terminal = REPOSITORY_ROOT / "artifacts" / "validation" / "v1-final" / "browser-terminal.log"

    with FixtureServer(synthetic_taifex_csv(), fail_download=True) as fixture:
        app = StreamlitProcess(project_root, fixture.recent_url, terminal, reserve_port())
        app.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}, device_scale_factor=1
                )
                page = _open_app(context, app.url, browser_evidence)
                try:
                    _record_step(page, 1, browser_evidence)
                    _record_step(page, 2, browser_evidence)
                    _record_step(page, 3, browser_evidence, warning=True)
                    assert 3 in browser_evidence["running_markers_observed"]
                    expect(workflow_button(page, 4)).to_be_disabled()
                    expect(page.get_by_text("warning", exact=True)).to_be_visible()
                    expect(page.locator("body")).to_contain_text("Failed 1")
                    expect(page.locator("body")).to_contain_text("retryable status 500")
                    screenshot = browser_artifact_root / "partial_sync_warning.png"
                    page.screenshot(path=screenshot, full_page=True)
                    browser_evidence["screenshots"].append(
                        "artifacts/ui/v1-final/partial_sync_warning.png"
                    )
                    browser_evidence["partial_sync_ui"] = True
                finally:
                    context.close()
                    browser.close()
            app.assert_clean_log()
        finally:
            app.stop()
    browser_evidence["duration_seconds"] += time.perf_counter() - started
