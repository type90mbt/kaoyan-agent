"""同步协议（桌面引擎、手机 App、同步服务器三方通用）。

- 每条记录：id / updated_at（修改时间，毫秒）/ deleted，合并规则是 updated_at 大的为准。
- 拉取游标用"接收方的本地序号 seq"，而不是 updated_at，这样离线修改的旧时间戳记录也不会漏掉。

接口：
  GET  /sync/pull?cursor=N   → {"cursor": 新游标, "changes": {集合: [记录...]}}
  POST /sync/push {"changes": {...}} → {"applied": 写入条数, "cursor": 当前游标}
"""

from __future__ import annotations

import socket
from typing import Any

import httpx

from .db import COLLECTIONS, Database

MAX_BATCH = 2000


def pull_payload(db: Database, cursor: int) -> dict[str, Any]:
    rows = [(int(r.get("seq") or 0), name, r) for name, items in db.changes_since(cursor).items() for r in items]
    rows.sort(key=lambda x: x[0])
    more = len(rows) > MAX_BATCH
    rows = rows[:MAX_BATCH]
    trimmed: dict[str, list[dict[str, Any]]] = {}
    for _, name, r in rows:
        trimmed.setdefault(name, []).append(r)
    new_cursor = rows[-1][0] if more else max(cursor, db.current_seq())
    return {"cursor": new_cursor, "changes": trimmed, "more": more}


def apply_push(db: Database, changes: dict[str, list[dict[str, Any]]]) -> int:
    applied = 0
    for name, rows in (changes or {}).items():
        if name in COLLECTIONS and isinstance(rows, list):
            applied += db.merge_remote(name, rows)
    return applied


def lan_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def sync_with_server(db: Database, base_url: str, token: str, client: httpx.Client | None = None) -> dict[str, int]:
    """把本机作为客户端，与用户自部署的同步服务器双向同步。"""
    http = client or httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {token}"}
    base = base_url.rstrip("/")

    pulled = 0
    cursor = int(db.get_state("server_pull_cursor", "0"))
    while True:
        resp = http.get(f"{base}/sync/pull", params={"cursor": cursor}, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        pulled += apply_push(db, data.get("changes") or {})
        cursor = int(data.get("cursor") or cursor)
        db.set_state("server_pull_cursor", str(cursor))
        if not data.get("more"):
            break

    pushed = 0
    push_cursor = int(db.get_state("server_push_cursor", "0"))
    while True:
        payload = pull_payload(db, push_cursor)
        if not payload["changes"]:
            break
        resp = http.post(f"{base}/sync/push", json={"changes": payload["changes"]}, headers=headers)
        resp.raise_for_status()
        pushed += sum(len(v) for v in payload["changes"].values())
        push_cursor = payload["cursor"]
        db.set_state("server_push_cursor", str(push_cursor))
        if not payload["more"]:
            break
    return {"pulled": pulled, "pushed": pushed}
