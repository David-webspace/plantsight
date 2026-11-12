import { Suspense } from "react";
import { ReviewQueue, type ReviewItem } from "@/components/ReviewQueue";
import { apiGet } from "@/lib/api";
import type { Me } from "@/lib/types";

type Report = { tables: Record<string, Record<string, number>>; review: Record<string, number> };
const TABLE = { work_orders: "工單", inspections: "檢驗", defects: "不良明細", shipments: "出貨", supplier_lots: "進料檢驗" } as const;

function sum(c: Record<string, number>, prefix: string): number {
  return Object.entries(c).filter(([k]) => k.startsWith(prefix)).reduce((s, [, v]) => s + v, 0);
}

async function DataQuality() {
  const [me, data] = await Promise.all([
    apiGet<Me>("/api/me"),
    apiGet<{ items: ReviewItem[]; report: { report: Report; created_at: string } | null }>("/api/review"),
  ]);
  const r = data.report?.report;
  return (
    <>
      {r && (
        <section className="mt-6">
          <h2 className="mb-2 text-lg font-medium">最近一次清理</h2>
          <div className="overflow-x-auto rounded-md border border-rule bg-sheet">
            <table className="w-full min-w-[36rem] text-sm">
              <thead className="bg-paper text-left text-muted">
                <tr>{["資料", "原始", "重複", "格式正規化", "寫入", "排除", "核准後修正"].map((h) => <th key={h} className="px-3 py-2 font-normal whitespace-nowrap">{h}</th>)}</tr>
              </thead>
              <tbody className="num divide-y divide-rule">
                {/* 資料庫存 JSON 時鍵的順序不保證，依固定順序列出 */}
                {Object.keys(TABLE).filter((t) => r.tables[t]).map((t) => [t, r.tables[t]] as const).map(([t, c]) => (
                  <tr key={t}>
                    <td className="px-3 py-2 font-sans">{TABLE[t as keyof typeof TABLE] ?? t}</td>
                    <td className="px-3 py-2">{c.raw ?? 0}</td>
                    <td className="px-3 py-2">{c.duplicate ?? 0}</td>
                    <td className="px-3 py-2">{c.normalized ?? 0}</td>
                    <td className="px-3 py-2">{c.loaded ?? 0}</td>
                    <td className="px-3 py-2">{sum(c, "excluded_")}</td>
                    <td className="px-3 py-2">{sum(c, "corrected_") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <ReviewQueue items={data.items} canDecide={me.can_review} />
    </>
  );
}

export default function DataPage() {
  return (
    <>
      <h1 className="text-2xl font-semibold">資料品質</h1>
      <p className="mt-1 text-sm text-muted">清理時無法自動決定的資料列在這裡。核准會套用系統建議並重新計算；駁回則維持排除。有問題的資料不會悄悄進入指標。</p>
      <Suspense fallback={<p className="mt-6 text-muted">讀取中…</p>}>
        <DataQuality />
      </Suspense>
    </>
  );
}
