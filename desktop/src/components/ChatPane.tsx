import { useEffect, useRef, useState } from "react";

export type UiMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
};

type Props = {
  messages: UiMessage[];
  streaming: boolean;
  status: string;
  disabled?: boolean;
  onSend: (text: string) => void;
  onSummarize: () => void;
};

export function ChatPane(props: Props) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [props.messages, props.streaming]);

  function submit() {
    const text = draft.trim();
    if (!text || props.streaming || props.disabled) return;
    props.onSend(text);
    setDraft("");
  }

  return (
    <aside className="flex h-full flex-col border-l border-ink-200/80 bg-white/75 backdrop-blur">
      <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-ink-800">AI 教学</div>
          <div className="text-xs text-ink-700/60">{props.status}</div>
        </div>
        <button
          type="button"
          className="rounded-md border border-ink-200 px-2.5 py-1 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-40"
          disabled={props.disabled || props.streaming}
          onClick={props.onSummarize}
        >
          本节总结
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-auto px-3 py-3">
        {props.messages.length === 0 ? (
          <div className="rounded-lg border border-dashed border-ink-200 p-4 text-sm text-ink-700/70">
            先选一门课，再提问。例如：「用苏格拉底方式讲这一页」「出三道练习题」。
          </div>
        ) : (
          props.messages.map((m) => (
            <div
              key={m.id}
              className={`max-w-[95%] rounded-2xl px-3 py-2 text-sm leading-6 ${
                m.role === "user"
                  ? "ml-auto bg-accent text-white"
                  : m.role === "system"
                    ? "mx-auto bg-ink-100 text-ink-700/80"
                    : "mr-auto bg-ink-50 text-ink-800"
              }`}
            >
              <div className="whitespace-pre-wrap">{m.content}</div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-ink-100 p-3">
        <div className="rounded-xl border border-ink-200 bg-white p-2 shadow-sm">
          <textarea
            className="h-20 w-full resize-none bg-transparent px-1 text-sm outline-none"
            placeholder={props.disabled ? "引擎未就绪或未选择课程" : "输入问题，Enter 发送，Shift+Enter 换行"}
            value={draft}
            disabled={props.disabled || props.streaming}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <div className="flex items-center justify-between px-1 pt-1">
            <span className="text-[11px] text-ink-700/50">
              {props.streaming ? "正在生成…" : "DeepSeek / 本地引擎"}
            </span>
            <button
              type="button"
              className="rounded-md bg-accent px-3 py-1 text-xs text-white hover:bg-accent/90 disabled:opacity-40"
              disabled={props.disabled || props.streaming || !draft.trim()}
              onClick={submit}
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
