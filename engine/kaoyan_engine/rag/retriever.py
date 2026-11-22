"""课件检索：把切好的片段存进 SQLite，提问时按"语义向量 + 本地哈希向量"打分，取最相关的几段。

个人课件规模（几千到几万段）直接暴力计算余弦相似度就足够快，不需要单独的向量数据库。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..db import Database, new_id
from ..llm.gateway import Gateway, LLMError
from .vectors import cosine, from_blob, hash_embed, to_blob

CHUNK_SIZE = 600
CHUNK_OVERLAP = 100


@dataclass
class Hit:
    chunk_id: str
    material_id: str
    page: int
    text: str
    score: float
    filename: str = ""


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        window = text[start:end]
        if end < len(text):
            cut = max(window.rfind("\n"), window.rfind("。"), window.rfind(". "))
            if cut > size // 2:
                end = start + cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


class Retriever:
    def __init__(self, db: Database, gateway: Gateway) -> None:
        self.db = db
        self.gateway = gateway

    def index_pages(
        self, material_id: str, course_id: str | None, lesson_id: str | None, pages: list[tuple[int, str]]
    ) -> int:
        self.db.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        items: list[tuple[int, str]] = []
        for page, text in pages:
            items.extend((page, piece) for piece in split_text(text))
        if not items:
            return 0
        embedded: tuple[list[list[float]], str] | None = None
        try:
            embedded = self.gateway.embed([t for _, t in items])
        except LLMError:
            embedded = None
        for i, (page, text) in enumerate(items):
            emb = to_blob(embedded[0][i]) if embedded else None
            self.db.execute(
                "INSERT INTO chunks (id, material_id, course_id, lesson_id, page, text, hash_vec, emb, emb_model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    material_id,
                    course_id,
                    lesson_id,
                    page,
                    text,
                    to_blob(hash_embed(text)),
                    emb,
                    embedded[1] if embedded else None,
                ),
            )
        return len(items)

    def search(
        self,
        query: str,
        *,
        course_id: str | None = None,
        lesson_id: str | None = None,
        k: int = 6,
    ) -> list[Hit]:
        clauses, params = [], []
        if course_id:
            clauses.append("c.course_id = ?")
            params.append(course_id)
        sql = (
            "SELECT c.id, c.material_id, c.page, c.text, c.hash_vec, c.emb, c.emb_model, c.lesson_id, m.filename "
            "FROM chunks c LEFT JOIN materials m ON m.id = c.material_id"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.db.query(sql, params)
        if not rows:
            return []

        q_hash = hash_embed(query)
        q_emb: np.ndarray | None = None
        q_model = ""
        if any(r["emb"] for r in rows):
            try:
                res = self.gateway.embed([query])
            except LLMError:
                res = None
            if res:
                q_emb, q_model = np.asarray(res[0][0], dtype=np.float32), res[1]

        hits: list[Hit] = []
        for r in rows:
            lexical = cosine(q_hash, from_blob(r["hash_vec"]))
            emb = from_blob(r["emb"])
            if q_emb is not None and emb is not None and r["emb_model"] == q_model:
                score = 0.7 * cosine(q_emb, emb) + 0.3 * lexical
            else:
                score = lexical
            if lesson_id and r["lesson_id"] == lesson_id:
                score += 0.05
            hits.append(Hit(r["id"], r["material_id"], r["page"], r["text"], score, r["filename"] or ""))
        hits.sort(key=lambda h: h.score, reverse=True)
        return [h for h in hits[:k] if h.score > 0.05]
