"""
RAG: 非代码文档分块 -> DashScope text-embedding-v3 -> 写入 Postgres (pgvector)
同时提供向量检索用于后续把“软规则”注入 review prompt。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.services.embedding import DASHSCOPE_EMBEDDING_URL, EMBEDDING_MODEL
from app.storage.db import create_db_engine
from app.storage.rag_models import RagChunk


def _chunk_text(content: str, *, chunk_chars: int = 2000, chunk_overlap_chars: int = 200) -> List[str]:
    content = content or ""
    content = content.strip()
    if not content:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(content):
        end = min(start + chunk_chars, len(content))
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(content):
            break
        start = max(0, end - chunk_overlap_chars)
    return chunks


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _embed_texts(api_key: str, texts: List[str]) -> List[List[float]]:
    """
    Embedding API: text-embedding-v3.
    Returns: list[embedding]
    """
    if not texts:
        return []
    with httpx.Client(timeout=120) as client:
        embeddings: List[List[float]] = []
        for t in texts:
            r = client.post(
                DASHSCOPE_EMBEDDING_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": EMBEDDING_MODEL, "input": t},
            )
            if r.status_code != 200:
                raise RuntimeError(f"Embedding API 错误: {r.status_code} {r.text[:200]}")
            data = r.json()
            items = data.get("data") or []
            emb = items[0].get("embedding") if items and isinstance(items[0], dict) else data.get("embedding") or []
            embeddings.append(emb if isinstance(emb, list) else [])
        return embeddings


def _get_engine() -> Optional[Engine]:
    return create_db_engine()


def index_rag_documents(
    *,
    repo_key: str,
    source_type: str,
    documents: List[Any],
    embedding_api_key: str,
    chunk_chars: int = 2000,
    chunk_overlap_chars: int = 200,
) -> Dict[str, Any]:
    """
    Index non-code docs into pgvector.

    documents:
      - RagIndexDoc: {content, source_path, metadata}
    """
    engine = _get_engine()
    if engine is None:
        return {"indexed_chunks": 0, "skipped": True, "reason": "未配置 DATABASE_URL，跳过写入"}

    # Ensure tables exist
    # (init_db already creates, but safe)
    RagChunk.__table__.create(bind=engine, checkfirst=True)

    # Prepare chunks
    items: List[Dict[str, Any]] = []
    for doc in documents:
        content = getattr(doc, "content", None) if not isinstance(doc, dict) else doc.get("content")
        if not content:
            continue
        source_path = getattr(doc, "source_path", None) if not isinstance(doc, dict) else doc.get("source_path") or ""
        metadata = getattr(doc, "metadata", None) if not isinstance(doc, dict) else doc.get("metadata") or {}
        chunks = _chunk_text(content, chunk_chars=chunk_chars, chunk_overlap_chars=chunk_overlap_chars)
        for idx, chunk in enumerate(chunks):
            chunk_key = _sha256(f"{repo_key}|{source_type}|{source_path}|{idx}|{chunk}")
            items.append(
                {
                    "repo_key": repo_key,
                    "source_type": source_type,
                    "source_path": source_path,
                    "chunk_key": chunk_key,
                    "content": chunk,
                    "meta": metadata,
                }
            )

    if not items:
        return {"indexed_chunks": 0, "skipped": False}

    contents = [it["content"] for it in items]
    embeddings = _embed_texts(embedding_api_key, contents)
    if len(embeddings) != len(items):
        raise RuntimeError("embedding 数量与 chunks 数量不一致")

    values = []
    for it, emb in zip(items, embeddings):
        if not emb:
            continue
        values.append(
            {
                "repo_key": it["repo_key"],
                "chunk_key": it["chunk_key"],
                "source_type": it["source_type"],
                "source_path": it["source_path"],
                "content": it["content"],
                "meta": it["meta"] or {},
                "embedding": emb,
            }
        )
    if not values:
        return {"indexed_chunks": 0, "skipped": False}

    with Session(engine) as session:
        stmt = pg_insert(RagChunk).values(values)
        # We assume chunk_key uniqueness in (repo_key, chunk_key)
        stmt = stmt.on_conflict_do_update(
            index_elements=["repo_key", "chunk_key"],
            set_={"content": stmt.excluded.content, "meta": stmt.excluded.meta, "embedding": stmt.excluded.embedding},
        )
        session.execute(stmt)
        session.commit()

    return {"indexed_chunks": len(values), "skipped": False}


def search_rag(
    *,
    repo_key: str,
    query_text: str,
    embedding_api_key: str,
    source_type: Optional[str] = None,
    ref: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    engine = _get_engine()
    if engine is None:
        return []

    q_emb = _embed_texts(embedding_api_key, [query_text])[0]
    if not q_emb:
        return []

    with Session(engine) as session:
        # prefer cosine_distance when supported
        try:
            stmt = select(RagChunk).where(RagChunk.repo_key == repo_key)
            if source_type:
                stmt = stmt.where(RagChunk.source_type == source_type)
            if ref:
                # meta is JSONB mapped as RagChunk.meta, column name "metadata"
                stmt = stmt.where(RagChunk.meta["ref"].astext == ref)
            stmt = stmt.order_by(RagChunk.embedding.cosine_distance(q_emb)).limit(top_k)
            rows = session.scalars(stmt).all()
        except Exception:
            stmt = select(RagChunk).where(RagChunk.repo_key == repo_key)
            if source_type:
                stmt = stmt.where(RagChunk.source_type == source_type)
            if ref:
                stmt = stmt.where(RagChunk.meta["ref"].astext == ref)
            stmt = stmt.order_by(RagChunk.embedding.l2_distance(q_emb)).limit(top_k)
            rows = session.scalars(stmt).all()

    results: List[Dict[str, Any]] = []
    for r in rows:
        results.append(
            {
                "source_type": r.source_type,
                "source_path": r.source_path,
                "content": r.content[:6000],
                "metadata": r.meta,
            }
        )
    return results

