"""错题与知识卡片，使用 FSRS 间隔重复算法安排复习时间。

FSRS 根据每次复习的评分（1 忘了 / 2 困难 / 3 记得 / 4 简单）估计"记忆稳定性"，
算出下次最该复习的时间。比固定的 1-2-4-7 天更省时间。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fsrs import Card, Rating, Scheduler

from ..db import Database, now_ms

_scheduler = Scheduler(enable_fuzzing=False)


def _due_ms(card: Card) -> int:
    return int(card.due.timestamp() * 1000)


def new_card(
    db: Database,
    front: str,
    back: str,
    *,
    course_id: str | None = None,
    lesson_id: str | None = None,
    source: str = "concept",
) -> dict[str, Any]:
    card = Card()
    return db.upsert(
        "cards",
        {
            "front": front,
            "back": back,
            "course_id": course_id,
            "lesson_id": lesson_id,
            "source": source,
            "fsrs": card.to_dict(),
            "due": _due_ms(card),
        },
    )


def review(db: Database, card_id: str, rating: int, when: datetime | None = None) -> dict[str, Any]:
    rec = db.get("cards", card_id)
    if rec is None:
        raise KeyError(card_id)
    state = rec.get("fsrs")
    card = Card.from_dict(state) if isinstance(state, dict) and state else Card()
    updated, _ = _scheduler.review_card(card, Rating(int(rating)), review_datetime=when or datetime.now(UTC))
    return db.upsert("cards", {"id": card_id, "fsrs": updated.to_dict(), "due": _due_ms(updated)})


def due_cards(db: Database, until_ms: int | None = None, course_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    cutoff = until_ms or now_ms()
    cards = [c for c in db.list("cards", order="due") if (c.get("due") or 0) <= cutoff]
    if course_id:
        cards = [c for c in cards if c.get("course_id") == course_id]
    return cards[:limit]
