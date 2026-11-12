"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export type ReviewItem = {
  id: string; source: string; kind: string; rows: number; description: string;
  suggestion: Record<string, string | number> | null; status: string; decided_by: string | null;
};

const KIND = { date_swapped: "日期對調", qty_overflow: "數量溢出", code_typo: "代碼誤植", code_unknown: "未知代碼" } as const;
const STATUS = { pending: "待確認", approved: "已核准", rejected: "已駁回" } as const;

export function ReviewQueue({ items, canDecide }: { items: ReviewItem[]; canDecide: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [show, setShow] = useState<"pending" | "all">("pending");
  const list = show === "pending" ? items.filter((i) => i.status === "pending") : items;

  async function decide(id: string, approve: boolean) {
    setBusy(id);
    setError(null);
    const res = await fetch(`/api/review/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ approve }),
    }).catch(() => null);
    setBusy(null);
    if (!res || !res.ok) {
      setError(res ? ((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "操作失敗" : "操作失敗");
      return;
    }
    router.refresh();
  }

  return (
    <section className="mt-8">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-medium">待確認清單</h2>
        <span className="text-sm text-muted">{items.filter((i) => i.status === "pending").length} 項待確認</span>
        <div className="ml-auto flex gap-1 text-sm">
          {(["pending", "all"] as const).map((v) => (
            <button key={v} onClick={() => setShow(v)} className={`rounded px-2 py-1 ${show === v ? "bg-river text-white" : "text-river hover:bg-river-wash"}`}>
              {v === "pending" ? "只看待確認" : "全部"}
            </button>
          ))}
        </div>
      </div>
      {!canDecide && <p className="mb-2 text-xs text-muted">只有品保工程師可以核准或駁回。</p>}
      {error && <p role="alert" className="mb-2 text-sm text-seal">{error}</p>}
      <ul className="space-y-2">
        {list.map((i) => (
          <li key={i.id} className="flex flex-col gap-2 rounded-md border border-rule bg-sheet p-3 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted">
                {KIND[i.kind as keyof typeof KIND] ?? i.kind}・{i.rows} 筆・{STATUS[i.status as keyof typeof STATUS]}
                {i.decided_by ? `（${i.decided_by}）` : ""}
              </p>
              <p className="text-sm break-words">{i.description}</p>
              {i.suggestion && (
                <p className="num text-xs break-words text-river">建議：{Object.entries(i.suggestion).map(([k, v]) => `${k} = ${v}`).join("、")}</p>
              )}
            </div>
            {canDecide && i.status === "pending" && (
              <div className="flex shrink-0 gap-2">
                <button disabled={busy !== null} onClick={() => void decide(i.id, false)}
                  className="rounded border border-rule px-3 py-1.5 text-sm hover:border-seal hover:text-seal disabled:opacity-50">駁回</button>
                <button disabled={busy !== null || !i.suggestion} onClick={() => void decide(i.id, true)}
                  title={i.suggestion ? "套用建議並重新計算" : "沒有可套用的建議，只能駁回"}
                  className="rounded bg-river px-3 py-1.5 text-sm text-white hover:bg-river-deep disabled:opacity-40">
                  {busy === i.id ? "重新計算中…" : "核准"}
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
