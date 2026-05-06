"""Shared test fixtures for the md-publisher pytest suite.

The conftest puts the plugin root on sys.path and sets CLAUDE_PLUGIN_ROOT
so test modules can import `lib.*` and skill scripts the same way the
runtime does at publish time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
os.environ["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
sys.path.insert(0, str(PLUGIN_ROOT))


# Default corpus location (the bake-off worktree alongside the plugin
# worktree). Tests that need a real markdown source can override via the
# MD_PUBLISHER_TEST_CORPUS env var; integration tests skip if neither
# the env var nor the default path exists.
_DEFAULT_CORPUS = Path(
    "C:/Users/ehart/repos/md-publisher-bakeoff/corpus/transformers-explainer-themed.md"
)


@pytest.fixture(scope="session")
def plugin_root() -> Path:
    """Absolute path to the plugin checkout this test run targets."""
    return PLUGIN_ROOT


@pytest.fixture(scope="session")
def corpus() -> Path:
    """Path to the bake-off corpus markdown (used for integration tests).

    Overridable via the MD_PUBLISHER_TEST_CORPUS env var so the suite is
    portable to a CI/contributor machine without the bake-off checkout.
    Tests that require a real file should `pytest.skip(...)` if the
    returned path doesn't exist rather than fail.
    """
    override = os.environ.get("MD_PUBLISHER_TEST_CORPUS")
    return Path(override) if override else _DEFAULT_CORPUS


@pytest.fixture
def tmp_build(tmp_path) -> Path:
    """Per-test temp build directory under pytest's tmp_path."""
    d = tmp_path / "build"
    d.mkdir()
    return d


@pytest.fixture(params=[
    ("atlas",    "light"), ("atlas",    "dark"),
    ("phosphor", "light"), ("phosphor", "dark"),
    ("arcade",   "light"), ("arcade",   "dark"),
])
def all_themes(request):
    """Parametrize a test across all 6 bundled (theme, mode) variants."""
    return request.param
