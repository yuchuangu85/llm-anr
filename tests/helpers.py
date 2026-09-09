from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / 'fixtures'
REPO_ROOT = FIXTURE_DIR.parent.parent
_CLI_ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding='utf-8'))


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the installed-module CLI from the repository root."""
    return subprocess.run(
        [sys.executable, '-m', 'anr_evidence', *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
        env=_CLI_ENV,
    )


def write_json_fixture(directory: str | Path, name: str, payload: object) -> Path:
    """Write a UTF-8 JSON CLI input under a caller-owned temporary directory."""
    path = Path(directory) / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return path
