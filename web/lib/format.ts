// 數值已由後端依指標定義四捨五入；前端只補足小數位（97 → 97.0）並加千分位，不再重新計算
export function num(v: number | string | null | undefined, decimals?: number): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  const s = decimals === undefined ? String(v) : v.toFixed(decimals);
  const [i, d] = s.split(".");
  return Number(i).toLocaleString("en-US") + (d ? `.${d}` : "");
}

export function withUnit(v: number | null | undefined, unit?: string, decimals?: number): string {
  if (v === null || v === undefined) return "—";
  if (!unit) return num(v, decimals);
  return unit === "%" ? `${num(v, decimals)}%` : `${num(v, decimals)} ${unit}`;
}

// 指標「越高越好」還是「越低越好」：決定差異的顏色
const LOWER_IS_BETTER = new Set(["dppm", "scrap_qty", "scrap_cost", "defect_qty", "incoming_reject_rate"]);

export function deltaTone(metric: string, delta: number | null | undefined): string {
  if (!delta) return "text-muted";
  const good = LOWER_IS_BETTER.has(metric) ? delta < 0 : delta > 0;
  return good ? "text-good" : "text-seal";
}

export function signed(v: number | null | undefined, unit?: string, decimals?: number): string {
  if (v === null || v === undefined) return "—";
  const u = unit === "%" ? " 個百分點" : unit ? ` ${unit}` : "";
  return `${v > 0 ? "+" : ""}${num(v, decimals)}${u}`;
}
