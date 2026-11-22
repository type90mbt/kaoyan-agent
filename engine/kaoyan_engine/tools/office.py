"""生成 Word / PPT / Excel 文件。Word 内容用简化的 Markdown 书写，方便模型输出。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Pt as PptPt

_NUMBERED = re.compile(r"^\d+[.、)]\s+")


def write_docx(path: Path, content: str, title: str | None = None) -> str:
    doc = Document()
    if title:
        doc.add_heading(title, level=0)
    in_code = False
    code_lines: list[str] = []
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_rows
        rows = [r for r in table_rows if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
        if rows:
            table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
            table.style = "Table Grid"
            for i, row in enumerate(rows):
                for j, cell in enumerate(row):
                    table.cell(i, j).text = cell.strip()
        table_rows = []

    for line in content.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                para = doc.add_paragraph()
                run = para.add_run("\n".join(code_lines))
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
                code_lines = []
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_rows.append(stripped.strip("|").split("|"))
            continue
        if table_rows:
            flush_table()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if heading:
            doc.add_heading(heading.group(2), level=len(heading.group(1)))
        elif stripped.startswith(("- ", "* ")):
            doc.add_paragraph(stripped[2:], style="List Bullet")
        elif _NUMBERED.match(stripped):
            doc.add_paragraph(_NUMBERED.sub("", stripped), style="List Number")
        else:
            doc.add_paragraph(stripped)
    if table_rows:
        flush_table()
    if code_lines:
        doc.add_paragraph("\n".join(code_lines))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return f"已生成 Word 文档：{path}"


def write_pptx(path: Path, slides: list[dict[str, Any]], title: str | None = None) -> str:
    prs = Presentation()
    if title:
        s = prs.slides.add_slide(prs.slide_layouts[0])
        s.shapes.title.text = title
    for spec in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = str(spec.get("title", ""))
        body = s.placeholders[1].text_frame
        body.clear()
        for i, bullet in enumerate(spec.get("bullets") or []):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            text = str(bullet)
            level = (len(text) - len(text.lstrip(" "))) // 2
            para.text = text.strip()
            para.level = min(level, 4)
            for run in para.runs:
                run.font.size = PptPt(20 if level == 0 else 16)
        if spec.get("notes"):
            s.notes_slide.notes_text_frame.text = str(spec["notes"])
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    return f"已生成 PPT：{path}（共 {len(prs.slides)} 页）"


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in (sheets or {"Sheet1": []}).items():
        ws = wb.create_sheet(title=str(name)[:31] or "Sheet")
        for row in rows:
            ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return f"已生成 Excel：{path}"
