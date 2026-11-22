"""本地哈希向量：不需要任何模型和网络，把文本的字和相邻两个字（中文）/单词（英文）哈希到固定维度。

它擅长"关键词是否出现"，配合向量模型的"语义相近"一起用，就是常说的混合检索。
"""

from __future__ import annotations

import re
import zlib

import numpy as np

DIM = 512
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]")


def tokens(text: str) -> list[str]:
    raw = [t.lower() for t in _WORD.findall(text)]
    out = list(raw)
    out.extend(a + b for a, b in zip(raw, raw[1:], strict=False) if len(a) == 1 and len(b) == 1)
    return out


def hash_embed(text: str, dim: int = DIM) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokens(text):
        h = zlib.crc32(tok.encode("utf-8"))
        vec[h % dim] += 1.0 if (h >> 16) & 1 else -1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def to_blob(vec: np.ndarray | list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if not na or not nb or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
