"""课件入库：保存文件 → 解析 → （可选）视觉模型识别图片页 → 切块向量化。"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from ..db import Database, new_id, now_ms
from ..llm.gateway import Gateway, LLMError
from ..rag.retriever import Retriever
from .parser import UnsupportedFormat, parse

VISION_PROMPT = (
    "这是一页课件的图片。请完整转写其中的文字、公式（用 LaTeX）和表格；如果有示意图，用两三句话描述图的含义。"
    "只输出内容本身，不要寒暄。"
)


class IngestService:
    def __init__(self, db: Database, gateway: Gateway, retriever: Retriever, materials_dir: Path, vision_enabled) -> None:
        self.db = db
        self.gateway = gateway
        self.retriever = retriever
        self.materials_dir = materials_dir
        self._vision_enabled = vision_enabled

    def add(self, filename: str, data: bytes, course_id: str | None, lesson_id: str | None, background: bool = True) -> dict[str, Any]:
        material_id = new_id()
        folder = self.materials_dir / material_id
        folder.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name or "material"
        path = folder / safe_name
        path.write_bytes(data)
        self.db.execute(
            "INSERT INTO materials (id, course_id, lesson_id, filename, path, kind, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (material_id, course_id, lesson_id, safe_name, str(path), path.suffix.lower().lstrip("."), "processing", now_ms()),
        )
        if background:
            threading.Thread(target=self._process, args=(material_id,), daemon=True).start()
        else:
            self._process(material_id)
        return self.get(material_id) or {}

    def get(self, material_id: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM materials WHERE id = ?", (material_id,))

    def pages(self, material_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT page, text FROM pages WHERE material_id = ? ORDER BY page", (material_id,))

    def delete(self, material_id: str) -> None:
        m = self.get(material_id)
        self.db.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        self.db.execute("DELETE FROM pages WHERE material_id = ?", (material_id,))
        self.db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        if m:
            shutil.rmtree(Path(m["path"]).parent, ignore_errors=True)

    def _process(self, material_id: str) -> None:
        m = self.get(material_id)
        if not m:
            return
        path = Path(m["path"])
        use_vision = bool(self._vision_enabled()) and self.gateway.available("vision")
        try:
            pages, preview = parse(path, path.parent, with_images=use_vision)
            result: list[tuple[int, str]] = []
            for page in pages:
                text = page.text
                for img in page.image_png_b64[:3]:
                    try:
                        text += "\n【图片识别】" + self.gateway.vision(VISION_PROMPT, img)
                    except LLMError:
                        break
                result.append((page.number, text))
                self.db.execute(
                    "INSERT OR REPLACE INTO pages (material_id, page, text) VALUES (?, ?, ?)",
                    (material_id, page.number, text),
                )
            count = self.retriever.index_pages(material_id, m["course_id"], m["lesson_id"], result)
            self.db.execute(
                "UPDATE materials SET status = ?, pages = ?, preview_pdf = ?, error = ? WHERE id = ?",
                ("ready" if count or pages else "empty", len(pages), str(preview) if preview else None, None, material_id),
            )
        except (UnsupportedFormat, OSError, ValueError, RuntimeError) as exc:
            self.db.execute("UPDATE materials SET status = ?, error = ? WHERE id = ?", ("error", str(exc), material_id))
