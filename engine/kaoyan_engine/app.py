"""FastAPI 应用：桌面端、手机 App（局域网）都通过这些接口访问引擎。

安全：除 /api/health 外，所有接口都要求令牌。
- 本机桌面端：读取 `~/.kaoyan/local_token` 文件里的令牌（浏览器里的恶意网页读不到这个文件，避免跨站调用）。
- 手机：扫码配对得到 `lan_pair_token`。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import __version__
from .config import SettingsStore
from .db import COLLECTIONS, Database, UnknownCollection
from .harness.agent import Agent
from .harness.summary import summarize_session
from .ingest.service import IngestService
from .keystore import KeyStore
from .learning import timetable as tt
from .learning.cards import due_cards, new_card, review
from .learning.planner import Planner
from .llm.gateway import Gateway, LLMError
from .rag.retriever import Retriever
from .skills.installer import SkillFormatError, install_github, install_zip, uninstall
from .skills.loader import SkillRegistry, default_roots
from .sync import apply_push, lan_addresses, pull_payload, sync_with_server
from .tools.builtin import register_builtin
from .tools.guard import ApprovalQueue, Guard
from .tools.mcp_bridge import McpManager
from .tools.registry import ToolRegistry
from .tools.snapshot import SnapshotStore

BUNDLED_SKILLS_CANDIDATES = [
    Path(__file__).resolve().parent / "bundled_skills",
    Path(__file__).resolve().parents[2] / ".agents" / "skills",
]


def bundled_skills_dir() -> Path | None:
    return next((p for p in BUNDLED_SKILLS_CANDIDATES if p.is_dir()), None)


@dataclass
class AppState:
    store: SettingsStore
    db: Database
    keys: KeyStore
    gateway: Gateway
    retriever: Retriever
    ingest: IngestService
    skills: SkillRegistry
    guard: Guard
    approvals: ApprovalQueue
    snapshots: SnapshotStore
    tools: ToolRegistry
    agent: Agent
    planner: Planner
    mcp: McpManager | None
    local_token: str


def build_state(home: Path | None = None, transport: httpx.BaseTransport | None = None, enable_mcp: bool = True) -> AppState:
    store = SettingsStore(home)
    db = Database(store.home / "kaoyan.db")
    keys = KeyStore({p.id: p.key_env for p in store.settings.providers if p.key_env})
    gateway = Gateway(lambda: store.settings, keys.get, transport=transport)
    retriever = Retriever(db, gateway)
    ingest = IngestService(db, gateway, retriever, store.materials_dir, lambda: store.settings.vision_ocr)

    def roots():
        ws = store.settings.workspaces
        return default_roots(
            Path(ws[0]) if ws else None, store.settings.custom_skill_dirs, store.user_skills_dir, bundled_skills_dir()
        )

    skills = SkillRegistry(roots)
    guard = Guard(lambda: store.settings)
    approvals = ApprovalQueue()
    snapshots = SnapshotStore(store.snapshots_dir)
    tools = ToolRegistry(db, guard, approvals, snapshots)
    register_builtin(tools)
    mcp = McpManager(lambda: store.settings) if enable_mcp else None
    if mcp:
        tools.add_provider(mcp.tools)
    agent = Agent(db, gateway, retriever, skills, tools, lambda: store.settings)
    planner = Planner(db, gateway, skills)
    token_file = store.home / "local_token"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    local_token = token_file.read_text(encoding="utf-8").strip()
    return AppState(
        store, db, keys, gateway, retriever, ingest, skills, guard, approvals, snapshots, tools, agent, planner, mcp, local_token
    )


def _sse(events) -> StreamingResponse:
    def gen():
        for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def create_app(state: AppState | None = None) -> FastAPI:
    st = state or build_state()
    app = FastAPI(title="Kaoyan Agent Engine", version=__version__)
    app.state.kaoyan = st
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in ("/api/health", "/docs", "/openapi.json"):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header else request.query_params.get("token", "")
        if token not in (st.local_token, st.store.settings.lan_pair_token):
            return JSONResponse({"detail": "未授权：缺少或错误的令牌"}, status_code=401)
        return await call_next(request)

    # ---------- 基础 ----------
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/settings")
    def get_settings():
        s = st.store.settings.model_dump()
        s["keys"] = {p.id: st.keys.has(p.id) for p in st.store.settings.providers}
        s["routes_available"] = {t: st.gateway.available(t) for t in st.store.settings.routes}
        return s

    @app.put("/api/settings")
    def put_settings(patch: dict = Body(...)):
        patch.pop("keys", None)
        patch.pop("routes_available", None)
        try:
            st.store.update(patch)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_settings()

    @app.put("/api/keys/{provider_id}")
    def put_key(provider_id: str, body: dict = Body(...)):
        if not st.store.settings.provider(provider_id):
            raise HTTPException(404, "未知的模型提供方")
        persisted = st.keys.set(provider_id, str(body.get("key", "")).strip())
        return {"ok": True, "persisted": persisted}

    @app.post("/api/keys/{provider_id}/test")
    def test_key(provider_id: str):
        for task, route in st.store.settings.routes.items():
            if route.provider == provider_id and task in ("chat", "summary", "reason"):
                try:
                    r = st.gateway.chat([{"role": "user", "content": "回复：连接成功"}], task=task)
                    return {"ok": True, "reply": r.content[:100], "model": r.model}
                except LLMError as exc:
                    return {"ok": False, "error": str(exc)}
        try:
            res = st.gateway.embed(["测试"])
            return {"ok": bool(res), "reply": "向量接口可用" if res else "未配置"}
        except LLMError as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- 通用数据（可同步集合） ----------
    @app.get("/api/data/{collection}")
    def list_data(collection: str, request: Request):
        try:
            where = {k: v for k, v in request.query_params.items() if k not in ("token",)}
            return st.db.list(collection, where=where, order="sort" if "sort" in COLLECTIONS.get(collection, []) else "updated_at")
        except UnknownCollection as exc:
            raise HTTPException(404, f"未知集合 {collection}") from exc

    @app.post("/api/data/{collection}")
    def upsert_data(collection: str, record: dict = Body(...)):
        try:
            return st.db.upsert(collection, record)
        except UnknownCollection as exc:
            raise HTTPException(404, f"未知集合 {collection}") from exc

    @app.delete("/api/data/{collection}/{record_id}")
    def delete_data(collection: str, record_id: str):
        try:
            st.db.soft_delete(collection, record_id)
        except UnknownCollection as exc:
            raise HTTPException(404, f"未知集合 {collection}") from exc
        return {"ok": True}

    @app.get("/api/tree")
    def tree():
        subjects = st.db.list("subjects", order="sort")
        courses = st.db.list("courses", order="sort")
        lessons = st.db.list("lessons", order="sort")
        materials = st.db.query("SELECT id, course_id, lesson_id, filename, status, pages FROM materials ORDER BY created_at")
        out = []
        for s in subjects:
            s_courses = []
            for c in (c for c in courses if c.get("subject_id") == s["id"]):
                s_courses.append(
                    {
                        **c,
                        "lessons": [l for l in lessons if l.get("course_id") == c["id"]],  # noqa: E741
                        "materials": [m for m in materials if m["course_id"] == c["id"]],
                    }
                )
            out.append({**s, "courses": s_courses})
        return out

    # ---------- 课件 ----------
    @app.post("/api/materials")
    async def upload_material(file: UploadFile = File(...), course_id: str = Form(""), lesson_id: str = Form("")):
        data = await file.read()
        return st.ingest.add(file.filename or "material", data, course_id or None, lesson_id or None)

    @app.get("/api/materials")
    def list_materials(course_id: str = ""):
        if course_id:
            return st.db.query("SELECT * FROM materials WHERE course_id = ? ORDER BY created_at", (course_id,))
        return st.db.query("SELECT * FROM materials ORDER BY created_at")

    @app.get("/api/materials/{material_id}")
    def get_material(material_id: str):
        m = st.ingest.get(material_id)
        if not m:
            raise HTTPException(404, "课件不存在")
        return {**m, "page_texts": st.ingest.pages(material_id)}

    @app.get("/api/materials/{material_id}/file")
    def material_file(material_id: str, preview: bool = True):
        m = st.ingest.get(material_id)
        if not m:
            raise HTTPException(404, "课件不存在")
        path = m["preview_pdf"] if preview and m.get("preview_pdf") else m["path"]
        return FileResponse(path, filename=Path(path).name, content_disposition_type="inline")

    @app.get("/api/materials/{material_id}/pages/{page}.png")
    def material_page_png(material_id: str, page: int):
        import fitz

        m = st.ingest.get(material_id)
        pdf = (m or {}).get("preview_pdf") or ((m or {}).get("path") if (m or {}).get("kind") == "pdf" else None)
        if not pdf:
            raise HTTPException(404, "这个课件没有可渲染的 PDF 预览（安装 LibreOffice 后重新上传 PPT 即可）")
        with fitz.open(pdf) as doc:
            if not 1 <= page <= len(doc):
                raise HTTPException(404, "页码超出范围")
            png = doc[page - 1].get_pixmap(dpi=120).tobytes("png")
        return Response(png, media_type="image/png")

    @app.delete("/api/materials/{material_id}")
    def delete_material(material_id: str):
        st.ingest.delete(material_id)
        return {"ok": True}

    @app.post("/api/search")
    def search(body: dict = Body(...)):
        hits = st.retriever.search(str(body.get("query", "")), course_id=body.get("course_id"), k=int(body.get("k", 8)))
        return [h.__dict__ for h in hits]

    # ---------- 对话 ----------
    @app.post("/api/sessions")
    def create_session(body: dict = Body(default={})):
        return st.agent.create_session(
            lesson_id=body.get("lesson_id"), course_id=body.get("course_id"), title=body.get("title"), origin=body.get("origin", "desktop")
        )

    @app.get("/api/sessions")
    def list_sessions(lesson_id: str = "", course_id: str = ""):
        if lesson_id:
            return st.db.query("SELECT * FROM sessions WHERE lesson_id = ? ORDER BY created_at DESC", (lesson_id,))
        if course_id:
            return st.db.query("SELECT * FROM sessions WHERE course_id = ? ORDER BY created_at DESC", (course_id,))
        return st.db.query("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 100")

    @app.get("/api/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        rows = st.db.query(
            "SELECT id, role, content, tool_calls, tool_call_id, name, archived, created_at FROM messages "
            "WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
        for r in rows:
            r["tool_calls"] = json.loads(r["tool_calls"]) if r["tool_calls"] else None
        return rows

    @app.post("/api/sessions/{session_id}/chat")
    def chat(session_id: str, body: dict = Body(...)):
        return _sse(st.agent.run(session_id, str(body.get("text", "")), mode=str(body.get("mode", "chat"))))

    @app.post("/api/sessions/{session_id}/summarize")
    def summarize(session_id: str):
        try:
            record = summarize_session(st.db, st.gateway, st.skills, session_id, close=True)
        except KeyError as exc:
            raise HTTPException(404, "会话不存在") from exc
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        return record or {"empty": True}

    @app.post("/api/dispatch")
    def dispatch(body: dict = Body(...)):
        """手机端把复杂任务派发到电脑上执行：创建会话并在后台运行，审批在电脑上完成。"""
        import threading

        session = st.agent.create_session(course_id=body.get("course_id"), title="手机派发：" + str(body.get("text", ""))[:20], origin="mobile")

        def run():
            for _ in st.agent.run(session["id"], str(body.get("text", ""))):
                pass

        threading.Thread(target=run, daemon=True).start()
        return session

    # ---------- 审批 / 日志 / 快照 ----------
    @app.get("/api/approvals")
    def approvals():
        return st.approvals.pending()

    @app.post("/api/approvals/{approval_id}")
    def decide(approval_id: str, body: dict = Body(...)):
        if not st.approvals.decide(approval_id, bool(body.get("approve"))):
            raise HTTPException(404, "审批已失效")
        return {"ok": True}

    @app.get("/api/tool-logs")
    def tool_logs(session_id: str = "", limit: int = 100):
        if session_id:
            return st.db.query("SELECT * FROM tool_logs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?", (session_id, limit))
        return st.db.query("SELECT * FROM tool_logs ORDER BY created_at DESC LIMIT ?", (limit,))

    @app.get("/api/snapshots")
    def list_snapshots():
        return st.snapshots.list()[:100]

    @app.post("/api/snapshots/{snapshot_id}/undo")
    def undo_snapshot(snapshot_id: str):
        try:
            return {"restored": st.snapshots.undo(snapshot_id)}
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/tools")
    def list_tools():
        return [
            {"name": t.name, "description": t.description, "level": t.level.value} for t in st.tools.all().values()
        ]

    @app.get("/api/mcp")
    def mcp_status():
        return st.mcp.status() if st.mcp else []

    # ---------- 技能 ----------
    @app.get("/api/skills")
    def list_skills():
        items = [s.summary() for s in st.skills.list()]
        return {
            "skills": items,
            "warnings": st.skills.warnings,
            "roots": [{"rank": r.rank, "source": r.source, "path": str(r.path)} for r in st.skills.roots()],
        }

    @app.get("/api/skills/{name}")
    def get_skill(name: str):
        skill = st.skills.get(name)
        if not skill:
            raise HTTPException(404, "技能不存在")
        return {**skill.summary(), "body": skill.body()}

    @app.post("/api/skills/install")
    def install_skill(body: dict = Body(...)):
        try:
            res = install_github(str(body.get("url", "")), st.store.user_skills_dir)
        except SkillFormatError as exc:
            raise HTTPException(400, str(exc)) from exc
        return res.__dict__

    @app.post("/api/skills/install-zip")
    async def install_skill_zip(file: UploadFile = File(...)):
        try:
            res = install_zip(await file.read(), st.store.user_skills_dir)
        except (SkillFormatError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return res.__dict__

    @app.delete("/api/skills/{name}")
    def delete_skill(name: str):
        if not uninstall(name, st.store.user_skills_dir):
            raise HTTPException(400, "只能卸载安装在 ~/.kaoyan/skills 中的技能")
        return {"ok": True}

    # ---------- 学习 ----------
    @app.get("/api/today")
    def today(day: str = ""):
        return st.planner.today(date.fromisoformat(day) if day else None)

    @app.post("/api/tasks/generate")
    def generate_tasks(body: dict = Body(default={})):
        day = date.fromisoformat(body["date"]) if body.get("date") else None
        return st.planner.generate(day, use_ai=bool(body.get("use_ai", True)))

    @app.get("/api/stats")
    def stats():
        return st.planner.stats()

    @app.post("/api/mentor")
    def mentor():
        return st.planner.mentor()

    @app.get("/api/cards/due")
    def cards_due(course_id: str = ""):
        return due_cards(st.db, course_id=course_id or None)

    @app.post("/api/cards")
    def create_card(body: dict = Body(...)):
        return new_card(
            st.db, body["front"], body["back"], course_id=body.get("course_id"), lesson_id=body.get("lesson_id"),
            source=body.get("source", "mistake"),
        )  # fmt: skip

    @app.post("/api/cards/{card_id}/review")
    def review_card(card_id: str, body: dict = Body(...)):
        rating = int(body.get("rating", 3))
        if rating not in (1, 2, 3, 4):
            raise HTTPException(400, "评分必须是 1~4")
        try:
            return review(st.db, card_id, rating)
        except KeyError as exc:
            raise HTTPException(404, "卡片不存在") from exc

    @app.post("/api/review/final")
    def final_review(body: dict = Body(...)):
        ws = st.store.settings.workspaces
        out_dir = Path(ws[0]) / "期末复习" if ws else None
        try:
            return st.planner.final_review(body["course_id"], st.retriever, out_dir)
        except KeyError as exc:
            raise HTTPException(404, "课程不存在") from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/timetable/import-ics")
    async def import_ics(file: UploadFile = File(...)):
        text = (await file.read()).decode("utf-8", errors="ignore")
        return {"imported": tt.import_ics(st.db, text)}

    @app.post("/api/timetable/import-image")
    async def import_timetable_image(file: UploadFile = File(...)):
        import base64

        data = base64.b64encode(await file.read()).decode()
        try:
            raw = st.gateway.vision(tt.VISION_TIMETABLE_PROMPT, data)
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"imported": tt.import_vision_json(st.db, raw), "raw": raw[:2000]}

    @app.get("/api/timetable/day")
    def timetable_day(day: str = ""):
        return tt.classes_on(st.db, date.fromisoformat(day) if day else date.today())

    # ---------- 同步 ----------
    @app.get("/sync/pull")
    def sync_pull(cursor: int = 0):
        return pull_payload(st.db, cursor)

    @app.post("/sync/push")
    def sync_push(body: dict = Body(...)):
        return {"applied": apply_push(st.db, body.get("changes") or {}), "cursor": st.db.current_seq()}

    @app.get("/api/sync/pair")
    def pair_info(request: Request):
        port = request.url.port or 8765
        urls = [f"http://{ip}:{port}" for ip in lan_addresses()]
        payload = {"kind": "kaoyan-pair", "urls": urls, "token": st.store.settings.lan_pair_token}
        return {**payload, "qr": json.dumps(payload, ensure_ascii=False)}

    @app.post("/api/sync/reset-pair-token")
    def reset_pair_token():
        st.store.update({"lan_pair_token": secrets.token_urlsafe(24)})
        return {"ok": True}

    @app.post("/api/sync/run")
    def run_sync():
        url = st.store.settings.sync_server_url
        token = st.keys.get("sync-server")
        if not url or not token:
            raise HTTPException(400, "请先在 设置 → 同步 中填写同步服务器地址和令牌")
        try:
            return sync_with_server(st.db, url, token)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"同步失败：{exc}") from exc

    @app.put("/api/sync/server-token")
    def put_sync_token(body: dict = Body(...)):
        return {"persisted": st.keys.set("sync-server", str(body.get("token", "")).strip())}

    @app.get("/api/collections")
    def collections() -> dict[str, Any]:
        return {"collections": COLLECTIONS}

    return app
