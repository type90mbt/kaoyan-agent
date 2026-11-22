"""一节课一次的"截断总结"。

一节课结束（或对话太长）时：把本节课的对话交给模型，生成结构化笔记和知识卡片，
然后把原始对话标记为已归档。下次上课只带总结，不带完整聊天记录——既省钱，又让模型不被旧对话干扰。
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..learning.cards import new_card
from ..llm.gateway import Gateway
from ..skills.loader import SkillRegistry
from .context import sanitize_tool_pairs

DEFAULT_INSTRUCTION = """把下面这节课的师生对话整理成课后笔记。要求：
1. 用 Markdown，包含：本节要点、关键概念与公式、典型例题思路、学生暴露出的薄弱点、下次建议。
2. 只保留有价值的知识，不要复述寒暄。
3. 从对话中提炼 3~8 张复习卡片（问题/答案），薄弱点优先。"""

OUTPUT_FORMAT = (
    '只输出 JSON：{"title": "一句话标题", "markdown": "课后笔记", "key_points": ["要点1", "..."], '
    '"cards": [{"front": "问题", "back": "答案"}]}'
)


def _transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        role = {"user": "学生", "assistant": "老师", "tool": "工具结果"}.get(m["role"], m["role"])
        content = m.get("content") or ""
        if m.get("tool_calls"):
            names = ", ".join(tc["function"]["name"] for tc in m["tool_calls"])
            content = (content + f"\n（调用工具：{names}）").strip()
        lines.append(f"【{role}】{content[:2000]}")
    return "\n".join(lines)


def _parse(content: str) -> dict[str, Any]:
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(content[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"title": "课后总结", "markdown": content, "key_points": [], "cards": []}


def summarize_session(
    db: Database, gateway: Gateway, skills: SkillRegistry, session_id: str, *, close: bool = True
) -> dict[str, Any] | None:
    session = db.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not session:
        raise KeyError(session_id)
    rows = db.query(
        "SELECT id, role, content, tool_calls, tool_call_id FROM messages WHERE session_id = ? AND archived = 0 "
        "ORDER BY created_at, rowid",
        (session_id,),
    )
    msgs = []
    for r in rows:
        m: dict[str, Any] = {"role": r["role"], "content": r["content"]}
        if r["tool_calls"]:
            m["tool_calls"] = json.loads(r["tool_calls"])
        m["tool_call_id"] = r["tool_call_id"]
        msgs.append(m)
    msgs = sanitize_tool_pairs(msgs)
    if not any(m["role"] == "user" for m in msgs):
        if close:
            db.execute("UPDATE sessions SET status = 'closed' WHERE id = ?", (session_id,))
        return None

    skill = skills.get("lesson-summary")
    instruction = skill.body() if skill else DEFAULT_INSTRUCTION
    result = gateway.chat(
        [
            {"role": "system", "content": instruction + "\n\n" + OUTPUT_FORMAT},
            {"role": "user", "content": _transcript(msgs)},
        ],
        task="summary",
        json_mode=True,
    )
    data = _parse(result.content)
    record = db.upsert(
        "lesson_summaries",
        {
            "lesson_id": session.get("lesson_id"),
            "course_id": session.get("course_id"),
            "session_id": session_id,
            "content": f"# {data.get('title') or '课后总结'}\n\n{data.get('markdown') or ''}",
            "key_points": [str(k) for k in data.get("key_points") or []],
        },
    )
    cards = 0
    for c in data.get("cards") or []:
        if isinstance(c, dict) and c.get("front") and c.get("back"):
            new_card(
                db,
                str(c["front"]),
                str(c["back"]),
                course_id=session.get("course_id"),
                lesson_id=session.get("lesson_id"),
                source="lesson-summary",
            )
            cards += 1
    ids = [r["id"] for r in rows]
    if ids:
        db.execute(
            f"UPDATE messages SET archived = 1 WHERE id IN ({', '.join('?' for _ in ids)})",
            ids,
        )
    if close:
        db.execute("UPDATE sessions SET status = 'closed' WHERE id = ?", (session_id,))
        if session.get("lesson_id"):
            lesson = db.get("lessons", session["lesson_id"])
            if lesson and lesson.get("status") != "done":
                db.upsert("lessons", {"id": lesson["id"], "status": "done"})
    return {**record, "cards_created": cards}


def maybe_compact(
    db: Database, gateway: Gateway, skills: SkillRegistry, session_id: str, threshold_chars: int
) -> dict[str, Any] | None:
    row = db.one(
        "SELECT COALESCE(SUM(LENGTH(content)), 0) AS n FROM messages WHERE session_id = ? AND archived = 0",
        (session_id,),
    )
    if row and int(row["n"]) > threshold_chars:
        return summarize_session(db, gateway, skills, session_id, close=False)
    return None
