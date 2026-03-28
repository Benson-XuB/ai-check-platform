from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SymbolDefinition(Base):
    """
    A lightweight symbol definition record (incremental, best-effort).
    """

    __tablename__ = "symbol_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_key: Mapped[str] = mapped_column(String(256), nullable=False)  # owner/repo
    sha: Mapped[str] = mapped_column(String(80), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    symbol: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # function|class
    line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


Index("idx_symbol_def_repo_symbol", SymbolDefinition.repo_key, SymbolDefinition.symbol)
Index("idx_symbol_def_repo_path", SymbolDefinition.repo_key, SymbolDefinition.path)


class SymbolCallEdge(Base):
    """
    A best-effort call edge: from_path calls callee (name only).
    """

    __tablename__ = "symbol_call_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_key: Mapped[str] = mapped_column(String(256), nullable=False)
    sha: Mapped[str] = mapped_column(String(80), nullable=False)
    from_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    callee: Mapped[str] = mapped_column(String(256), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


Index("idx_symbol_call_repo_callee", SymbolCallEdge.repo_key, SymbolCallEdge.callee)
Index("idx_symbol_call_repo_from", SymbolCallEdge.repo_key, SymbolCallEdge.from_path)


