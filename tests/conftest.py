"""Test setup: point Studio at a throwaway workspace before anything imports it."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_WS = Path(tempfile.mkdtemp(prefix="mmh3-test-ws-"))
os.environ["MMH3_WORKSPACE"] = str(_WS)
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("CIVITAI_TOKEN", None)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def workspace() -> Path:
    from studio import paths

    paths.ensure_dirs()
    return _WS
