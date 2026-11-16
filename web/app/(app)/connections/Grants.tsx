"use client";

import { useState } from "react";

export type Grant = { id: number; client_name: string; created_at: string; last_used_at: string | null };

const fmt = (iso: string) => new Date(iso).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });

export function Grants({ grants: initial }: { grants: Grant[] }) {
  const [grants, setGrants] = useState(initial);
  const [error, setError] = useState<string | null>(null);

  async function revoke(id: number) {
    setError(null);
    const res = await fetch(`/api/oauth/grants/${id}/revoke`, { method: "POST" }).catch(() => null);
    if (!res?.ok) return setError("中斷失敗，請重新整理後再試");
    setGrants((g) => g.filter((x) => x.id !== id));
  }

  return (
    <section className="mt-6">
      <h2 className="mb-2 text-lg font-medium">已連接</h2>
      {error && <p role="alert" className="mb-2 text-sm text-seal">{error}</p>}
      {grants.length === 0 ? (
        <p className="text-sm text-muted">目前沒有連接任何 AI 工具。</p>
      ) : (
        <ul className="divide-y divide-rule rounded-md border border-rule bg-sheet">
          {grants.map((g) => (
            <li key={g.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-medium break-words">{g.client_name}</p>
                <p className="text-xs text-muted">
                  連接於 {fmt(g.created_at)}・{g.last_used_at ? `最後使用 ${fmt(g.last_used_at)}` : "尚未使用"}
                </p>
              </div>
              <button onClick={() => revoke(g.id)} className="rounded border border-rule px-3 py-1.5 text-sm hover:border-seal hover:text-seal">
                中斷連線
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
