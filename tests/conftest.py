"""Shared pytest fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_requests.json"


@pytest.fixture
def tmp_trace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRIP_PLANNER_TRACE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not accidentally pick up a real API key from the developer's shell.

    Streamlit's import-time load_dotenv() pulls .env keys into os.environ for the
    rest of the session, so we have to clear ALL provider keys (LLM + tools)
    when we're not in live-test mode.
    """
    if "RUN_LIVE" not in os.environ:
        for var in (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "TAVILY_API_KEY",
            "SERPER_API_KEY",
            "BRAVE_API_KEY",
            "GOOGLE_MAPS_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def sample_requests() -> dict:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip @pytest.mark.live tests unless RUN_LIVE=1 is set."""
    if os.getenv("RUN_LIVE"):
        from dotenv import load_dotenv

        load_dotenv()
        return
    skip_live = pytest.mark.skip(reason="set RUN_LIVE=1 to run live LLM tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
