"""引擎配置：数据目录、模型提供方、任务路由、工作区与权限策略。

配置保存在 `$KAOYAN_HOME/config.json`（默认 `~/.kaoyan/config.json`）。API Key 不放这里，见 `secrets.py`。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def kaoyan_home() -> Path:
    home = Path(os.environ.get("KAOYAN_HOME", Path.home() / ".kaoyan"))
    home.mkdir(parents=True, exist_ok=True)
    return home


class ProviderConfig(BaseModel):
    id: str
    name: str
    base_url: str
    key_env: str = ""


class RouteConfig(BaseModel):
    provider: str
    model: str


class McpServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    read_only: bool = False


def _default_providers() -> list[ProviderConfig]:
    return [
        ProviderConfig(id="deepseek", name="DeepSeek", base_url="https://api.deepseek.com/v1", key_env="DEEPSEEK_API_KEY"),
        ProviderConfig(
            id="siliconflow", name="硅基流动", base_url="https://api.siliconflow.cn/v1", key_env="SILICONFLOW_API_KEY"
        ),
        ProviderConfig(
            id="dashscope",
            name="阿里云百炼（通义）",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            key_env="DASHSCOPE_API_KEY",
        ),
        ProviderConfig(
            id="gemini",
            name="Google Gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            key_env="GEMINI_API_KEY",
        ),
        ProviderConfig(id="mock", name="离线模拟（无需 Key，仅用于体验和测试）", base_url="mock://"),
    ]


def _default_routes() -> dict[str, RouteConfig]:
    return {
        "chat": RouteConfig(provider="deepseek", model="deepseek-chat"),
        "reason": RouteConfig(provider="deepseek", model="deepseek-reasoner"),
        "summary": RouteConfig(provider="deepseek", model="deepseek-chat"),
        "vision": RouteConfig(provider="dashscope", model="qwen-vl-plus"),
        "embed": RouteConfig(provider="siliconflow", model="BAAI/bge-m3"),
    }


class Settings(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=_default_providers)
    routes: dict[str, RouteConfig] = Field(default_factory=_default_routes)
    workspaces: list[str] = Field(default_factory=list)
    write_policy: Literal["ask", "auto"] = "ask"
    tutor_mode: Literal["tutor", "direct"] = "tutor"
    vision_ocr: bool = True
    custom_skill_dirs: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    sync_server_url: str = ""
    lan_pair_token: str = Field(default_factory=lambda: secrets.token_urlsafe(24))
    compact_threshold_chars: int = 24000
    onboarded: bool = False

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)


class SettingsStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or kaoyan_home()
        self.path = self.home / "config.json"
        self.settings = self._load()

    def _load(self) -> Settings:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            settings = Settings.model_validate(data)
        else:
            settings = Settings()
        known = {p.id for p in settings.providers}
        for p in _default_providers():
            if p.id not in known:
                settings.providers.append(p)
        self._save(settings)
        return settings

    def _save(self, settings: Settings) -> None:
        self.path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")

    def update(self, patch: dict) -> Settings:
        merged = self.settings.model_dump()
        merged.update(patch)
        self.settings = Settings.model_validate(merged)
        self._save(self.settings)
        return self.settings

    @property
    def snapshots_dir(self) -> Path:
        d = self.home / "snapshots"
        d.mkdir(exist_ok=True)
        return d

    @property
    def materials_dir(self) -> Path:
        d = self.home / "materials"
        d.mkdir(exist_ok=True)
        return d

    @property
    def user_skills_dir(self) -> Path:
        d = self.home / "skills"
        d.mkdir(exist_ok=True)
        return d
