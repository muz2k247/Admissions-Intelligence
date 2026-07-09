"""Backend settings. The extracted-records directory is configurable via
env var so tests/deploys can point at fixture data instead of the real
.tmp/extracted output (which is gitignored and only exists after a scraper
+ extraction run)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

EXTRACTED_DIR = Path(os.environ.get("ADMISSIONS_EXTRACTED_DIR", REPO_ROOT / ".tmp" / "extracted"))
