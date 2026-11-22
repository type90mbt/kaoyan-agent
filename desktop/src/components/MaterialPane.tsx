import type { EngineClient, MaterialDetail } from "../api/client";

type Props = {
  client: EngineClient | null;
  material: MaterialDetail | null;
  page: number;
  onPageChange: (page: number) => void;
  emptyHint: string;
};

export function MaterialPane(props: Props) {
  const { material, client, page } = props;
  const total = material?.pages || material?.page_texts?.length || 0;
  const pageText = material?.page_texts?.find((p) => p.page === page)?.text ?? "";

  return (
    <section className="flex h-full min-w-0 flex-col bg-white/50">
      <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-ink-800">
            {material ? material.filename : "课件预览"}
          </div>
          <div className="text-xs text-ink-700/60">
            {material ? `状态 ${material.status}` : "选中左侧课件后在这里阅读"}
          </div>
        </div>
        {total > 0 && (
          <div className="flex items-center gap-2 text-sm">
            <button
              type="button"
              className="rounded border border-ink-200 px-2 py-0.5 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => props.onPageChange(page - 1)}
            >
              上一页
            </button>
            <span className="tabular-nums text-ink-700">
              {page} / {total}
            </span>
            <button
              type="button"
              className="rounded border border-ink-200 px-2 py-0.5 disabled:opacity-40"
              disabled={page >= total}
              onClick={() => props.onPageChange(page + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {!material || !client ? (
          <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-ink-200 bg-white/60 p-8 text-center text-sm text-ink-700/70">
            {props.emptyHint}
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            <div className="overflow-hidden rounded-xl border border-ink-100 bg-white shadow-sm">
              <img
                src={client.pageImageUrl(material.id, page)}
                alt={`第 ${page} 页`}
                className="block w-full"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
              />
            </div>
            {pageText && (
              <pre className="whitespace-pre-wrap rounded-xl border border-ink-100 bg-white/80 p-4 text-sm leading-6 text-ink-800">
                {pageText}
              </pre>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
