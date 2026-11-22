"""课件解析：把 PDF / PPTX / DOCX / TXT / MD 统一转成"按页的文字"。

- PDF：PyMuPDF 逐页取文字；文字很少的页（多半是图片或公式截图）可以渲染成图片交给视觉模型识别。
- PPTX：python-pptx 读每页的文本框、表格和备注。
- 老格式 PPT / DOC：如果装了 LibreOffice，先转成 PDF 再解析。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document
from pptx import Presentation

SUPPORTED = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".txt", ".md"}
LOW_TEXT = 40


@dataclass
class Page:
    number: int
    text: str
    image_png_b64: list[str] = field(default_factory=list)


class UnsupportedFormat(ValueError):
    pass


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def convert_to_pdf(path: Path, out_dir: Path) -> Path | None:
    soffice = find_soffice()
    if not soffice:
        return None
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
        check=False,
        capture_output=True,
        timeout=180,
    )
    pdf = out_dir / (path.stem + ".pdf")
    return pdf if pdf.exists() else None


def parse_pdf(path: Path, render_low_text: bool = False) -> list[Page]:
    pages: list[Page] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            images: list[str] = []
            if render_low_text and len(text) < LOW_TEXT:
                pix = page.get_pixmap(dpi=110)
                images.append(base64.b64encode(pix.tobytes("png")).decode())
            pages.append(Page(i, text, images))
    return pages


def parse_pptx(path: Path, collect_images: bool = False) -> list[Page]:
    prs = Presentation(str(path))
    pages: list[Page] = []
    for i, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        images: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        lines.append(("  " * para.level) + t)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
            if collect_images and shape.shape_type == 13:  # PICTURE
                try:
                    images.append(base64.b64encode(shape.image.blob).decode())
                except (AttributeError, ValueError):
                    pass
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"【备注】{notes}")
        text = "\n".join(lines)
        pages.append(Page(i, text, images if len(text) < LOW_TEXT else []))
    return pages


def parse_docx(path: Path, page_chars: int = 1500) -> list[Page]:
    doc = Document(str(path))
    pages: list[Page] = []
    buf: list[str] = []
    size = 0
    for para in doc.paragraphs:
        t = para.text.strip()
        if not t:
            continue
        if para.style is not None and para.style.name.lower().startswith("heading"):
            t = "# " + t
        buf.append(t)
        size += len(t)
        if size >= page_chars:
            pages.append(Page(len(pages) + 1, "\n".join(buf)))
            buf, size = [], 0
    for table in doc.tables:
        for row in table.rows:
            buf.append(" | ".join(c.text.strip() for c in row.cells))
    if buf:
        pages.append(Page(len(pages) + 1, "\n".join(buf)))
    return pages


def parse_text(path: Path, page_chars: int = 1500) -> list[Page]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    pages: list[Page] = []
    for start in range(0, max(len(text), 1), page_chars):
        chunk = text[start : start + page_chars]
        if chunk.strip():
            pages.append(Page(len(pages) + 1, chunk))
    return pages


def parse(path: Path, work_dir: Path, with_images: bool = False) -> tuple[list[Page], Path | None]:
    """返回 (页面列表, 用于预览的 PDF 路径)。"""
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFormat(f"暂不支持 {ext} 格式，支持：{', '.join(sorted(SUPPORTED))}")
    if ext == ".pdf":
        return parse_pdf(path, with_images), path
    if ext == ".pptx":
        preview = convert_to_pdf(path, work_dir)
        return parse_pptx(path, with_images), preview
    if ext in (".ppt", ".doc"):
        pdf = convert_to_pdf(path, work_dir)
        if not pdf:
            raise UnsupportedFormat(f"{ext} 是旧格式，需要安装 LibreOffice 才能解析，或者先另存为 {ext}x")
        return parse_pdf(pdf, with_images), pdf
    if ext == ".docx":
        return parse_docx(path), convert_to_pdf(path, work_dir)
    return parse_text(path), None
