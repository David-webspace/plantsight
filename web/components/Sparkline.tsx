"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { withUnit } from "@/lib/format";

export function Sparkline({ points, unit }: { points: { period: string; value: number }[]; unit: string }) {
  return (
    <ResponsiveContainer width="100%" height={48}>
      <LineChart data={points} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
        <YAxis hide domain={["auto", "auto"]} />
        <Tooltip formatter={(v) => withUnit(v as number, unit)} labelFormatter={(_, p) => String(p?.[0]?.payload?.period ?? "")} />
        <Line type="monotone" dataKey="value" name="當月" stroke="#2b5d8a" strokeWidth={1.75} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
