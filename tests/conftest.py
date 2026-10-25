"""Test isolation.

This must run before anything imports `app.db.database`, which builds its engine from settings at
import time. pytest loads `conftest.py` before collecting test modules, so setting the environment
here is what makes the suite use a throwaway database instead of the developer's own.

Without this the suite ran against `data/instagram_knowledge.db` and accumulated rows across every
run, which quietly makes any aggregate assertion depend on how many times pytest had been invoked.
"""

import os
import shutil
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent.parent / "data" / "test-run"
if TEST_ROOT.exists():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
TEST_ROOT.mkdir(parents=True, exist_ok=True)

# Ignore the developer's .env: these tests assert default provider behaviour, and a local model
# configuration would otherwise change the results (and try to load real weights).
os.environ["ENV_FILE"] = str(TEST_ROOT / "no-such.env")

os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}"
os.environ["MEDIA_ROOT"] = str(TEST_ROOT / "media")
os.environ["OBJECT_STORE_ROOT"] = str(TEST_ROOT / "objects")
# Keep the suite quiet; individual tests assert on the formatter directly.
os.environ.setdefault("LOG_LEVEL", "WARNING")
