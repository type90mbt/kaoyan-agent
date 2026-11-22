"""MCP 桥接：把用户配置的 MCP 服务器（stdio 方式）的工具接入 harness。

工具名统一为 `mcp__<服务器名>__<工具名>`。外部工具默认按 exec 级别处理（每次审批），
服务器配置了 read_only=true 时按 read 级别。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any

from ..config import McpServerConfig, Settings
from .guard import Level
from .registry import Tool, ToolContext


class _ServerConn:
    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self.session: Any = None
        self.tools: list[Any] = []
        self.error: str = ""
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()

    async def run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.cfg.command, args=self.cfg.args, env=self.cfg.env or None)
        try:
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                self.session = session
                self.tools = list(listed.tools)
                self.ready.set()
                await self.stop.wait()
        except Exception as exc:  # noqa: BLE001 - 外部进程的任何失败都只记录
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()


class McpManager:
    def __init__(self, settings_getter: Callable[[], Settings]) -> None:
        self._settings = settings_getter
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._conns: dict[str, _ServerConn] = {}
        self._signature: tuple = ()

    def _sync(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def refresh(self) -> None:
        servers = [s for s in self._settings().mcp_servers if s.enabled]
        signature = tuple(s.model_dump_json() for s in servers)
        if signature == self._signature:
            return
        self.shutdown()
        self._signature = signature
        for cfg in servers:
            conn = _ServerConn(cfg)
            self._conns[cfg.name] = conn
            asyncio.run_coroutine_threadsafe(conn.run(), self._loop)
            with contextlib.suppress(Exception):
                self._sync(asyncio.wait_for(conn.ready.wait(), 30), timeout=35)

    def status(self) -> list[dict[str, Any]]:
        return [
            {"name": n, "tools": [t.name for t in c.tools], "error": c.error, "connected": c.session is not None}
            for n, c in self._conns.items()
        ]

    def shutdown(self) -> None:
        for conn in self._conns.values():
            self._loop.call_soon_threadsafe(conn.stop.set)
        self._conns = {}
        self._signature = ()

    def _call(self, server: str, tool: str, args: dict[str, Any]) -> str:
        conn = self._conns.get(server)
        if not conn or conn.session is None:
            return f"MCP 服务器 {server} 未连接：{conn.error if conn else '未配置'}"
        result = self._sync(conn.session.call_tool(tool, args), timeout=300)
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        prefix = "（工具报告错误）" if getattr(result, "isError", False) or getattr(result, "is_error", False) else ""
        return prefix + "\n".join(parts)

    def tools(self) -> list[Tool]:
        self.refresh()
        out: list[Tool] = []
        for name, conn in self._conns.items():
            level = Level.READ if conn.cfg.read_only else Level.EXEC
            for t in conn.tools:
                schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {"type": "object"}

                def fn(ctx: ToolContext, a: dict[str, Any], _s=name, _t=t.name) -> str:
                    return self._call(_s, _t, a)

                out.append(
                    Tool(
                        name=f"mcp__{name}__{t.name}",
                        description=f"[MCP:{name}] {t.description or ''}",
                        parameters=schema,
                        level=level,
                        fn=fn,
                        summarize=lambda a, _s=name, _t=t.name: f"调用 MCP 工具 {_s}/{_t}",
                    )
                )
        return out
