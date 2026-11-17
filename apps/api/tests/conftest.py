"""Safe test defaults; production settings remain fail-fast."""

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "not-used-by-unit-tests")
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")
