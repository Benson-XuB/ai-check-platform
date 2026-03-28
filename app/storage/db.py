import os
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


def get_database_url() -> Optional[str]:
    """
    Returns DATABASE_URL if configured.
    Example (Postgres): postgresql+psycopg://user:pass@host:5432/dbname
    """
    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def create_db_engine() -> Optional[Engine]:
    url = get_database_url()
    if not url:
        return None
    # Keep it simple (sync engine); if needed, can switch to async later.
    engine = create_engine(url, pool_pre_ping=True)

    # Register pgvector types if pgvector is installed.
    try:
        from pgvector.psycopg import register_vector

        @event.listens_for(engine, "connect")
        def _connect(dbapi_connection, connection_record):  # type: ignore
            try:
                register_vector(dbapi_connection)
            except Exception:
                pass
    except Exception:
        pass

    return engine

