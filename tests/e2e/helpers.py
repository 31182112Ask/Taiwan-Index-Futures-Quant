from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil
import yaml
from playwright.sync_api import Page, expect

REPOSITORY_ROOT = Path(__file__).parents[2]
APP_PATH = REPOSITORY_ROOT / "src" / "tifq" / "apps" / "backtest_lab.py"
WORKFLOW_LABELS = (
    "Check",
    "Plan",
    "Sync",
    "Import",
    "Bars",
    "Preflight",
    "Backtest",
    "Results",
)


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def synthetic_taifex_csv(*, zero_trade: bool = False) -> bytes:
    rows = ["symbol,contract,timestamp,price,volume"]
    pattern = (-30, -30, -30, -30, -30, 30, 30, 30, 30, 30)
    for day_offset in range(2):
        trading_day = date(2026, 6, 17) + timedelta(days=day_offset)
        timestamp = datetime.combine(trading_day, datetime.min.time()).replace(
            hour=8, minute=45
        )
        for index in range(59):
            movement = 0 if zero_trade else pattern[index % len(pattern)]
            price = 22_000 + day_offset * 15 + movement
            rows.append(f"TMF,202606,{timestamp:%Y-%m-%d %H:%M:%S},{price},10")
            timestamp += timedelta(minutes=5)
    return ("\n".join(rows) + "\n").encode("utf-8")


class FixtureServer:
    def __init__(self, csv_bytes: bytes, *, fail_download: bool = False) -> None:
        self.csv_bytes = csv_bytes
        self.fail_download = fail_download
        self.port = reserve_port()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/recent":
                    body = (
                        "<html><table><tr><td>2026/06/18</td>"
                        f'<td><a href="http://127.0.0.1:{owner.port}/Daily_20260618.csv">'
                        "Download CSV</a></td></tr></table></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/Daily_20260618.csv":
                    if owner.fail_download:
                        body = b"controlled fixture failure"
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain")
                    else:
                        body = owner.csv_bytes
                        self.send_response(200)
                        self.send_header("Content-Type", "text/csv")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def recent_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/recent"

    def __enter__(self) -> FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def create_test_project(root: Path, *, zero_trade: bool = False) -> Path:
    for directory in (
        root / "configs",
        root / "data" / "raw" / "taifex",
        root / "data" / "processed",
        root / "data" / "results" / "backtests",
        root / "logs",
        root / "hooks",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    min_atr = 10_000 if zero_trade else 0.1
    config = {
        "project": {"name": "TIFQ E2E", "timezone": "Asia/Taipei"},
        "data": {
            "symbol": "TMF",
            "contract_mode": "continuous_front_month",
            "contract": None,
            "roll_confirmation_days": 1,
            "raw_dir": "data/raw/taifex",
            "processed_dir": "data/processed",
            "start_date": "2026-06-17",
            "end_date": "2026-06-18",
            "session": "day",
            "timeframe": "5m",
        },
        "product": {"point_value": 10, "tick_size": 1, "exchange": "TAIFEX"},
        "cost": {
            "commission_per_side": 5,
            "tax_rate": 0.00002,
            "slippage_points_per_side": 1,
        },
        "strategy": {
            "name": "vwap_trend",
            "params": {
                "ema_fast": 3,
                "ema_slow": 8,
                "atr_period": 3,
                "atr_stop_mult": 1.5,
                "take_profit_r": 1.5,
                "min_atr_points": min_atr,
                "max_atr_points": 20_000,
                "max_trades_per_day": 10,
                "force_flatten_time": "13:35:00",
                "no_entry_before": "08:55:00",
                "no_entry_after": "13:20:00",
            },
        },
        "portfolio": {
            "initial_cash": 100_000,
            "max_position": 1,
            "allow_short": True,
            "assumed_margin_per_contract": None,
        },
    }
    config_path = root / "configs" / "v1_backtest.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (root / "hooks" / "sitecustomize.py").write_text(
        """import os
from urllib.parse import urljoin, urlparse
from tifq.data import taifex_fetcher

url = os.environ.get("TIFQ_E2E_RECENT_URL")
if url:
    taifex_fetcher.TAIFEX_RECENT_FUTURES_URL = url
    taifex_fetcher.TAIFEX_HOST = urlparse(url).hostname or "127.0.0.1"
    def validate_fixture_url(href, source_url):
        resolved = urljoin(source_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme == "http" and parsed.hostname == taifex_fetcher.TAIFEX_HOST:
            return resolved
        return None
    taifex_fetcher._validated_taifex_url = validate_fixture_url
""",
        encoding="utf-8",
    )
    return config_path


@dataclass
class StreamlitProcess:
    project_root: Path
    recent_url: str
    terminal_artifact: Path
    port: int
    process: subprocess.Popen[str] | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    _stdout_handle: Any = None
    _stderr_handle: Any = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if _port_is_open(self.port):
            raise RuntimeError(f"Streamlit test port {self.port} is already occupied")
        log_dir = self.project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.stdout_path = log_dir / "streamlit.stdout.log"
        self.stderr_path = log_dir / "streamlit.stderr.log"
        self._stdout_handle = self.stdout_path.open("w", encoding="utf-8")
        self._stderr_handle = self.stderr_path.open("w", encoding="utf-8")
        env = os.environ.copy()
        python_paths = [
            str(self.project_root / "hooks"),
            str(REPOSITORY_ROOT / "src"),
        ]
        if env.get("PYTHONPATH"):
            python_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        env["TIFQ_E2E_RECENT_URL"] = self.recent_url
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP_PATH),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(self.port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=self.project_root,
            env=env,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            text=True,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            ),
        )
        self._wait_until_ready()

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                owner = psutil.Process(process.pid)
                targets = owner.children(recursive=True) + [owner]
            except psutil.NoSuchProcess:
                targets = []
            for target in targets:
                try:
                    target.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(targets, timeout=8)
            for target in alive:
                try:
                    target.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(alive, timeout=5)
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None:
                handle.close()
        self._append_terminal_artifact()
        deadline = time.monotonic() + 10
        while _port_is_open(self.port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _port_is_open(self.port):
            raise RuntimeError(f"Streamlit test port {self.port} was not released")
        self.process = None

    def combined_log(self) -> str:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None and not handle.closed:
                handle.flush()
        parts = []
        for path in (self.stdout_path, self.stderr_path):
            if path is not None and path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def assert_clean_log(self) -> None:
        log = self.combined_log()
        forbidden = (
            "Traceback",
            "StreamlitDuplicateElementId",
            "StreamlitDuplicateElementKey",
            "500 Internal Server Error",
            "Arrow serialization error",
        )
        found = [token for token in forbidden if token in log]
        if found:
            raise AssertionError(f"Streamlit terminal log contains: {found}\n{log}")

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 60
        last_error = ""
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"Streamlit exited with {self.process.returncode}: {self.combined_log()}"
                )
            try:
                with urllib.request.urlopen(self.url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        raise RuntimeError(f"Streamlit did not become ready: {last_error}\n{self.combined_log()}")

    def _append_terminal_artifact(self) -> None:
        self.terminal_artifact.parent.mkdir(parents=True, exist_ok=True)
        with self.terminal_artifact.open("a", encoding="utf-8") as output:
            output.write(f"\n===== {self.project_root.name} port {self.port} =====\n")
            output.write(self.combined_log())


def workflow_button(page: Page, number: int):
    label = WORKFLOW_LABELS[number - 1]
    return page.get_by_role("button", name=re.compile(rf"^{number} {label}"))


def click_workflow_step(
    page: Page,
    number: int,
    *,
    expect_warning: bool = False,
    timeout: int = 120_000,
) -> dict[str, Any]:
    button = workflow_button(page, number)
    expect(button).to_be_visible(timeout=30_000)
    expect(button).to_be_enabled(timeout=30_000)
    button.click()
    running_observed = False
    try:
        expect(workflow_button(page, number)).to_contain_text("…", timeout=1_500)
        running_observed = True
    except AssertionError:
        pass
    marker = "⚠" if expect_warning else "✅"
    expect(workflow_button(page, number)).to_contain_text(marker, timeout=timeout)
    expect(page.get_by_text("Current step", exact=True)).to_be_visible()
    if number < 8 and not expect_warning:
        expect(workflow_button(page, number + 1)).to_be_enabled(timeout=30_000)
    return {
        "step": number,
        "label": WORKFLOW_LABELS[number - 1],
        "marker": marker,
        "running_marker_observed": running_observed,
    }


def collect_button_boxes(page: Page) -> list[dict[str, float]]:
    boxes: list[dict[str, float]] = []
    for number in range(1, 9):
        button = workflow_button(page, number)
        expect(button).to_be_visible()
        box = button.bounding_box()
        if box is None:
            raise AssertionError(f"Workflow button {number} has no bounding box")
        boxes.append({key: float(value) for key, value in box.items()})
    return boxes


def assert_horizontal_layout(boxes: list[dict[str, float]]) -> None:
    if len(boxes) != 8:
        raise AssertionError(f"Expected 8 workflow buttons, got {len(boxes)}")
    if max(box["y"] for box in boxes) - min(box["y"] for box in boxes) > 8:
        raise AssertionError(f"Workflow buttons are not horizontally aligned: {boxes}")
    for previous, current in zip(boxes, boxes[1:], strict=False):
        if current["x"] <= previous["x"]:
            raise AssertionError(f"Workflow button x positions are not increasing: {boxes}")
        if previous["x"] + previous["width"] > current["x"] + 1:
            raise AssertionError(f"Workflow buttons overlap: {boxes}")
    if any(
        box["x"] < 0
        or box["y"] < 0
        or box["x"] + box["width"] > 1920
        or box["y"] + box["height"] > 1080
        for box in boxes
    ):
        raise AssertionError(f"Workflow button is outside the viewport: {boxes}")


def assert_browser_clean(
    console_errors: list[str],
    page_errors: list[str],
    failed_requests: list[str],
) -> None:
    allowed_request_patterns = ("favicon",)
    unexpected_requests = [
        request
        for request in failed_requests
        if not any(pattern in request.lower() for pattern in allowed_request_patterns)
    ]
    if console_errors or page_errors or unexpected_requests:
        raise AssertionError(
            json.dumps(
                {
                    "console_errors": console_errors,
                    "page_errors": page_errors,
                    "failed_requests": unexpected_requests,
                },
                indent=2,
            )
        )


def snapshot_pipeline_mtimes(project_root: Path) -> dict[str, int]:
    roots = (project_root / "data" / "raw", project_root / "data" / "processed")
    return {
        str(path): path.stat().st_mtime_ns
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".zip", ".parquet"}
    }


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0
