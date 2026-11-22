"""权限层：所有电脑操作工具都必须经过这里。

- 工作区限制：只能访问用户在设置里添加的文件夹，解析符号链接后再判断，防止 `..` 和链接绕过。
- 三级权限：read 自动放行；write 按设置（ask/auto）；exec（执行命令、联网、删除、外部 MCP 工具）每次都要审批。
- 审批：工具调用会挂起，等待界面上用户点"允许/拒绝"，超时视为拒绝。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import new_id


class Level(StrEnum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"


class PermissionDenied(PermissionError):
    pass


class Guard:
    def __init__(self, settings_getter: Callable[[], Settings]) -> None:
        self._settings = settings_getter

    def workspaces(self) -> list[Path]:
        return [Path(w).expanduser().resolve() for w in self._settings().workspaces if w]

    def resolve(self, raw: str, must_exist: bool = False) -> Path:
        spaces = self.workspaces()
        if not spaces:
            raise PermissionDenied("还没有设置工作区。请在 设置 → 电脑操作 中添加一个允许 AI 读写的文件夹")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = spaces[0] / p
        resolved = p.resolve()
        if not any(resolved == ws or resolved.is_relative_to(ws) for ws in spaces):
            raise PermissionDenied(f"路径 {resolved} 不在工作区内，已拒绝")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"文件不存在：{resolved}")
        return resolved

    def needs_approval(self, level: Level) -> bool:
        if level == Level.READ:
            return False
        if level == Level.WRITE:
            return self._settings().write_policy != "auto"
        return True


@dataclass
class Approval:
    id: str
    session_id: str
    tool: str
    level: str
    args: dict[str, Any]
    summary: str
    created_at: float = field(default_factory=time.time)
    decision: bool | None = None
    event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "tool": self.tool,
            "level": self.level,
            "args": self.args,
            "summary": self.summary,
            "created_at": self.created_at,
        }


class ApprovalQueue:
    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout
        self._pending: dict[str, Approval] = {}
        self._lock = threading.Lock()

    def request(self, session_id: str, tool: str, level: Level, args: dict[str, Any], summary: str) -> Approval:
        approval = Approval(new_id(), session_id, tool, level.value, args, summary)
        with self._lock:
            self._pending[approval.id] = approval
        return approval

    def wait(self, approval: Approval) -> bool:
        approval.event.wait(self.timeout)
        with self._lock:
            self._pending.pop(approval.id, None)
        return bool(approval.decision)

    def decide(self, approval_id: str, approve: bool) -> bool:
        with self._lock:
            approval = self._pending.get(approval_id)
        if not approval:
            return False
        approval.decision = approve
        approval.event.set()
        return True

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.public() for a in self._pending.values()]
