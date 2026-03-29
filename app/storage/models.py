from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


def _new_saas_webhook_token() -> str:
    return secrets.token_hex(24)


class AppUser(Base):
    """登录用户（当前仅 Gitee OAuth）。"""

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # 出现在 Webhook URL 中，用于将 Gitee 事件路由到对应用户
    saas_webhook_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=_new_saas_webhook_token, index=True
    )


class GiteeOAuthAccount(Base):
    """Gitee OAuth 令牌（明文存库；生产环境建议加密或专用密钥仓）。"""

    __tablename__ = "gitee_oauth_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_users.id"), nullable=False, index=True)
    gitee_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(256), nullable=False)
    access_token: Mapped[str] = mapped_column(String(512), nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class GiteeWatchedRepo(Base):
    """已为本用户注册 Webhook 的仓库（用于展示与去重）。"""

    __tablename__ = "gitee_watched_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_users.id"), nullable=False, index=True)
    path_with_namespace: Mapped[str] = mapped_column(String(512), nullable=False)
    hook_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("user_id", "path_with_namespace", name="uq_gitee_watched_user_path"),)


class PrReviewReport(Base):
    """SaaS：每次 Webhook 触发的审查结果（不写回 Gitee 评论）。"""

    __tablename__ = "pr_review_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_users.id"), nullable=False, index=True)
    path_with_namespace: Mapped[str] = mapped_column(String(512), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    pr_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # completed | failed
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("idx_pr_reports_user_created", "user_id", "created_at"),)


