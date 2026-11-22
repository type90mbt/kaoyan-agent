"""工具注册表与执行器。

每个工具声明：名字、说明、参数 JSON Schema、权限级别、要打快照的路径。执行器统一做：
权限检查 → （需要时）请求审批 → 打快照 → 执行 → 写操作日志。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import Database, new_id, now_ms
from .guard import ApprovalQueue, Guard, Level, PermissionDenied
from .snapshot import SnapshotStore

MAX_RESULT_CHARS = 12000


@dataclass
class ToolContext:
    guard: Guard
    session_id: str
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    level: Level
    fn: Callable[[ToolContext, dict[str, Any]], str]
    summarize: Callable[[dict[str, Any]], str] | None = None
    snapshot_paths: Callable[[dict[str, Any]], list[str]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def params(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


class ToolRegistry:
    def __init__(self, db: Database, guard: Guard, approvals: ApprovalQueue, snapshots: SnapshotStore) -> None:
        self.db = db
        self.guard = guard
        self.approvals = approvals
        self.snapshots = snapshots
        self._tools: dict[str, Tool] = {}
        self._dynamic: list[Callable[[], list[Tool]]] = []

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def add_provider(self, provider: Callable[[], list[Tool]]) -> None:
        """动态工具来源（例如 MCP 服务器），每次列出工具时调用。"""
        self._dynamic.append(provider)

    def all(self) -> dict[str, Tool]:
        tools = dict(self._tools)
        for provider in self._dynamic:
            try:
                for t in provider():
                    tools[t.name] = t
            except Exception:  # noqa: BLE001 - 外部工具来源失败不能影响内置工具
                continue
        return tools

    def schemas(self, include_computer: bool = True) -> list[dict[str, Any]]:
        tools = list(self.all().values())
        if not include_computer:
            tools = [t for t in tools if t.name in INTERNAL_TOOLS]
        return [t.schema() for t in tools]

    def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> Iterator[tuple[str, Any]]:
        """生成事件：("approval", 审批信息) → ("result", {"ok", "output", "snapshot_id"})。"""
        tool = self.all().get(name)
        log_id = new_id()
        if tool is None:
            yield ("result", {"ok": False, "output": f"没有名为 {name} 的工具"})
            return
        summary = tool.summarize(args) if tool.summarize else f"{name} {json.dumps(args, ensure_ascii=False)[:300]}"
        status, output, snapshot_id = "ok", "", None
        try:
            if self.guard.needs_approval(tool.level):
                approval = self.approvals.request(ctx.session_id, name, tool.level, args, summary)
                yield ("approval", approval.public())
                if not self.approvals.wait(approval):
                    status, output = "denied", "用户拒绝了这次操作（或审批超时）。请换一种方式，或者询问用户。"
                    raise PermissionDenied(output)
            if tool.snapshot_paths:
                paths = [self.guard.resolve(p) for p in tool.snapshot_paths(args)]
                snapshot_id = self.snapshots.take(paths, summary)
            output = tool.fn(ctx, args)
        except PermissionDenied as exc:
            status = "denied"
            output = str(exc)
        except Exception as exc:  # noqa: BLE001 - 工具错误要原样反馈给模型
            status = "error"
            output = f"{type(exc).__name__}: {exc}"
        if len(output) > MAX_RESULT_CHARS:
            output = output[:MAX_RESULT_CHARS] + f"\n……（已截断，共 {len(output)} 字符）"
        self.db.execute(
            "INSERT INTO tool_logs (id, session_id, tool, args, level, status, result, snapshot_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log_id,
                ctx.session_id,
                name,
                json.dumps(args, ensure_ascii=False),
                tool.level.value,
                status,
                output[:4000],
                snapshot_id,
                now_ms(),
            ),
        )
        yield ("result", {"ok": status == "ok", "status": status, "output": output, "snapshot_id": snapshot_id})


INTERNAL_TOOLS = {"load_skill", "search_materials"}


def rel(path: Path, guard: Guard) -> str:
    for ws in guard.workspaces():
        if path.is_relative_to(ws):
            return str(path.relative_to(ws)) or "."
    return str(path)
