"""skill 格式校验：`kaoyan-skill-lint <目录>`，按 DSH 规则检查一个 skill 根目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .loader import ALLOWED_KEYS, DESCRIPTION_LIMIT, SkillFormatError, parse_skill_file, split_frontmatter


def lint_root(root: Path) -> list[str]:
    problems: list[str] = []
    if not root.is_dir():
        return [f"{root} 不是目录"]
    names: set[str] = set()
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            path = entry / "SKILL.md"
            if not path.is_file():
                nested = list(entry.rglob("SKILL.md"))
                if nested:
                    problems.append(f"{entry}: DSH 只扫描一层，嵌套的 {nested[0]} 不会被发现")
                continue
            expected = entry.name
        elif entry.suffix.lower() == ".md":
            path = entry
            expected = entry.stem
        else:
            continue
        try:
            skill = parse_skill_file(path)
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (SkillFormatError, yaml.YAMLError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        extra = set(meta or {}) - ALLOWED_KEYS
        if extra:
            problems.append(f"{path}: 出现 DSH 不认识的顶层字段 {sorted(extra)}，扩展信息请放进 metadata")
        if skill.name != expected:
            problems.append(f"{path}: name {skill.name!r} 与文件夹/文件名 {expected!r} 不一致")
        if len(skill.description) > DESCRIPTION_LIMIT:
            problems.append(f"{path}: description 超过 {DESCRIPTION_LIMIT} 字符")
        if not body.strip():
            problems.append(f"{path}: 正文为空")
        if skill.name in names:
            problems.append(f"{path}: 重名 skill {skill.name}")
        names.add(skill.name)
    return problems


def main() -> None:
    roots = [Path(a) for a in sys.argv[1:]] or [Path(".agents/skills")]
    problems: list[str] = []
    for root in roots:
        problems.extend(lint_root(root))
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print(f"skill 校验通过：{', '.join(str(r) for r in roots)}")


if __name__ == "__main__":
    main()
