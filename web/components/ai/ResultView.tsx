"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { deltaTone, num, signed, withUnit } from "@/lib/format";
import type { ExplainResult, QueryResult, ToolResult } from "@/lib/types";

const SERIES = ["#2b5d8a", "#d99a1e", "#2f8458", "#8a4fb0", "#c2412d", "#5a6573"];
const AXIS = { fontSize: 11, fill: "#5a6573" };

function Excluded({ items }: { items: QueryResult["excluded"] }) {
  if (!items.length) return null;
  return (
    <p className="text-xs text-muted break-words">
      已排除：{items.map((e) => `${e.reason} ${e.rows} 筆`).join("、")}
    </p>
  );
}

async function download(spec: Record<string, unknown>, setError: (s: string | null) => void) {
  setError(null);
  const res = await fetch("/api/export", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(spec) })
    .catch(() => null);
  if (!res || !res.ok) {
    setError(res ? ((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "匯出失敗" : "匯出失敗");
    return;
  }
  const name = decodeURIComponent(/filename\*=UTF-8''([^;]+)/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "plantsight.xlsx");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function QueryChart({ data }: { data: QueryResult }) {
  const metric = data.metrics[0];
  const dims = data.columns.filter((c) => !c.unit && !c.key.endsWith("_name")).map((c) => c.key);
  const group = dims.find((d) => d !== "period");
  if (data.grain !== "total" && dims.includes("period") && new Set(data.rows.map((r) => String(r.period))).size > 1) {
    // 趨勢：每個分組一條線（最多 6 條），沒有分組就一條
    const keys = group ? [...new Set(data.rows.map((r) => String(r[group])))].slice(0, 6) : [metric.key];
    const periods = [...new Set(data.rows.map((r) => String(r.period)))].sort();
    const points = periods.map((p) => {
      const point: Record<string, string | number | null> = { period: p };
      for (const r of data.rows.filter((x) => String(x.period) === p)) {
        point[group ? String(r[group]) : metric.key] = r[metric.key] as number | null;
      }
      return point;
    });
    return (
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#e3e8ee" vertical={false} />
          <XAxis dataKey="period" tick={AXIS} />
          <YAxis tick={AXIS} domain={["auto", "auto"]} />
          <Tooltip formatter={(v) => withUnit(v as number, metric.unit)} />
          {keys.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {keys.map((k, i) => <Line key={k} type="monotone" dataKey={k} stroke={SERIES[i % SERIES.length]} strokeWidth={2} dot={false} />)}
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (!group || data.rows.length < 2) return null;
  const compare = data.compare ? `${metric.key}__compare` : null;
  const groups = dims.filter((d) => d !== "period");
  const rows = data.rows.map((r) => ({ ...r, __label: groups.map((d) => String(r[d])).join(" · ") }));
  // 數值很接近時（良率 96 vs 97）從 0 起畫看不出差異；從資料自動縮放又會讓最短的條幾乎消失。
  // 下限往下留一個全距，百分比上限不超過 100。
  const values = data.rows.flatMap((r) => [r[metric.key], compare ? r[compare] : null]).filter((v): v is number => typeof v === "number");
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || Math.abs(hi) * 0.1 || 1;
  const floor = lo >= 0 && lo - span <= 0 ? 0 : lo - span;
  const ceil = metric.unit === "%" ? Math.min(100, hi + span * 0.1) : hi + span * 0.1;
  return (
    <ResponsiveContainer width="100%" height={Math.max(140, rows.length * 28)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 8 }}>
        <CartesianGrid stroke="#e3e8ee" horizontal={false} />
        <XAxis type="number" tick={AXIS} domain={[floor, ceil]} allowDataOverflow tickFormatter={(v: number) => num(v, span < 5 ? 1 : 0)} />
        <YAxis type="category" dataKey="__label" tick={AXIS} width={groups.length > 1 ? 120 : 72} />
        <Tooltip formatter={(v) => withUnit(v as number, metric.unit)} />
        {compare && <Bar dataKey={compare} name="比較" fill="#cfd6de" barSize={8} />}
        <Bar dataKey={metric.key} name={metric.label} fill="#2b5d8a" barSize={compare ? 8 : 14} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function QueryView({ data, spec }: { data: QueryResult; spec: Record<string, unknown> }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="space-y-2 rounded-md border border-rule bg-sheet p-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 text-xs text-muted break-words">
          {data.metrics.map((m) => m.label).join("、")}・{data.period}・{data.scope}{data.compare ? `・比較：${data.compare}` : ""}
        </p>
        <button onClick={() => void download(spec, setError)} className="shrink-0 text-xs text-river hover:underline">下載 Excel</button>
      </div>
      <QueryChart data={data} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>{data.columns.map((c) => <th key={c.key} className="px-2 py-1 font-normal whitespace-nowrap">{c.label}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {data.rows.map((r, i) => (
              <tr key={i}>
                {data.columns.map((c) => {
                  const v = r[c.key];
                  const metric = c.key.split("__")[0];
                  const cls = c.key.endsWith("__delta") ? deltaTone(metric, v as number) : "";
                  return (
                    <td key={c.key} className={`px-2 py-1 whitespace-nowrap ${c.unit ? "num text-right" : ""} ${cls}`}>
                      {c.key.endsWith("__delta") ? signed(v as number, undefined, c.decimals) : typeof v === "number" ? num(v, c.decimals) : (v ?? "—")}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.truncated && <p className="text-xs text-muted">只顯示前段結果，完整資料請下載 Excel。</p>}
      <p className="text-xs text-muted break-words">口徑：{data.metrics.map((m) => `${m.label}＝${m.basis}`).join("；")}</p>
      <Excluded items={data.excluded} />
      {error && <p role="alert" className="text-xs text-seal">{error}</p>}
    </section>
  );
}

function ExplainView({ data }: { data: ExplainResult }) {
  const ratio = data.mix_effect !== undefined;
  // 資料庫存 JSON 時鍵的順序不保證，用 by_key 找分組欄位；舊紀錄沒有 by_key 時找不是數值欄位的那一個
  const key = data.by_key ?? Object.keys(data.groups[0] ?? {}).find((k) => typeof data.groups[0][k] === "string") ?? "";
  const bars = data.groups.map((g) => ({ name: String(g[key]), value: (ratio ? g.total_effect : g.change) as number }));
  const unit = data.effect_unit ?? data.unit;
  return (
    <section className="space-y-2 rounded-md border border-rule bg-sheet p-3">
      <p className="text-xs text-muted break-words">{data.metric}變化拆解・依{data.by}・{data.base} → {data.current}</p>
      <p className="num text-base">
        {withUnit(data.before, data.unit)} → {withUnit(data.after, data.unit)}
        <span className={`ml-2 ${data.change < 0 ? "text-seal" : "text-good"}`}>{signed(data.change, data.unit)}</span>
      </p>
      {ratio && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className={`rounded border px-2 py-1.5 ${data.main_driver === "組合效應" ? "border-river bg-river-wash" : "border-rule"}`}>
            <p className="text-muted">組合效應（占比改變）</p>
            <p className="num text-sm">{signed(data.mix_effect, unit === "百分點" ? "%" : unit)}</p>
          </div>
          <div className={`rounded border px-2 py-1.5 ${data.main_driver === "比率效應" ? "border-river bg-river-wash" : "border-rule"}`}>
            <p className="text-muted">比率效應（各組自己變化）</p>
            <p className="num text-sm">{signed(data.rate_effect, unit === "百分點" ? "%" : unit)}</p>
          </div>
        </div>
      )}
      <ResponsiveContainer width="100%" height={Math.max(120, bars.length * 26)}>
        <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 8 }}>
          <XAxis type="number" tick={AXIS} />
          <YAxis type="category" dataKey="name" tick={AXIS} width={80} />
          <ReferenceLine x={0} stroke="#5a6573" />
          <Tooltip formatter={(v) => signed(v as number, unit === "百分點" ? "%" : unit)} />
          <Bar dataKey="value" name="對整體的影響" barSize={12}>
            {bars.map((b) => <Cell key={b.name} fill={b.value < 0 ? "#c2412d" : "#2f8458"} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="text-xs text-muted break-words">{data.method}。{data.basis}</p>
      <Excluded items={data.excluded} />
    </section>
  );
}

export function ResultView({ r }: { r: ToolResult }) {
  return r.tool === "query_metrics" ? <QueryView data={r.data} spec={r.args} /> : <ExplainView data={r.data} />;
}
