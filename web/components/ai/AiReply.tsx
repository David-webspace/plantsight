import type { Turn } from "@/components/ai/ChatProvider";
import { ResultView } from "@/components/ai/ResultView";
import { Markdown } from "@/components/Markdown";

const TOOL_LABEL: Record<string, string> = { list_metrics: "指標目錄", query_metrics: "指標查詢", explain_change: "變化拆解" };

export function AiReply({ t }: { t: Extract<Turn, { role: "assistant" }> }) {
  return (
    <article className="min-w-0 space-y-3 border-l-2 border-river pl-3">
      <Markdown text={t.text} />
      {t.results.map((r, i) => <ResultView key={i} r={r} />)}
      <p className="text-[11px] text-muted">
        {[...new Set(t.tools.map((x) => TOOL_LABEL[x.name] ?? x.name))].join("、") || "未查詢資料"}
        {t.latencyMs !== undefined && <> · <span className="num">{(t.latencyMs / 1000).toFixed(1)}s</span></>}
        {!t.groundingOk && " · 部分數字未能核對"}
      </p>
    </article>
  );
}
