"""RAG 检索层（方案 §3.2 · 真做，不 mock）。

防 AI 幻觉的锚点：教学/讲解时从用户知识库检索权威片段，要求 LLM 基于来源作答并
标注 citation；无来源的事实性陈述前端显式标注"请核对"。

检索策略：Hybrid = BM25（纯词法，零依赖，始终可用）+ Dense（向量余弦，配了
embedding key 才启用）+ 分数融合。未配 embedding → 自动退化为 BM25，仍是真实检索。

存储：SQLite（kb_sources / kb_chunks），按 (user_id, instance_id) 隔离。
CJK 友好分词：中文按字、拉丁按词/数字。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Optional

from core.db import get_conn
from core.embeddings import get_embedder

# ----------------------------- 分块 -----------------------------

def chunk_text(text: str, target: int = 500, overlap: int = 80) -> list[str]:
    """按段落聚合到约 target 字，相邻块带 overlap 字重叠（保留上下文）。"""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= target or not buf:
            buf = (buf + "\n" + p).strip() if buf else p
        else:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = (tail + "\n" + p).strip() if tail else p
    if buf:
        chunks.append(buf)
    # 单段超长 → 硬切
    out: list[str] = []
    for c in chunks:
        if len(c) <= target * 2:
            out.append(c)
        else:
            for i in range(0, len(c), target):
                out.append(c[i:i + target])
    return out


# ----------------------------- 分词（CJK 友好） -----------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z]+|[0-9]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


# ----------------------------- 入库 -----------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ingest_text(*, user_id: int, instance_id: str, title: str, text: str, kind: str = "text") -> dict:
    """切块 + （可选）向量化 + 落库。返回 {ok, source_id, n_chunks, embedded}。"""
    title = (title or "").strip() or "未命名来源"
    chunks = chunk_text(text)
    if not chunks:
        return {"ok": False, "error": "内容为空"}

    embedder = get_embedder()
    vectors: Optional[list[list[float]]] = None
    if embedder is not None:
        try:
            vectors = embedder.embed_texts(chunks)
        except Exception:  # noqa: BLE001 — 向量化失败不挡入库，退化为 BM25
            vectors = None

    ts = _now()
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO kb_sources (user_id, instance_id, title, kind, n_chunks, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, instance_id, title, kind, len(chunks), ts),
        )
        source_id = cur.lastrowid
        for i, ch in enumerate(chunks):
            emb = json.dumps(vectors[i]) if vectors and i < len(vectors) else None
            conn.execute(
                """INSERT INTO kb_chunks (source_id, user_id, instance_id, ord, text, embedding, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (source_id, user_id, instance_id, i, ch, emb, ts),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "source_id": source_id, "n_chunks": len(chunks), "embedded": vectors is not None}


def list_sources(user_id: int, instance_id: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, title, kind, n_chunks, created_at FROM kb_sources
               WHERE user_id=? AND instance_id=? ORDER BY created_at DESC""",
            (user_id, instance_id),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_source(user_id: int, instance_id: str, source_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM kb_sources WHERE id=? AND user_id=? AND instance_id=?",
            (source_id, user_id, instance_id),
        )
        conn.execute("DELETE FROM kb_chunks WHERE source_id=? AND user_id=?", (source_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def has_knowledge(user_id: int, instance_id: str) -> bool:
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM kb_chunks WHERE user_id=? AND instance_id=?",
            (user_id, instance_id),
        ).fetchone()
        return (r["n"] or 0) > 0
    finally:
        conn.close()


# ----------------------------- 检索 -----------------------------

def _load_chunks(user_id: int, instance_id: Optional[str] = None) -> list[dict]:
    conn = get_conn()
    try:
        if instance_id is None:
            rows = conn.execute(
                """SELECT k.id, k.source_id, k.instance_id, k.text, k.embedding,
                          s.title AS source_title
                     FROM kb_chunks k JOIN kb_sources s ON s.id = k.source_id
                    WHERE k.user_id=?""",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT k.id, k.source_id, k.instance_id, k.text, k.embedding,
                          s.title AS source_title
                     FROM kb_chunks k JOIN kb_sources s ON s.id = k.source_id
                    WHERE k.user_id=? AND k.instance_id=?""",
                (user_id, instance_id),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _bm25_scores(query: str, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs_tokens)
    if n == 0:
        return []
    dls = [len(d) for d in docs_tokens]
    avgdl = (sum(dls) / n) or 1.0
    df: dict[str, int] = {}
    for d in docs_tokens:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    q_tokens = set(_tokenize(query))
    scores = [0.0] * n
    for i, d in enumerate(docs_tokens):
        if not d:
            continue
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q_tokens:
            f = tf.get(t, 0)
            if f == 0:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dls[i] / avgdl))
        scores[i] = s
    return scores


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        # 全部相等：都有信号→都给 1.0；都为 0→保持 0.0（避免单条/同分被压成 0 误删）
        return [(1.0 if hi > 0 else 0.0) for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def _search_chunks(chunks: list[dict], query: str, k: int) -> dict:
    query = (query or "").strip()
    if not query or not chunks:
        return {"mode": "none", "hits": []}

    docs_tokens = [_tokenize(c["text"]) for c in chunks]
    raw_bm25 = _bm25_scores(query, docs_tokens)
    bm25 = _normalize(raw_bm25)

    raw_dense = [0.0] * len(chunks)
    dense = [0.0] * len(chunks)
    mode = "bm25"
    embedder = get_embedder()
    have_vecs = any(c.get("embedding") for c in chunks)
    if embedder is not None and have_vecs:
        try:
            qvec = embedder.embed_texts([query])[0]
            for idx, c in enumerate(chunks):
                try:
                    cv = json.loads(c["embedding"]) if c.get("embedding") else None
                except (json.JSONDecodeError, TypeError):
                    cv = None
                raw_dense[idx] = _cosine(qvec, cv) if cv else 0.0
            dense = _normalize(raw_dense)
            mode = "hybrid"
        except Exception:  # noqa: BLE001 — dense 失败退回 BM25
            raw_dense = [0.0] * len(chunks)
            dense = [0.0] * len(chunks)
            mode = "bm25"

    w_dense = 0.5 if mode == "hybrid" else 0.0
    w_bm25 = 1.0 - w_dense
    fused = [w_bm25 * bm25[i] + w_dense * dense[i] for i in range(len(chunks))]

    # 相关性判定看「原始」分（避免单条/同分被归一化压成 0 而被误删）；排序用融合分
    def _relevant(i: int) -> bool:
        return raw_bm25[i] > 0 or raw_dense[i] > 0.15

    order = sorted(range(len(chunks)), key=lambda i: fused[i], reverse=True)
    hits = []
    for i in order:
        if len(hits) >= k or not _relevant(i):
            continue
        hits.append({
            "text": chunks[i]["text"],
            "source_title": chunks[i]["source_title"],
            "source_id": chunks[i]["source_id"],
            "instance_id": chunks[i].get("instance_id"),
            "score": round(fused[i], 4),
        })
    return {"mode": mode, "hits": hits}


def search(user_id: int, instance_id: str, query: str, k: int = 4) -> dict:
    """在单条学习路径内混合检索 top-k。"""
    return _search_chunks(_load_chunks(user_id, instance_id), query, k)


def search_user_knowledge(user_id: int, query: str, k: int = 6) -> dict:
    """跨当前用户自己的学习路径检索；绝不跨 user_id。"""
    result = _search_chunks(_load_chunks(user_id), query, max(1, min(int(k or 6), 12)))
    result["scope"] = "user"
    return result


__all__ = [
    "chunk_text", "ingest_text", "list_sources", "delete_source",
    "has_knowledge", "search", "search_user_knowledge",
]
