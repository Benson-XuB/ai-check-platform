from __future__ import annotations

from sqlalchemy import text

from app.storage.db import create_db_engine
from app.storage.models import Base


def init_db() -> None:
    """
    Create tables if DATABASE_URL is configured.
    Safe to call multiple times.
    """
    engine = create_db_engine()
    if engine is None:
        return
    # Enable pgvector extension when possible (needed for RAG).
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception:
        pass

    Base.metadata.create_all(engine)

