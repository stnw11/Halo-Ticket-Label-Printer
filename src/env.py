"""Minimal .env file loader (no python-dotenv dependency, per §4.1's
requirements list). Docker Compose loads .env via env_file itself; this
lets bare local dev (`python -m src.main`, tools/ CLIs) see the same file.
"""

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: str | Path = DEFAULT_ENV_PATH) -> None:
    """Populate os.environ from a KEY=VALUE .env file. Real environment
    variables already set take precedence and are not overwritten."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
