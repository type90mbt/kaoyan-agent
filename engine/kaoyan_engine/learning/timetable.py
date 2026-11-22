"""课表：周次计算、今日课程、.ics 导入、截图识别结果导入。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from ..db import Database

DEFAULT_SECTION_TIMES = [
    ["08:00", "08:45"], ["08:55", "09:40"], ["10:00", "10:45"], ["10:55", "11:40"],
    ["14:00", "14:45"], ["14:55", "15:40"], ["16:00", "16:45"], ["16:55", "17:40"],
    ["19:00", "19:45"], ["19:55", "20:40"], ["20:50", "21:35"], ["21:45", "22:30"],
]  # fmt: skip


def get_profile(db: Database) -> dict[str, Any]:
    profile = db.get("profile", "me")
    if profile is None:
        profile = db.upsert(
            "profile",
            {
                "id": "me",
                "exam_date": "2027-12-25",
                "tone": "gentle",
                "section_times": DEFAULT_SECTION_TIMES,
                "nickname": "同学",
            },
        )
    return profile


def week_number(term_start: str | None, day: date) -> int | None:
    if not term_start:
        return None
    start = date.fromisoformat(term_start)
    start = start - timedelta(days=start.weekday())
    return (day - start).days // 7 + 1


def classes_on(db: Database, day: date) -> list[dict[str, Any]]:
    profile = get_profile(db)
    week = week_number(profile.get("term_start"), day)
    sections = profile.get("section_times") or DEFAULT_SECTION_TIMES
    result = []
    for entry in db.list("timetable"):
        if int(entry.get("weekday") or 0) != day.isoweekday():
            continue
        weeks = entry.get("weeks") or []
        if week is not None and weeks and week not in weeks:
            continue
        start, end = entry.get("start_time"), entry.get("end_time")
        if not start and entry.get("start_section"):
            idx = int(entry["start_section"]) - 1
            start = sections[idx][0] if 0 <= idx < len(sections) else None
        if not end and entry.get("end_section"):
            idx = int(entry["end_section"]) - 1
            end = sections[idx][1] if 0 <= idx < len(sections) else None
        result.append({**entry, "start_time": start, "end_time": end, "week": week})
    return sorted(result, key=lambda e: e.get("start_time") or "99:99")


# ---------- .ics 导入 ----------
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_dt(value: str) -> datetime:
    value = value.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间 {value}")


def parse_ics(text: str) -> list[dict[str, Any]]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, value = line.split(":", 1)
            cur[key.split(";")[0].upper()] = value.replace("\\,", ",").replace("\\n", "\n")
    occurrences = []
    for ev in events:
        if "DTSTART" not in ev:
            continue
        start = _parse_dt(ev["DTSTART"])
        end = _parse_dt(ev["DTEND"]) if "DTEND" in ev else start + timedelta(minutes=90)
        dates = [start]
        rule = dict(p.split("=", 1) for p in ev.get("RRULE", "").split(";") if "=" in p)
        if rule.get("FREQ") == "WEEKLY":
            interval = int(rule.get("INTERVAL", 1))
            count = int(rule["COUNT"]) if "COUNT" in rule else None
            until = _parse_dt(rule["UNTIL"]) if "UNTIL" in rule else None
            limit = count or 30
            dates = []
            d = start
            while len(dates) < limit and (until is None or d <= until):
                dates.append(d)
                d += timedelta(weeks=interval)
        for d in dates:
            occurrences.append(
                {
                    "title": ev.get("SUMMARY", "未命名课程").strip(),
                    "location": ev.get("LOCATION", "").strip(),
                    "start": d,
                    "end": d + (end - start),
                    "description": ev.get("DESCRIPTION", ""),
                }
            )
    return occurrences


def import_ics(db: Database, text: str) -> int:
    occ = parse_ics(text)
    if not occ:
        return 0
    profile = get_profile(db)
    if not profile.get("term_start"):
        first = min(o["start"] for o in occ).date()
        term_start = (first - timedelta(days=first.weekday())).isoformat()
        profile = db.upsert("profile", {"id": "me", "term_start": term_start})
    groups: dict[tuple, set[int]] = defaultdict(set)
    meta: dict[tuple, dict[str, Any]] = {}
    for o in occ:
        key = (o["title"], o["start"].isoweekday(), o["start"].strftime("%H:%M"), o["end"].strftime("%H:%M"))
        week = week_number(profile["term_start"], o["start"].date())
        if week and week > 0:
            groups[key].add(week)
        meta[key] = o
    for key, weeks in groups.items():
        title, weekday, start, end = key
        db.upsert(
            "timetable",
            {
                "course_name": title,
                "weekday": weekday,
                "start_time": start,
                "end_time": end,
                "weeks": sorted(weeks),
                "location": meta[key]["location"],
            },
        )
    return len(groups)


# ---------- 截图识别 ----------
VISION_TIMETABLE_PROMPT = (
    "这是一张大学课表截图。请识别所有课程，只输出 JSON 数组，不要任何解释。每项格式："
    '{"course_name": "课程名", "weekday": 1到7, "start_section": 开始节次, "end_section": 结束节次, '
    '"weeks": [周次数字...], "location": "教室", "teacher": "老师"}。'
    "周次如 1-16周 展开为 [1,2,...,16]，单周只保留奇数，双周只保留偶数。"
)


def parse_week_spec(spec: str) -> list[int]:
    weeks: set[int] = set()
    odd = "单" in spec
    even = "双" in spec
    for a, b in re.findall(r"(\d+)\s*(?:-\s*(\d+))?", spec):
        lo, hi = int(a), int(b or a)
        for w in range(lo, hi + 1):
            if (odd and w % 2 == 0) or (even and w % 2 == 1):
                continue
            weeks.add(w)
    return sorted(weeks)


def import_vision_json(db: Database, raw: str) -> int:
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        return 0
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 0
    count = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("course_name"):
            continue
        weeks = item.get("weeks")
        if isinstance(weeks, str):
            weeks = parse_week_spec(weeks)
        db.upsert(
            "timetable",
            {
                "course_name": item["course_name"],
                "weekday": int(item.get("weekday") or 1),
                "start_section": item.get("start_section"),
                "end_section": item.get("end_section"),
                "weeks": weeks or [],
                "location": item.get("location", ""),
                "teacher": item.get("teacher", ""),
            },
        )
        count += 1
    return count
