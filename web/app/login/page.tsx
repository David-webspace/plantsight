"use client";

import { useState } from "react";

// 示範帳號：讓評估者不用查文件就能切換角色體驗權限差異（密碼見部署時的 SEED_DEMO_PASSWORD）
const DEMO = [
  { u: "gm", who: "總經理", role: "全公司" },
  { u: "tw.manager", who: "廠長", role: "台灣桃園廠" },
  { u: "tw.smt1.lead", who: "產線主管", role: "TW-SMT1" },
  { u: "pwr.head", who: "事業群主管", role: "電源事業群" },
  { u: "qa.engineer", who: "品保工程師", role: "全公司品質資料" },
  { u: "finance.analyst", who: "財務", role: "全公司，含成本" },
  { u: "sysadmin", who: "系統管理員", role: "只看稽核" },
];

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
    }).catch(() => null);
    setBusy(false);
    if (!res) return setError("連不上伺服器，請確認服務已啟動");
    if (!res.ok) return setError(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "登入失敗");
    const me = (await res.json()) as { is_admin: boolean };
    // 同登出：整頁載入，確保不沿用前一位使用者留在瀏覽器記憶體裡的狀態
    // next 只接受站內路徑，否則登入頁會變成把人導去任意網站的跳板（//evil、/\evil 在瀏覽器裡都會被當成外部網址）
    const next = new URLSearchParams(window.location.search).get("next");
    const safe = next && /^\/(?![/\\])/.test(next) ? next : null;
    window.location.replace(safe ?? (me.is_admin ? "/admin" : "/"));
  }

  return (
    <main className="mx-auto grid min-h-dvh max-w-4xl items-center gap-10 px-4 py-10 md:grid-cols-[1fr_20rem]">
      <section className="min-w-0">
        <p className="text-5xl font-semibold tracking-tight text-river">plantsight</p>
        <h1 className="mt-3 text-2xl">Arcwell Electronics 廠務數據助理</h1>
        <p className="mt-4 max-w-md text-muted">上個月良率最差的是哪條線？為什麼掉了？——直接問。指標由系統依定義計算，看得到哪些廠區、產線與成本，依你的登入身分決定。</p>
        <div className="mt-8">
          <p className="mb-2 text-sm text-muted">示範帳號（點一下帶入）</p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {DEMO.map((d) => (
              <li key={d.u}>
                <button type="button" onClick={() => setUsername(d.u)}
                  className={`w-full rounded-md border px-3 py-2 text-left hover:border-river ${username === d.u ? "border-river bg-river-wash" : "border-rule bg-sheet"}`}>
                  <span className="block font-medium">{d.who}</span>
                  <span className="block text-xs text-muted">{d.role}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>
      <form onSubmit={submit} className="rounded-md border border-rule bg-sheet p-6">
        <h2 className="mb-4 text-xl font-medium">登入</h2>
        <label className="block text-sm text-muted" htmlFor="u">帳號</label>
        <input id="u" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required
          className="mt-1 mb-4 w-full rounded border border-rule px-3 py-2 focus:border-river focus:outline-none" />
        <label className="block text-sm text-muted" htmlFor="p">密碼</label>
        <input id="p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required
          className="mt-1 w-full rounded border border-rule px-3 py-2 focus:border-river focus:outline-none" />
        <p className="mt-1.5 text-xs text-muted break-words">示範帳號密碼：專案資料夾 .env 的 SEED_DEMO_PASSWORD。</p>
        {error && <p role="alert" className="mt-3 text-sm text-seal">{error}</p>}
        <button disabled={busy} className="mt-6 w-full rounded bg-river py-2.5 text-white hover:bg-river-deep disabled:opacity-50">
          {busy ? "登入中…" : "登入"}
        </button>
      </form>
    </main>
  );
}
