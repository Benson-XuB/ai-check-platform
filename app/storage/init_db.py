from __future__ import annotations

from sqlalchemy import text

from app.storage.db import create_db_engine
from app.storage.models import Base


def init_db() -> None:
    """
    Create tables if DATABASE_URL is configured.
    Safe to call multiple times.

    注意：已有 PostgreSQL 库不会自动加新列。若报 app_users.active_llm_credential_id
    不存在等错误，请在库上执行 scripts/sql/migrate_user_llm_credentials_postgres.sql。
    若报 user_llm_credentials.custom_completion_backend 不存在，请执行
    scripts/sql/migrate_user_llm_custom_completion_backend_postgres.sql。
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

