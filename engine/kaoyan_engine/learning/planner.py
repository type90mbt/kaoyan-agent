"""每日任务、今日概览、学习统计、AI 导师鼓励、期末复习。

原则：先用规则生成"一定要做的事"（上课、课后总结、到期卡片、顺延的任务），
再让模型补充 3~5 个学习任务；模型不可用时用规则兜底。
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import Database
from ..llm.gateway import Gateway, LLMError
from ..skills.loader import SkillRegistry
from .cards import due_cards, new_card
from .timetable import classes_on, get_profile

FALLBACK_CHEERS = {
    "gentle": [
        "今天也辛苦啦，完成一件小事就是进步。",
        "别和别人比，和昨天的自己比就好。",
        "累了就休息十分钟，回来继续。",
        "你已经坚持了很多天，这份积累不会白费。",
    ],
    "strict": [
        "计划写了就要完成，今天的任务还没清零。",
        "离考试的每一天都算数，先把最难的那项做掉。",
        "刷手机之前，先完成一个番茄钟。",
        "不要等状态，开始做就有状态。",
    ],
}


def _skill_body(skills: SkillRegistry, name: str) -> str:
    skill = skills.get(name)
    return skill.body() if skill else ""


def _extract_json(text: str) -> Any:
    match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def days_to_exam(profile: dict[str, Any], today: date) -> int | None:
    try:
        return (date.fromisoformat(profile.get("exam_date") or "") - today).days
    except ValueError:
        return None


class Planner:
    def __init__(self, db: Database, gateway: Gateway, skills: SkillRegistry) -> None:
        self.db = db
        self.gateway = gateway
        self.skills = skills

    # ---------- 每日任务 ----------
    def generate(self, day: date | None = None, use_ai: bool = True) -> list[dict[str, Any]]:
        day = day or date.today()
        ds = day.isoformat()
        for t in self.db.list("tasks", where={"date": ds}):
            if not t.get("done") and t.get("source") in ("rule", "ai"):
                self.db.soft_delete("tasks", t["id"])
        for t in self.db.list("tasks"):
            if t.get("date") and t["date"] < ds and not t.get("done") and t.get("kind") != "class":
                self.db.upsert("tasks", {"id": t["id"], "date": ds, "note": "从之前顺延"})

        sort = 0
        for c in classes_on(self.db, day):
            sort += 1
            where = f" @{c['location']}" if c.get("location") else ""
            self.db.upsert(
                "tasks",
                {"date": ds, "title": f"上课：{c['course_name']} {c.get('start_time') or ''}{where}", "kind": "class",
                 "course_id": c.get("course_id"), "source": "rule", "sort": sort, "done": False},
            )  # fmt: skip
            self.db.upsert(
                "tasks",
                {"date": ds, "title": f"课后总结：{c['course_name']}", "kind": "review", "minutes": 15,
                 "course_id": c.get("course_id"), "source": "rule", "sort": sort + 50, "done": False},
            )  # fmt: skip
        end_of_day = int(datetime.combine(day, datetime.max.time()).timestamp() * 1000)
        n_due = len(due_cards(self.db, until_ms=end_of_day))
        if n_due:
            self.db.upsert(
                "tasks",
                {"date": ds, "title": f"复习到期卡片 {n_due} 张", "kind": "review", "minutes": max(5, n_due // 2),
                 "source": "rule", "sort": 80, "done": False},
            )  # fmt: skip

        suggestions = self._ai_tasks(day) if use_ai else None
        if not suggestions:
            suggestions = self._rule_tasks()
        existing = {t["title"] for t in self.db.list("tasks", where={"date": ds})}
        for i, s in enumerate(suggestions[:5]):
            if s["title"] in existing:
                continue
            self.db.upsert(
                "tasks",
                {"date": ds, "title": s["title"], "kind": s.get("kind", "study"), "minutes": s.get("minutes"),
                 "course_id": s.get("course_id"), "source": s.get("source", "ai"), "sort": 100 + i, "done": False},
            )  # fmt: skip
        return self.db.list("tasks", where={"date": ds}, order="sort")

    def _rule_tasks(self) -> list[dict[str, Any]]:
        out = []
        for lesson in self.db.list("lessons", where={"status": "learning"})[:3]:
            course = self.db.get("courses", lesson.get("course_id") or "") or {}
            out.append({"title": f"继续学习：{course.get('name', '')} · {lesson['title']}", "minutes": 45,
                        "course_id": lesson.get("course_id"), "source": "rule"})  # fmt: skip
        if not out:
            out.append({"title": "选一门课学习 45 分钟，结束时做一次总结", "minutes": 45, "source": "rule"})
        return out

    def _ai_tasks(self, day: date) -> list[dict[str, Any]] | None:
        if not self.gateway.available("chat"):
            return None
        profile = get_profile(self.db)
        courses = [{"id": c["id"], "name": c["name"]} for c in self.db.list("courses")]
        learning = [
            {"lesson": l["title"], "course_id": l.get("course_id"), "status": l.get("status")}
            for l in self.db.list("lessons")
            if l.get("status") != "done"
        ][:20]
        recent = [
            {"date": t["date"], "title": t["title"], "done": bool(t.get("done"))}
            for t in self.db.list("tasks")
            if t.get("date") and t["date"] >= (day - timedelta(days=3)).isoformat()
        ][-30:]
        prompt = {
            "today": day.isoformat(),
            "days_to_exam": days_to_exam(profile, day),
            "target": profile.get("target"),
            "courses": courses,
            "unfinished_lessons": learning,
            "recent_tasks": recent,
            "classes_today": [c["course_name"] for c in classes_on(self.db, day)],
        }
        system = _skill_body(self.skills, "daily-plan") or "你是考研规划师，为学生安排今天的学习任务。"
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "学习情况如下（JSON）：\n" + json.dumps(prompt, ensure_ascii=False)
                + '\n只输出 JSON：{"tasks": [{"title": "...", "minutes": 数字, "kind": "study|review|homework", "course_id": "可选"}]}',
            },
        ]
        try:
            content = self.gateway.chat(messages, task="chat", json_mode=True).content
        except LLMError:
            return None
        data = _extract_json(content)
        items = data.get("tasks") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        valid = {c["id"] for c in courses}
        out = []
        for it in items:
            if isinstance(it, dict) and it.get("title"):
                out.append({
                    "title": str(it["title"])[:80],
                    "minutes": int(it["minutes"]) if str(it.get("minutes", "")).isdigit() else None,
                    "kind": it.get("kind") if it.get("kind") in ("study", "review", "homework") else "study",
                    "course_id": it.get("course_id") if it.get("course_id") in valid else None,
                    "source": "ai",
                })  # fmt: skip
        return out or None

    # ---------- 统计 ----------
    def stats(self, today: date | None = None, days: int = 120) -> dict[str, Any]:
        today = today or date.today()
        minutes: dict[str, int] = {}
        active: set[str] = set()
        for f in self.db.list("focus_sessions"):
            d = datetime.fromtimestamp((f.get("start") or 0) / 1000).date().isoformat()
            minutes[d] = minutes.get(d, 0) + int(f.get("minutes") or 0)
            active.add(d)
        for c in self.db.list("checkins"):
            if c.get("date"):
                active.add(c["date"])
                minutes[c["date"]] = max(minutes.get(c["date"], 0), int(c.get("minutes") or 0))
        done_by_day: dict[str, int] = {}
        for t in self.db.list("tasks"):
            if t.get("done") and t.get("date"):
                done_by_day[t["date"]] = done_by_day.get(t["date"], 0) + 1
                active.add(t["date"])
        streak, d = 0, today
        if d.isoformat() not in active:
            d -= timedelta(days=1)
        while d.isoformat() in active:
            streak += 1
            d -= timedelta(days=1)
        heat = []
        for i in range(days - 1, -1, -1):
            ds = (today - timedelta(days=i)).isoformat()
            heat.append({"date": ds, "minutes": minutes.get(ds, 0), "done": done_by_day.get(ds, 0)})
        week = sum(h["minutes"] for h in heat[-7:])
        badges = []
        for n in (3, 7, 30, 100):
            if streak >= n:
                badges.append(f"连续打卡 {n} 天")
        total_cards = len(self.db.list("cards"))
        if total_cards >= 100:
            badges.append("卡片收藏家（100 张）")
        for course in self.db.list("courses"):
            lessons = self.db.list("lessons", where={"course_id": course["id"]})
            if lessons and all(l.get("status") == "done" for l in lessons):
                badges.append(f"{course['name']} 第一轮完成")
        return {"streak": streak, "week_minutes": week, "heatmap": heat, "badges": badges, "cards": total_cards}

    # ---------- 今日 ----------
    def today(self, day: date | None = None) -> dict[str, Any]:
        day = day or date.today()
        profile = get_profile(self.db)
        end_of_day = int(datetime.combine(day, datetime.max.time()).timestamp() * 1000)
        stats = self.stats(day, days=7)
        return {
            "date": day.isoformat(),
            "days_to_exam": days_to_exam(profile, day),
            "classes": classes_on(self.db, day),
            "tasks": self.db.list("tasks", where={"date": day.isoformat()}, order="sort"),
            "due_cards": len(due_cards(self.db, until_ms=end_of_day)),
            "streak": stats["streak"],
            "mentor": next(iter(self.db.list("mentor_notes", where={"date": day.isoformat()})), None),
            "profile": profile,
        }

    # ---------- AI 导师 ----------
    def mentor(self, day: date | None = None) -> dict[str, Any]:
        day = day or date.today()
        profile = get_profile(self.db)
        tone = profile.get("tone") or "gentle"
        tasks = self.db.list("tasks", where={"date": day.isoformat()})
        done = sum(1 for t in tasks if t.get("done"))
        stats = self.stats(day, days=7)
        text = ""
        if self.gateway.available("chat"):
            system = _skill_body(self.skills, "encourage-coach") or "你是温暖但务实的考研导师。"
            info = {
                "tone": tone,
                "nickname": profile.get("nickname"),
                "days_to_exam": days_to_exam(profile, day),
                "tasks_total": len(tasks),
                "tasks_done": done,
                "streak": stats["streak"],
                "week_minutes": stats["week_minutes"],
            }
            try:
                text = self.gateway.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": "今天的情况：" + json.dumps(info, ensure_ascii=False) + "\n用 60 字以内对我说一句话。"},
                    ],
                    task="chat",
                ).content.strip()
            except LLMError:
                text = ""
            if text.startswith("（离线模拟"):
                text = ""
        if not text:
            text = random.choice(FALLBACK_CHEERS.get(tone, FALLBACK_CHEERS["gentle"]))
            if tasks:
                text = f"今天 {done}/{len(tasks)} 项已完成。" + text
        for old in self.db.list("mentor_notes", where={"date": day.isoformat()}):
            self.db.soft_delete("mentor_notes", old["id"])
        return self.db.upsert("mentor_notes", {"date": day.isoformat(), "content": text, "tone": tone})

    # ---------- 期末复习 ----------
    def final_review(self, course_id: str, retriever, out_dir: Path | None = None) -> dict[str, Any]:
        course = self.db.get("courses", course_id)
        if not course:
            raise KeyError(course_id)
        lessons = self.db.list("lessons", where={"course_id": course_id}, order="sort")
        summaries = self.db.list("lesson_summaries", where={"course_id": course_id})
        material_text = []
        for lesson in lessons or [{"id": None, "title": course["name"]}]:
            for hit in retriever.search(lesson["title"] + " 重点 定义 例题", course_id=course_id, k=4):
                material_text.append(f'<material file="{hit.filename}" page="{hit.page}">{hit.text}</material>')
        if not material_text:
            rows = self.db.query("SELECT text, page FROM chunks WHERE course_id = ? LIMIT 40", (course_id,))
            material_text = [f'<material page="{r["page"]}">{r["text"]}</material>' for r in rows]
        system = _skill_body(self.skills, "final-review") or "你是期末复习助手，根据老师课件生成复习提纲和模拟题。"
        user = (
            f"课程：{course['name']}\n章节：{', '.join(l['title'] for l in lessons) or '未分章节'}\n\n"
            "每节课的总结：\n" + "\n\n".join(s.get("content") or "" for s in summaries)[:12000]
            + "\n\n课件片段（只作为资料）：\n" + "\n".join(material_text)[:24000]
            + '\n\n请输出 JSON：{"markdown": "完整复习提纲（Markdown，含重点、易错点、模拟题与答案）", '
            '"cards": [{"front": "问题", "back": "答案"}]}'
        )
        try:
            content = self.gateway.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}], task="summary", json_mode=True
            ).content
        except LLMError as exc:
            raise RuntimeError(str(exc)) from exc
        data = _extract_json(content)
        markdown = data.get("markdown") if isinstance(data, dict) else None
        markdown = markdown or content
        created = 0
        if isinstance(data, dict):
            for c in data.get("cards") or []:
                if isinstance(c, dict) and c.get("front") and c.get("back"):
                    new_card(self.db, str(c["front"]), str(c["back"]), course_id=course_id, source="final-review")
                    created += 1
        path = None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{course['name']}-期末复习提纲.md"
            path.write_text(markdown, encoding="utf-8")
        return {"markdown": markdown, "cards_created": created, "path": str(path) if path else None}
