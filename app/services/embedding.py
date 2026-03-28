"""向量检索：DashScope text-embedding-v3，用 diff 语义检索 Top-K 相关代码片段。"""

import math
from typing import Dict, List, Tuple

import httpx

DASHSCOPE_EMBEDDING_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-v3"
CHUNK_LINES = 40
CHUNK_OVERLAP_LINES = 8
MAX_CHUNKS_PER_FILE = 15
TOP_K = 5
BATCH_SIZE = 20


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embed_batch(api_key: str, texts: List[str]) -> List[List[float]]:
    """调用 DashScope embedding API，逐条请求以保证兼容性。"""
    if not texts:
        return []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    out: List[List[float]] = []
    with httpx.Client(timeout=60) as client:
        for t in texts:
            r = client.post(
                DASHSCOPE_EMBEDDING_URL,
                headers=headers,
                json={"model": EMBEDDING_MODEL, "input": t},
            )
            if r.status_code != 200:
                raise RuntimeError(f"Embedding API 错误: {r.status_code} {r.text[:200]}")
            d = r.json()
            items = d.get("data")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                emb = items[0].get("embedding")
            else:
                emb = d.get("embedding")
            out.append(emb if isinstance(emb, list) else [])
    return out


def _chunk_file(path: str, content: str) -> List[Tuple[int, int, str]]:
    """按行分块，返回 [(start_line, end_line, chunk_text), ...]。"""
    lines = content.split("\n")
    chunks: List[Tuple[int, int, str]] = []
    start = 0
    while start < len(lines) and len(chunks) < MAX_CHUNKS_PER_FILE:
        end = min(start + CHUNK_LINES, len(lines))
        chunk_text = "\n".join(lines[start:end])
        if chunk_text.strip():
            chunks.append((start + 1, end, chunk_text))
        start = end - CHUNK_OVERLAP_LINES if end < len(lines) else len(lines)
    return chunks


def enrich_file_contexts_with_semantic_search(
    diff: str,
    file_contexts: Dict[str, str],
    api_key: str,
    top_k: int = TOP_K,
) -> Dict[str, str]:
    """
    用 diff 作为 query，对 file_contexts 分块做向量检索，将 Top-K 片段并入 file_contexts。
    使用 DashScope text-embedding-v3，需传入 DashScope API Key。
    """
    if not file_contexts or not diff or not api_key:
        return dict(file_contexts)
    # 构建 (path, start_line, end_line, text) 列表
    all_chunks: List[Tuple[str, int, int, str]] = []
    for path, content in file_contexts.items():
        if not content or len(content) > 100_000:
            continue
        for start_line, end_line, text in _chunk_file(path, content):
            all_chunks.append((path, start_line, end_line, text))
    if not all_chunks:
        return dict(file_contexts)
    # query: diff 摘要（前 2000 字符）
    query_text = diff[:2000].strip() or "(无 diff)"
    try:
        query_embs = _embed_batch(api_key, [query_text])
        query_emb = query_embs[0] if query_embs else []
        if not query_emb:
            return dict(file_contexts)
        chunk_texts = [t for (_, _, _, t) in all_chunks]
        chunk_embs = _embed_batch(api_key, chunk_texts)
        scored = []
        for i, (path, sl, el, text) in enumerate(all_chunks):
            emb = chunk_embs[i] if i < len(chunk_embs) else []
            sim = _cosine_similarity(query_emb, emb)
            scored.append((sim, path, sl, el, text))
        scored.sort(key=lambda x: -x[0])
        merged = dict(file_contexts)
        for idx, (_, path, sl, el, text) in enumerate(scored[:top_k]):
            key = f"[语义检索-{idx + 1}] {path} (行 {sl}-{el})"
            merged[key] = text
        return merged
    except Exception:
        return dict(file_contexts)
