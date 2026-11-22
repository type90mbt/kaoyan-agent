"""技能路由：根据课程所属学科和用户意图，预先选出最多 2 个 skill 放进上下文；
其余 skill 只把"名字 + 描述"列进目录，模型需要时调用 load_skill 工具加载。
"""

from __future__ import annotations

from ..rag.vectors import cosine, hash_embed
from ..skills.loader import Skill

INTENT_WORDS: dict[str, tuple[str, ...]] = {
    "teach": ("讲", "解释", "什么是", "为什么", "原理", "推导", "怎么理解", "区别", "概念"),
    "quiz": ("出题", "考考", "测试", "练习", "题目", "做题", "检验"),
    "homework": ("作业", "实验报告", "报告", "实验", "习题"),
    "paper": ("论文", "文献", "摘要", "开题", "综述", "参考文献", "降重"),
    "project": ("项目", "代码", "bug", "报错", "部署", "程序"),
    "review": ("复习", "期末", "提纲", "重点", "考点", "总结"),
    "plan": ("计划", "安排", "规划", "进度"),
}


def detect_intents(text: str) -> set[str]:
    low = text.lower()
    return {kind for kind, words in INTENT_WORDS.items() if any(w in low for w in words)}


def select_skills(skills: list[Skill], text: str, subject_prefix: str | None, limit: int = 2) -> list[Skill]:
    intents = detect_intents(text) or {"teach"}
    q = hash_embed(text)
    scored: list[tuple[float, Skill]] = []
    for s in skills:
        if not s.model_invocable:
            continue
        meta = s.metadata or {}
        score = 0.0
        subject = str(meta.get("subject") or "")
        if subject_prefix and (s.name.startswith(subject_prefix) or subject == subject_prefix):
            score += 2.0
        elif subject and subject not in ("general", ""):
            score -= 1.0
        if str(meta.get("kind") or "") in intents:
            score += 2.0
        score += 2.0 * cosine(q, hash_embed(f"{s.description} {s.when_to_use or ''}"))
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for score, s in scored[:limit] if score >= 2.0]
