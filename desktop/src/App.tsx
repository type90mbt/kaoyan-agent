import { useCallback, useEffect, useMemo, useState } from "react";
import {
  EngineClient,
  type Course,
  type Lesson,
  type MaterialDetail,
  type Subject,
} from "./api/client";
import { ChatPane, type UiMessage } from "./components/ChatPane";
import { CourseTree } from "./components/CourseTree";
import { MaterialPane } from "./components/MaterialPane";

type ConnState = {
  baseUrl: string;
  token: string | null;
  healthy: boolean;
  message: string;
};

function newId(): string {
  return crypto.randomUUID();
}

export default function App() {
  const [conn, setConn] = useState<ConnState>({
    baseUrl: "http://127.0.0.1:8765",
    token: null,
    healthy: false,
    message: "正在连接本地引擎…",
  });
  const [tree, setTree] = useState<Subject[]>([]);
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<string | null>(null);
  const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(null);
  const [material, setMaterial] = useState<MaterialDetail | null>(null);
  const [page, setPage] = useState(1);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deepseekReady, setDeepseekReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const client = useMemo(() => {
    if (!conn.token) return null;
    return new EngineClient(conn.baseUrl, conn.token);
  }, [conn.baseUrl, conn.token]);

  const selectedCourse = useMemo(() => {
    for (const s of tree) {
      const c = s.courses.find((x) => x.id === selectedCourseId);
      if (c) return c;
    }
    return null;
  }, [tree, selectedCourseId]);

  const refreshTree = useCallback(async (c: EngineClient) => {
    const data = await c.tree();
    setTree(data);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (window.kaoyan) {
          const ensured = await window.kaoyan.ensureEngine();
          const boot = await window.kaoyan.getBootstrap();
          if (cancelled) return;
          setConn({
            baseUrl: boot.baseUrl,
            token: ensured.token ?? boot.token,
            healthy: ensured.ok || boot.healthy,
            message: ensured.message,
          });
        } else {
          // 浏览器开发：尝试读 Vite 环境变量，否则仅健康检查
          const token = import.meta.env.VITE_KAOYAN_TOKEN as string | undefined;
          const baseUrl = (import.meta.env.VITE_KAOYAN_BASE as string | undefined) || "http://127.0.0.1:8765";
          const healthy = await fetch(`${baseUrl}/api/health`).then((r) => r.ok).catch(() => false);
          if (cancelled) return;
          setConn({
            baseUrl,
            token: token || null,
            healthy,
            message: healthy
              ? token
                ? "已连接引擎（浏览器开发模式）"
                : "引擎在线，但缺少令牌。请用 Electron 打开，或设置 VITE_KAOYAN_TOKEN"
              : "引擎未启动。请先运行 engine：uv run kaoyan-engine",
          });
        }
      } catch (err) {
        if (!cancelled) {
          setConn((prev) => ({
            ...prev,
            healthy: false,
            message: err instanceof Error ? err.message : String(err),
          }));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!client || !conn.healthy) return;
    let cancelled = false;
    (async () => {
      try {
        const settings = await client.settings();
        if (cancelled) return;
        setDeepseekReady(Boolean(settings.keys.deepseek));
        await refreshTree(client);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, conn.healthy, refreshTree]);

  useEffect(() => {
    if (!client || !selectedMaterialId) {
      setMaterial(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const detail = await client.getMaterial(selectedMaterialId);
        if (cancelled) return;
        setMaterial(detail);
        setPage(1);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, selectedMaterialId]);

  async function ensureSession(course: Course, lessonId?: string | null): Promise<string> {
    if (!client) throw new Error("引擎未连接");
    if (sessionId) return sessionId;
    const session = await client.createSession({
      course_id: course.id,
      lesson_id: lessonId || undefined,
      title: `${course.name} · 课堂`,
    });
    setSessionId(session.id);
    return session.id;
  }

  async function onSelectCourse(course: Course) {
    setSelectedCourseId(course.id);
    setSelectedLessonId(null);
    setSelectedMaterialId(course.materials[0]?.id ?? null);
    setSessionId(null);
    setMessages([]);
    if (client) {
      try {
        const session = await client.createSession({ course_id: course.id, title: `${course.name} · 课堂` });
        setSessionId(session.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  async function onSelectLesson(course: Course, lesson: Lesson) {
    setSelectedCourseId(course.id);
    setSelectedLessonId(lesson.id);
    setSessionId(null);
    setMessages([]);
    if (client) {
      try {
        const session = await client.createSession({
          course_id: course.id,
          lesson_id: lesson.id,
          title: `${course.name} · ${lesson.title}`,
        });
        setSessionId(session.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  function onSelectMaterial(course: Course, materialId: string) {
    setSelectedCourseId(course.id);
    setSelectedMaterialId(materialId);
  }

  async function onCreateSubject() {
    if (!client) return;
    const name = window.prompt("学科名称", "计算机");
    if (!name) return;
    setBusy(true);
    try {
      await client.upsert("subjects", { name, kind: "course", sort: tree.length });
      await refreshTree(client);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateCourse(subjectId: string) {
    if (!client) return;
    const name = window.prompt("课程名称", "大数据基础平台");
    if (!name) return;
    setBusy(true);
    try {
      await client.upsert("courses", { subject_id: subjectId, name, sort: 0 });
      await refreshTree(client);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateLesson(courseId: string) {
    if (!client) return;
    const title = window.prompt("课时标题", "第 1 讲");
    if (!title) return;
    setBusy(true);
    try {
      await client.upsert("lessons", { course_id: courseId, title, sort: 0, status: "todo" });
      await refreshTree(client);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onUpload(courseId: string, file: File) {
    if (!client) return;
    setBusy(true);
    setError(null);
    try {
      const m = await client.uploadMaterial(file, courseId, selectedLessonId || undefined);
      await refreshTree(client);
      setSelectedCourseId(courseId);
      setSelectedMaterialId(m.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSend(text: string) {
    if (!client || !selectedCourse) return;
    setError(null);
    setStreaming(true);
    const userMsg: UiMessage = { id: newId(), role: "user", content: text };
    const assistantId = newId();
    setMessages((prev) => [...prev, userMsg, { id: assistantId, role: "assistant", content: "" }]);
    try {
      const sid = await ensureSession(selectedCourse, selectedLessonId);
      await client.streamChat(sid, text, (ev) => {
        if (ev.type === "delta" && typeof ev.text === "string") {
          const piece = ev.text;
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + piece } : m)),
          );
        } else if (ev.type === "notice" && typeof ev.message === "string") {
          const notice = ev.message;
          setMessages((prev) => [...prev, { id: newId(), role: "system", content: notice }]);
        } else if (ev.type === "error" && typeof ev.message === "string") {
          setError(ev.message);
        } else if (ev.type === "skills" && Array.isArray(ev.items)) {
          const items = ev.items.map(String);
          setMessages((prev) => [
            ...prev,
            { id: newId(), role: "system", content: `已加载 skill：${items.join(", ")}` },
          ]);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStreaming(false);
    }
  }

  async function onSummarize() {
    if (!client || !sessionId) return;
    setBusy(true);
    try {
      const result = await client.summarize(sessionId);
      const content =
        result && typeof result === "object" && "content" in result
          ? String((result as { content: unknown }).content)
          : JSON.stringify(result, null, 2);
      setMessages((prev) => [...prev, { id: newId(), role: "system", content: `本节总结\n${content}` }]);
      setSessionId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const chatStatus = !conn.healthy
    ? conn.message
    : !deepseekReady
      ? "引擎在线，但未检测到 DeepSeek Key"
      : selectedCourse
        ? `当前课程：${selectedCourse.name}`
        : "请选择一门课程开始提问";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-ink-200/70 bg-white/80 px-4 py-2 backdrop-blur">
        <div className="flex items-baseline gap-3">
          <h1 className="text-base font-semibold tracking-wide text-ink-900">Kaoyan Agent</h1>
          <span className="text-xs text-ink-700/60">本地优先 · 考研学习助手</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className={conn.healthy ? "text-accent" : "text-red-600"}>
            {conn.healthy ? "引擎已连接" : "引擎离线"}
          </span>
          <span className="text-ink-700/50">{conn.baseUrl}</span>
        </div>
      </header>

      {error && (
        <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
          <button type="button" className="ml-3 underline" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_360px]">
        <CourseTree
          tree={tree}
          selectedCourseId={selectedCourseId}
          selectedLessonId={selectedLessonId}
          selectedMaterialId={selectedMaterialId}
          busy={busy}
          onSelectCourse={onSelectCourse}
          onSelectLesson={onSelectLesson}
          onSelectMaterial={onSelectMaterial}
          onCreateSubject={onCreateSubject}
          onCreateCourse={onCreateCourse}
          onCreateLesson={onCreateLesson}
          onUpload={onUpload}
        />
        <MaterialPane
          client={client}
          material={material}
          page={page}
          onPageChange={setPage}
          emptyHint={
            selectedCourse
              ? "这门课还没有可预览的课件。可在左侧上传 PDF / PPT。"
              : "从左侧选择课程，或新建学科开始。"
          }
        />
        <ChatPane
          messages={messages}
          streaming={streaming}
          status={chatStatus}
          disabled={!client || !conn.healthy || !selectedCourse || !deepseekReady}
          onSend={onSend}
          onSummarize={onSummarize}
        />
      </div>
    </div>
  );
}
