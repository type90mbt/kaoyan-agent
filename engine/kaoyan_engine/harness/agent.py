"""Agent 主循环。

一轮对话：保存用户消息 → 斜杠命令处理 → 检索课件 → 选择技能 → 组装上下文 →
调用模型（流式）→ 如果模型要调用工具：权限检查/审批/执行 → 把结果交回模型 → ……直到模型给出最终回答。
全部过程以事件流的形式交给界面：delta（文字）、tool_call、approval、tool_result、done 等。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..config import Settings
from ..db import Database, new_id, now_ms
from ..learning.timetable import get_profile
from ..llm.gateway import Gateway, LLMError
from ..rag.retriever import Retriever
from ..skills.loader import SkillRegistry
from ..tools.guard import Level
from ..tools.registry import Tool, ToolContext, ToolRegistry, params
from .context import build_system, history_messages
from .router import select_skills
from .summary import maybe_compact, summarize_session

MAX_STEPS = 8
SUMMARY_COMMANDS = {"lesson-summary", "总结", "summary"}

Event = dict[str, Any]


class Agent:
    def __init__(
        self,
        db: Database,
        gateway: Gateway,
        retriever: Retriever,
        skills: SkillRegistry,
        tools: ToolRegistry,
        settings_getter,
    ) -> None:
        self.db = db
        self.gateway = gateway
        self.retriever = retriever
        self.skills = skills
        self.tools = tools
        self._settings = settings_getter
        self._register_internal_tools()

    @property
    def settings(self) -> Settings:
        return self._settings()

    # ---------- 内部工具：加载技能、检索课件 ----------
    def _register_internal_tools(self) -> None:
        def load_skill(ctx: ToolContext, a: dict[str, Any]) -> str:
            skill = self.skills.get(str(a.get("name", "")))
            if not skill or not skill.model_invocable:
                return f"没有可用的技能 {a.get('name')}"
            return f"# 技能 {skill.name}\n{skill.body()}"

        def search_materials(ctx: ToolContext, a: dict[str, Any]) -> str:
            hits = self.retriever.search(
                str(a.get("query", "")), course_id=a.get("course_id") or ctx.extras.get("course_id"), k=6
            )
            if not hits:
                return "课件里没有找到相关内容"
            return "\n".join(
                f'<material file="{h.filename}" page="{h.page}">\n{h.text}\n</material>' for h in hits
            )

        self.tools.register(
            Tool(
                "load_skill",
                "加载一个技能的完整说明（技能目录见系统提示词）。",
                params({"name": {"type": "string"}}, ["name"]),
                Level.READ,
                load_skill,
            )
        )
        self.tools.register(
            Tool(
                "search_materials",
                "在已上传的课件里检索相关内容。默认只搜当前课程。",
                params({"query": {"type": "string"}, "course_id": {"type": "string"}}, ["query"]),
                Level.READ,
                search_materials,
            )
        )

    # ---------- 会话 ----------
    def create_session(
        self, *, lesson_id: str | None = None, course_id: str | None = None, title: str | None = None, origin: str = "desktop"
    ) -> dict[str, Any]:
        if lesson_id and not course_id:
            lesson = self.db.get("lessons", lesson_id)
            course_id = lesson.get("course_id") if lesson else None
            title = title or (lesson.get("title") if lesson else None)
            if lesson and lesson.get("status") in (None, "", "todo"):
                self.db.upsert("lessons", {"id": lesson_id, "status": "learning"})
        sid = new_id()
        self.db.execute(
            "INSERT INTO sessions (id, lesson_id, course_id, title, status, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, lesson_id, course_id, title or "新对话", "open", origin, now_ms()),
        )
        return self.db.one("SELECT * FROM sessions WHERE id = ?", (sid,)) or {}

    def _save(self, session_id: str, role: str, content: str | None, **extra: Any) -> None:
        self.db.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_calls, tool_call_id, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                session_id,
                role,
                content,
                json.dumps(extra["tool_calls"], ensure_ascii=False) if extra.get("tool_calls") else None,
                extra.get("tool_call_id"),
                extra.get("name"),
                now_ms(),
            ),
        )

    def _context_entities(self, session: dict[str, Any]) -> tuple[dict | None, dict | None, dict | None]:
        lesson = self.db.get("lessons", session["lesson_id"]) if session.get("lesson_id") else None
        course = self.db.get("courses", session["course_id"]) if session.get("course_id") else None
        subject = self.db.get("subjects", course["subject_id"]) if course and course.get("subject_id") else None
        return subject, course, lesson

    def _summaries(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        if session.get("lesson_id"):
            same = self.db.list("lesson_summaries", where={"lesson_id": session["lesson_id"]})
        else:
            same = self.db.list("lesson_summaries", where={"session_id": session["id"]})
        others = (
            self.db.list("lesson_summaries", where={"course_id": session["course_id"]}) if session.get("course_id") else []
        )
        seen, out = set(), []
        for s in [*same, *reversed(others)]:
            if s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
        return out[:4]

    # ---------- 一轮对话 ----------
    def run(self, session_id: str, text: str, mode: str = "chat") -> Iterator[Event]:
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not session:
            yield {"type": "error", "message": "会话不存在"}
            return
        if session.get("status") == "closed":
            self.db.execute("UPDATE sessions SET status = 'open' WHERE id = ?", (session_id,))

        forced: list = []
        stripped = text.strip()
        if stripped.startswith("/"):
            command, _, rest = stripped[1:].partition(" ")
            if command in SUMMARY_COMMANDS:
                yield from self._summary_events(session_id)
                return
            skill = self.skills.get(command)
            if skill and skill.user_invocable:
                forced.append(skill)
                text = rest.strip() or f"请按照技能 {skill.name} 开始。"
            elif not command.startswith(("tool", "工具")):
                yield {"type": "notice", "message": f"没有找到命令 /{command}，按普通消息处理"}

        self._save(session_id, "user", text)
        subject, course, lesson = self._context_entities(session)
        hits = self.retriever.search(
            text if not lesson else f"{lesson['title']} {text}",
            course_id=session.get("course_id"),
            lesson_id=session.get("lesson_id"),
            k=6,
        )
        if hits:
            yield {
                "type": "sources",
                "items": [{"file": h.filename, "page": h.page, "material_id": h.material_id, "score": round(h.score, 3)} for h in hits],
            }
        catalog = self.skills.model_catalog()
        prefix = subject.get("skill_prefix") if subject else None
        active = forced + [s for s in select_skills(catalog, text, prefix) if s not in forced]
        active = active[:3]
        if active:
            yield {"type": "skills", "items": [s.name for s in active]}

        tools_enabled = bool(self.settings.workspaces)
        system = build_system(
            tutor_mode=self.settings.tutor_mode,
            tools_enabled=tools_enabled,
            profile=get_profile(self.db),
            subject=subject,
            course=course,
            lesson=lesson,
            catalog=[s for s in catalog if s not in active],
            active_skills=active,
            summaries=self._summaries(session),
            hits=hits,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history_messages(self.db, session_id)]
        task = "reason" if mode == "deep" else "chat"
        tool_schemas = None if task == "reason" else self.tools.schemas(include_computer=tools_enabled)
        ctx = ToolContext(guard=self.tools.guard, session_id=session_id, extras={"course_id": session.get("course_id")})

        for _step in range(MAX_STEPS):
            result = None
            try:
                for kind, value in self.gateway.stream(messages, task=task, tools=tool_schemas):
                    if kind == "delta":
                        yield {"type": "delta", "text": value}
                    elif kind == "reasoning":
                        yield {"type": "reasoning", "text": value}
                    elif kind == "done":
                        result = value
            except LLMError as exc:
                yield {"type": "error", "message": str(exc)}
                return
            if result is None:
                break
            tool_calls = [tc.to_message() for tc in result.tool_calls]
            self._save(session_id, "assistant", result.content, tool_calls=tool_calls)
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": result.content or None}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            if result.usage:
                yield {"type": "usage", "usage": result.usage, "model": result.model}
            if not result.tool_calls:
                break
            for call in result.tool_calls:
                args = call.parsed_args()
                yield {"type": "tool_call", "id": call.id, "name": call.name, "args": args}
                output = ""
                for ev, val in self.tools.execute(call.name, args, ctx):
                    if ev == "approval":
                        yield {"type": "approval", **val}
                    else:
                        output = val["output"]
                        yield {"type": "tool_result", "id": call.id, "name": call.name, **val}
                self._save(session_id, "tool", output, tool_call_id=call.id, name=call.name)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        else:
            yield {"type": "notice", "message": f"已达到单轮最多 {MAX_STEPS} 步工具调用，先停在这里"}

        try:
            compacted = maybe_compact(
                self.db, self.gateway, self.skills, session_id, self.settings.compact_threshold_chars
            )
        except LLMError:
            compacted = None
        if compacted:
            yield {"type": "compacted", "summary_id": compacted["id"]}
        yield {"type": "done"}

    def _summary_events(self, session_id: str) -> Iterator[Event]:
        yield {"type": "notice", "message": "正在生成本节课的总结……"}
        try:
            record = summarize_session(self.db, self.gateway, self.skills, session_id, close=True)
        except LLMError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        if record is None:
            yield {"type": "notice", "message": "这节课还没有对话内容，无需总结"}
        else:
            yield {"type": "summary", "summary": record}
        yield {"type": "done"}
