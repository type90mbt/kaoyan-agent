export type SyncRecord = {
  id: string;
  updated_at: number;
  deleted: boolean;
  [key: string]: unknown;
};

export type Lesson = SyncRecord & {
  course_id: string;
  title: string;
  sort?: number;
  status?: string;
};

export type MaterialMeta = {
  id: string;
  course_id: string | null;
  lesson_id: string | null;
  filename: string;
  status: string;
  pages: number;
};

export type Course = SyncRecord & {
  subject_id: string;
  name: string;
  teacher?: string;
  term?: string;
  description?: string;
  lessons: Lesson[];
  materials: MaterialMeta[];
};

export type Subject = SyncRecord & {
  name: string;
  kind?: string;
  skill_prefix?: string;
  color?: string;
  courses: Course[];
};

export type ChatMessage = {
  id: string;
  role: string;
  content: string;
  created_at?: number;
};

export type Session = {
  id: string;
  title?: string;
  course_id?: string | null;
  lesson_id?: string | null;
  status?: string;
};

export type MaterialDetail = MaterialMeta & {
  page_texts: { page: number; text: string }[];
  preview_pdf?: string | null;
  kind?: string;
};

export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "skills"; items: string[] }
  | { type: "notice"; message: string }
  | { type: "error"; message: string }
  | { type: "usage"; usage?: unknown; model?: string }
  | { type: "summary"; summary: unknown }
  | { type: "done" }
  | { type: string; [key: string]: unknown };

export class EngineClient {
  constructor(
    public baseUrl: string,
    public token: string,
  ) {}

  private headers(json = true): HeadersInit {
    const h: Record<string, string> = { Authorization: `Bearer ${this.token}` };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async health(): Promise<boolean> {
    const res = await fetch(`${this.baseUrl}/api/health`);
    return res.ok;
  }

  async settings(): Promise<{ keys: Record<string, boolean>; routes_available: Record<string, boolean> }> {
    const res = await fetch(`${this.baseUrl}/api/settings`, { headers: this.headers(false) });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ keys: Record<string, boolean>; routes_available: Record<string, boolean> }>;
  }

  async tree(): Promise<Subject[]> {
    const res = await fetch(`${this.baseUrl}/api/tree`, { headers: this.headers(false) });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<Subject[]>;
  }

  async upsert(collection: string, body: Record<string, unknown>): Promise<SyncRecord> {
    const res = await fetch(`${this.baseUrl}/api/data/${collection}`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<SyncRecord>;
  }

  async uploadMaterial(file: File, courseId: string, lessonId?: string): Promise<MaterialMeta> {
    const form = new FormData();
    form.append("file", file);
    form.append("course_id", courseId);
    if (lessonId) form.append("lesson_id", lessonId);
    const res = await fetch(`${this.baseUrl}/api/materials`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}` },
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<MaterialMeta>;
  }

  async getMaterial(id: string): Promise<MaterialDetail> {
    const res = await fetch(`${this.baseUrl}/api/materials/${id}`, { headers: this.headers(false) });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<MaterialDetail>;
  }

  pageImageUrl(materialId: string, page: number): string {
    return `${this.baseUrl}/api/materials/${materialId}/pages/${page}.png?token=${encodeURIComponent(this.token)}`;
  }

  async createSession(input: { course_id?: string; lesson_id?: string; title?: string }): Promise<Session> {
    const res = await fetch(`${this.baseUrl}/api/sessions`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ ...input, origin: "desktop" }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<Session>;
  }

  async listMessages(sessionId: string): Promise<ChatMessage[]> {
    const res = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/messages`, {
      headers: this.headers(false),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<ChatMessage[]>;
  }

  async summarize(sessionId: string): Promise<unknown> {
    const res = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/summarize`, {
      method: "POST",
      headers: this.headers(),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async streamChat(
    sessionId: string,
    text: string,
    onEvent: (ev: StreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/chat`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(await res.text());
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const line = chunk
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload) as StreamEvent);
        } catch {
          // ignore malformed event
        }
      }
    }
  }
}
