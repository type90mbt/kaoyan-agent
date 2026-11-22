"""教学质量评估：`kaoyan-eval evals/cs408-sample.jsonl [--task chat] [--skill teach]`

每行一道题：{"id": "...", "question": "...", "must_include": ["关键词", ...], "must_not_include": [...]}。
每次改了提示词、技能或换了模型，就跑一遍，对比得分有没有下降。结果保存在 evals/results/。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..app import build_state
from ..harness.context import BASE_SYSTEM, TUTOR_MODE
from ..llm.gateway import LLMError


def score(answer: str, must: list[str], must_not: list[str]) -> float:
    if not must and not must_not:
        return 1.0
    hit = sum(1 for k in must if k.lower() in answer.lower())
    bad = sum(1 for k in must_not if k.lower() in answer.lower())
    base = hit / len(must) if must else 1.0
    return max(0.0, base - 0.5 * bad)


def main() -> None:
    parser = argparse.ArgumentParser(description="教学质量评估")
    parser.add_argument("dataset")
    parser.add_argument("--task", default="chat")
    parser.add_argument("--skill", default="teach")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    state = build_state(enable_mcp=False)
    skill = state.skills.get(args.skill)
    system = BASE_SYSTEM + "\n\n" + TUTOR_MODE + (f"\n\n## 技能 {skill.name}\n{skill.body()}" if skill else "")
    rows = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    results, total = [], 0.0
    for row in rows:
        started = time.time()
        try:
            answer = state.gateway.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": row["question"]}], task=args.task
            ).content
        except LLMError as exc:
            answer = f"ERROR: {exc}"
        s = score(answer, row.get("must_include", []), row.get("must_not_include", []))
        total += s
        results.append({**row, "answer": answer, "score": s, "seconds": round(time.time() - started, 1)})
        print(f"[{s:.2f}] {row['id']}  {row['question'][:40]}")

    avg = total / len(rows) if rows else 0.0
    route = state.gateway.route_info(args.task)
    print(f"\n平均得分：{avg:.3f}（{len(rows)} 题，模型 {route['provider']}/{route['model']}，技能 {args.skill}）")
    out_dir = Path(args.dataset).parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{Path(args.dataset).stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"average": avg, "route": route, "skill": args.skill, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"详细结果：{out}")


if __name__ == "__main__":
    main()
