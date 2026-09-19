"""Vercel entrypoint. See pyproject.toml [tool.vercel]."""

import sys
from pathlib import Path

# the backend imports its own modules as `app.*` from inside backend/, the same
# way pytest.ini and uvicorn run it. Vercel imports from the repo root instead,
# so put backend/ on the path before touching the app.
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.main import app  # noqa: E402
