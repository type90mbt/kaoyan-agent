"""skill 安装：从 zip 包或 GitHub 地址安装到 `~/.kaoyan/skills`。

- 自动在压缩包里查找所有包含 SKILL.md 的文件夹（比如仓库里的 `.agents/skills/xxx/`）。
- 防止压缩包路径穿越（zip slip）。
- 带脚本的 skill 会在结果里标记，界面需要提示用户。
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .loader import SkillFormatError, parse_skill_file

GITHUB_RE = re.compile(r"github\.com/([^/]+)/([^/#?]+)(?:/tree/([^/]+)(?:/(.*))?)?")
MAX_ZIP_BYTES = 50 * 1024 * 1024


@dataclass
class InstallResult:
    installed: list[str] = field(default_factory=list)
    with_scripts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(dest):
            raise SkillFormatError(f"压缩包包含不安全的路径：{member.filename}")
    zf.extractall(dest)


def install_zip(data: bytes, target_dir: Path, subpath: str | None = None, overwrite: bool = True) -> InstallResult:
    if len(data) > MAX_ZIP_BYTES:
        raise SkillFormatError("压缩包超过 50MB")
    result = InstallResult()
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _safe_extract(zf, tmp_path)
        search_root = tmp_path
        if subpath:
            matches = [p for p in tmp_path.glob(f"*/{subpath.strip('/')}") if p.exists()]
            search_root = matches[0] if matches else tmp_path
        candidates = sorted(search_root.rglob("SKILL.md"))
        if not candidates:
            result.errors.append("压缩包里没有找到 SKILL.md")
        for skill_md in candidates:
            try:
                skill = parse_skill_file(skill_md, "user-kaoyan", 550)
            except SkillFormatError as exc:
                result.errors.append(f"{skill_md.parent.name}: {exc}")
                continue
            dest = target_dir / skill.name
            if dest.exists():
                if not overwrite:
                    result.errors.append(f"{skill.name}: 已存在")
                    continue
                shutil.rmtree(dest)
            shutil.copytree(skill_md.parent, dest)
            result.installed.append(skill.name)
            if skill.has_scripts:
                result.with_scripts.append(skill.name)
    return result


def github_zip_urls(url: str) -> tuple[list[str], str | None]:
    match = GITHUB_RE.search(url)
    if not match:
        raise SkillFormatError("不是合法的 GitHub 地址，例如 https://github.com/用户/仓库")
    owner, repo, branch, sub = match.groups()
    repo = repo.removesuffix(".git")
    branches = [branch] if branch else ["main", "master"]
    return [f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{b}" for b in branches], sub


def install_github(url: str, target_dir: Path, client: httpx.Client | None = None) -> InstallResult:
    urls, sub = github_zip_urls(url)
    http = client or httpx.Client(timeout=60, follow_redirects=True)
    last_error = ""
    for zip_url in urls:
        try:
            resp = http.get(zip_url)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 200:
            return install_zip(resp.content, target_dir, sub)
        last_error = f"HTTP {resp.status_code}"
    raise SkillFormatError(f"下载失败：{last_error}")


def uninstall(name: str, target_dir: Path) -> bool:
    dest = (target_dir / name).resolve()
    if not dest.is_relative_to(target_dir.resolve()) or not dest.exists():
        return False
    shutil.rmtree(dest)
    return True
