from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models import Base


class RagChunk(Base):
    """
    Non-code knowledge chunks stored with embeddings in Postgres (pgvector).
    """

    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_key: Mapped[str] = mapped_column(String(256), nullable=False)  # owner/repo or "global"
    chunk_key: Mapped[str] = mapped_column(String(128), nullable=False)  # stable hash for dedup
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)  # readme/wiki/policy/...
    source_path: Mapped[str] = mapped_column(String(1024), nullable=True)  # optional
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # NOTE: "metadata" is reserved attribute name in SQLAlchemy declarative API,
    # so we map it from column name "metadata" to Python attribute "meta".
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding = mapped_column(VECTOR())  # dimension is inferred by pgvector; dim pinning can be added later

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


Index("idx_rag_repo_chunk_key", RagChunk.repo_key, RagChunk.chunk_key, unique=True)
Index("idx_rag_repo_type", RagChunk.repo_key, RagChunk.source_type)

