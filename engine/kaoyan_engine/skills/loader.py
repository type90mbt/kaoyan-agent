"""兼容 DeepSeek Harness（DSH）的 skill 加载器。

规则与 DSH `dsh-skill-filesystem` 保持一致：
- 形态：`<root>/<name>/SKILL.md` 目录包，或 `<root>/<name>.md` 平铺文件；只扫描一层。
- name 必须是 kebab-case；frontmatter 必填 name、description。
- 可选 whenToUse、metadata、disable-model-invocation、user-invocable；两个开关省略时默认允许。
- 布尔值接受 true/false、yes/no、on/off、1/0（不区分大小写），其它值丢弃整个 skill 并给出警告。
- 旧的驼峰键（disableModelInvocation、modelInvocable、userInvocable）直接报错丢弃。
- 多个根目录出现同名 skill 时，优先级高（rank 小）的生效。
- 目录信息在扫描时解析，正文每次加载时重新读取，编辑后无需重启。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_KEYS = {"name", "description", "whenToUse", "metadata", "disable-model-invocation", "user-invocable"}
LEGACY_KEYS = {
    "disableModelInvocation": "disable-model-invocation",
    "modelInvocable": "disable-model-invocation",
    "userInvocable": "user-invocable",
}
TRUE_WORDS = {"true", "yes", "on", "1"}
FALSE_WORDS = {"false", "no", "off", "0"}
SCRIPT_SUFFIXES = {".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".exe"}
DESCRIPTION_LIMIT = 500


class SkillFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SkillRoot:
    rank: int
    source: str
    path: Path
    skip_system: bool = False


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    source: str
    rank: int
    when_to_use: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    model_invocable: bool = True
    user_invocable: bool = True
    has_scripts: bool = False

    @property
    def resource_base(self) -> Path:
        return self.path.parent

    def body(self) -> str:
        _, body = split_frontmatter(self.path.read_text(encoding="utf-8"))
        return body.strip()

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description[:DESCRIPTION_LIMIT],
            "whenToUse": self.when_to_use,
            "metadata": self.metadata,
            "invocation": {"modelInvocable": self.model_invocable, "userInvocable": self.user_invocable},
            "source": self.source,
            "path": str(self.path),
            "hasScripts": self.has_scripts,
        }


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            head = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            data = yaml.safe_load(head) or {}
            if not isinstance(data, dict):
                raise SkillFormatError("frontmatter 必须是 YAML 映射")
            return data, body
    raise SkillFormatError("frontmatter 没有结束的 ---")


def parse_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in TRUE_WORDS:
            return True
        if low in FALSE_WORDS:
            return False
    raise SkillFormatError(f"frontmatter 字段 {key!r} 不是合法的布尔值：{value!r}")


def parse_skill_file(path: Path, source: str = "custom", rank: int = 300) -> Skill:
    meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    if meta is None:
        raise SkillFormatError("缺少 YAML frontmatter")
    for legacy, replacement in LEGACY_KEYS.items():
        if legacy in meta:
            raise SkillFormatError(f'frontmatter field "{legacy}" is unsupported; use "{replacement}"')
    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        raise SkillFormatError("frontmatter 必须包含 name 和 description")
    name = str(name)
    if not NAME_RE.match(name):
        raise SkillFormatError(f"name {name!r} 不是 kebab-case（只能用小写字母、数字和连字符）")
    disable_model = parse_bool(meta["disable-model-invocation"], "disable-model-invocation") if "disable-model-invocation" in meta else False
    user_invocable = parse_bool(meta["user-invocable"], "user-invocable") if "user-invocable" in meta else True
    metadata = meta.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SkillFormatError("metadata 必须是映射")
    has_scripts = False
    if path.name == "SKILL.md":
        has_scripts = any(p.suffix.lower() in SCRIPT_SUFFIXES for p in path.parent.rglob("*") if p.is_file())
    return Skill(
        name=name,
        description=str(description),
        path=path,
        source=source,
        rank=rank,
        when_to_use=str(meta["whenToUse"]) if meta.get("whenToUse") else None,
        metadata=metadata,
        model_invocable=not disable_model,
        user_invocable=user_invocable,
        has_scripts=has_scripts,
    )


def find_project_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return cur


def default_roots(
    project_dir: Path | None,
    custom_dirs: list[str],
    kaoyan_skills_dir: Path | None,
    bundled_dir: Path | None,
) -> list[SkillRoot]:
    home = Path.home()
    dsh_home = Path(os.environ.get("DSH_HOME", home / ".dsh"))
    agents_home = Path(os.environ.get("DSH_AGENTS_HOME", home / ".agents"))
    roots: list[SkillRoot] = []
    if project_dir is not None:
        project = find_project_root(project_dir)
        roots.append(SkillRoot(100, "project-dsh", project / ".dsh" / "skills"))
        roots.append(SkillRoot(200, "project-agents", project / ".agents" / "skills"))
    for d in custom_dirs:
        roots.append(SkillRoot(300, "custom", Path(d).expanduser()))
    roots.append(SkillRoot(400, "user-dsh", dsh_home / "skills", skip_system=True))
    roots.append(SkillRoot(500, "user-agents", agents_home / "skills"))
    if kaoyan_skills_dir is not None:
        roots.append(SkillRoot(550, "user-kaoyan", kaoyan_skills_dir))
    if bundled_dir is not None:
        roots.append(SkillRoot(600, "bundled", bundled_dir))
    return roots


class SkillRegistry:
    def __init__(self, roots_provider) -> None:
        """roots_provider: 无参函数，返回当前的 SkillRoot 列表（设置改动后会立即生效）。"""
        self._roots_provider = roots_provider
        self.warnings: list[str] = []

    def roots(self) -> list[SkillRoot]:
        return sorted(self._roots_provider(), key=lambda r: r.rank)

    def scan(self) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        warnings: list[str] = []
        for root in self.roots():
            try:
                entries = sorted(root.path.iterdir()) if root.path.is_dir() else []
            except OSError as exc:
                warnings.append(f"{root.path}: 无法读取（{exc}）")
                continue
            for entry in entries:
                if root.skip_system and entry.name == ".system":
                    continue
                if entry.is_dir():
                    candidate = entry / "SKILL.md"
                    if not candidate.is_file():
                        continue
                elif entry.is_file() and entry.suffix.lower() == ".md":
                    candidate = entry
                else:
                    continue
                try:
                    skill = parse_skill_file(candidate, root.source, root.rank)
                except (SkillFormatError, yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
                    warnings.append(f"{candidate}: {exc}")
                    continue
                if skill.name not in found:
                    found[skill.name] = skill
        self.warnings = warnings
        return found

    def list(self) -> list[Skill]:
        return sorted(self.scan().values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return self.scan().get(name)

    def model_catalog(self) -> list[Skill]:
        return [s for s in self.list() if s.model_invocable]

    def user_commands(self) -> list[Skill]:
        return [s for s in self.list() if s.user_invocable]
