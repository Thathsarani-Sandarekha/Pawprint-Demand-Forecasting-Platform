"""Opt-in PostgreSQL connection; never connect merely by importing the app."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_database_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        return None
    if not value.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL must use postgresql+psycopg:// with psycopg 3")
    return value


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    # SQLAlchemy hides credentials in reprs; do not log url or connection exceptions.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3})
