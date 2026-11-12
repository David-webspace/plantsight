import { redirect } from "next/navigation";
import { Suspense } from "react";
import { AskChips } from "@/components/ai/AiButtons";
import { Sparkline } from "@/components/Sparkline";
import { apiGet } from "@/lib/api";
import { deltaTone, signed, withUnit } from "@/lib/format";
import type { Me, Overview } from "@/lib/types";

async function Dashboard() {
  const me = await apiGet<Me>("/api/me");
  if (me.is_admin) redirect("/admin");
  const ov = await apiGet<Overview>("/api/overview");
  return (
    <>
      <p className="text-sm text-muted">{ov.period} 的關鍵指標・{me.role_label}・{ov.scope}</p>
      <h1 className="mt-1 text-2xl font-semibold">{me.name}，上個月的生產概況</h1>
      <section className="mt-6 grid gap-px overflow-hidden rounded-md border border-rule bg-rule sm:grid-cols-2 lg:grid-cols-3">
        {ov.cards.map((c) => (
          <div key={c.key} className="min-w-0 bg-sheet p-4">
            <p className="text-sm text-muted">{c.label}</p>
            <p className="num mt-1 text-2xl">{withUnit(c.value, c.unit === "新台幣元" ? "元" : c.unit, c.decimals)}</p>
            <p className={`num mt-0.5 text-xs ${deltaTone(c.key, c.delta)}`}>較前月 {signed(c.delta, c.unit === "新台幣元" ? "元" : c.unit, c.decimals)}</p>
            <Sparkline points={ov.trends[c.key] ?? []} unit={c.unit} />
            <p className="text-[11px] break-words text-muted">{c.basis}</p>
          </div>
        ))}
      </section>
      {ov.cards.length < 6 && (
        <p className="mt-3 text-xs text-muted">依你的角色與範圍，部分指標（例如準交率、報廢金額）不在權限內，不顯示。</p>
      )}
      <section className="mt-8 rounded-md border border-rule bg-sheet p-4">
        <h2 className="text-lg font-medium">問數據助理</h2>
        <p className="mt-1 mb-3 text-sm text-muted">點一下就會開啟助理並送出問題；也可以隨時按 Ctrl／⌘ + J 開關。</p>
        <AskChips prompts={["上個月良率最差的是哪條線？", "近三個月良率為什麼下降？", "上個月各廠的不良主要是哪一類？"]} />
      </section>
    </>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={<p className="mt-6 text-muted">讀取指標…</p>}>
      <Dashboard />
    </Suspense>
  );
}
