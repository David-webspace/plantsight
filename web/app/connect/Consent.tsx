"use client";

import { useState } from "react";

export type Info = { client_name: string; redirect_host: string; scope: string; user: { name: string; role_label: string; scope: string } };

export function Consent({ rid, info }: { rid: string; info: Info }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(action: "approve" | "deny") {
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/oauth/requests/${encodeURIComponent(rid)}/${action}`, { method: "POST" }).catch(() => null);
    const body = res ? ((await res.json().catch(() => ({}))) as { redirect?: string; detail?: string }) : {};
    if (!res?.ok || !body.redirect) {
      setBusy(false);
      return setError(body.detail ?? "連不上伺服器，請稍後再試");
    }
    window.location.href = body.redirect;
  }

  return (
    <div className="mt-6 rounded-md border border-rule bg-sheet p-6">
      <h1 className="text-xl font-medium break-words">{info.client_name} 想要連接你的 plantsight</h1>
      <dl className="mt-4 space-y-3 text-sm">
        <div>
          <dt className="text-muted">以誰的身分</dt>
          <dd className="break-words">{info.user.name}（{info.user.role_label}・{info.user.scope}）</dd>
        </div>
        <div>
          <dt className="text-muted">可以做什麼</dt>
          <dd className="break-words">{info.scope}；不能核准資料修正，也不能代你做任何變更</dd>
        </div>
        <div>
          <dt className="text-muted">授權後會回到</dt>
          <dd className="num break-all">{info.redirect_host}</dd>
        </div>
      </dl>
      <p className="mt-4 rounded bg-glow-wash px-3 py-2 text-sm break-words">
        只有你自己剛在 AI 工具裡發起連線時才按同意。如果是別人傳連結給你，請按拒絕。之後可以在「連接 AI 工具」頁隨時中斷。
      </p>
      {error && <p role="alert" className="mt-3 text-sm text-seal break-words">{error}</p>}
      <div className="mt-6 flex gap-3">
        <button onClick={() => decide("approve")} disabled={busy}
          className="flex-1 rounded bg-river py-2.5 text-white hover:bg-river-deep disabled:opacity-50">同意連接</button>
        <button onClick={() => decide("deny")} disabled={busy}
          className="flex-1 rounded border border-rule py-2.5 hover:border-river disabled:opacity-50">拒絕</button>
      </div>
    </div>
  );
}
