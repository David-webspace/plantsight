"use client";

import { useEffect, useRef, useState } from "react";
import { useChat } from "@/components/ai/ChatProvider";
import { AiReply } from "@/components/ai/AiReply";
import { Sparkle } from "@/components/ai/Sparkle";

const CHIP = "rounded-full border border-rule bg-sheet px-3 py-1.5 text-sm hover:border-river hover:text-river";

// 依角色給起手問題：看得到成本的人才建議問成本、看得到出貨的人才建議問準交率
function suggestionsFor(role: string): string[] {
  const base = ["上個月良率最差的是哪條線？", "近三個月良率為什麼下降？"];
  if (role === "line_lead") return [...base.slice(0, 1), "上個月我這條線的不良主要是哪一類？", "上個月我這條線的良率和目標比如何？"].slice(0, 3);
  if (role === "qa") return [...base, "哪個供應商的進料不良率最高？"];
  if (role === "finance") return ["上個月各廠報廢金額是多少？", "報廢金額比前一個月增加多少？", ...base.slice(0, 1)];
  return [...base, "上個月各廠準交率和目標比如何？"];
}

export function AiPanel() {
  const { me, open, setOpen, turns, busy, send, conversations, conversationId, loadConversation, newConversation, available, floating, setFloating } = useChat();
  const [input, setInput] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  // 大括號不可省：新版瀏覽器的 scrollIntoView 會回傳 Promise，被 React 當成 cleanup 呼叫會整頁崩潰
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns, busy, open]);
  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);

  if (!open || !available || !me) return null;

  function submit() {
    if (busy || !input.trim()) return;
    const text = input;
    setInput("");
    void send(text);
  }

  return (
    <>
      {!floating && <div onClick={() => setOpen(false)} className="fixed inset-0 z-30 hidden bg-ink/20 md:block lg:hidden" aria-hidden="true" />}
      <aside aria-label="數據助理"
        className={`fixed inset-0 z-40 flex flex-col bg-paper ${floating
          ? "md:inset-auto md:right-6 md:bottom-6 md:h-[min(42rem,calc(100dvh-3rem))] md:w-[28rem] md:overflow-hidden md:rounded-xl md:border md:border-rule md:shadow-2xl"
          : "md:left-auto md:w-[30rem] md:border-l md:border-rule md:shadow-xl lg:sticky lg:inset-auto lg:top-0 lg:z-auto lg:h-dvh lg:shrink-0 lg:shadow-none"}`}>
        <header className="flex items-center gap-2 border-b border-rule bg-sheet px-4 py-3">
          <Sparkle className="size-5 text-river" />
          <h2 className="shrink-0 text-lg font-medium whitespace-nowrap">數據助理</h2>
          <select aria-label="切換對話" value={conversationId ?? ""} disabled={busy}
            onChange={(e) => (e.target.value ? void loadConversation(Number(e.target.value)) : newConversation())}
            className="ml-auto max-w-40 min-w-0 truncate rounded border border-rule bg-sheet px-2 py-1 text-xs text-muted">
            <option value="">新對話</option>
            {conversations.map((c) => <option key={c.id} value={c.id}>{c.title ?? `對話 ${c.id}`}</option>)}
          </select>
          <button onClick={() => setFloating(!floating)} aria-label={floating ? "固定在右側" : "改成浮動視窗"} title={floating ? "固定在右側" : "改成浮動視窗"}
            className="hidden rounded px-1.5 py-1 text-muted hover:bg-paper hover:text-ink md:block">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" className="size-4" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              {floating ? <path d="M15 4v16" /> : <rect x="12" y="12" width="6" height="5" rx="1" />}
            </svg>
          </button>
          <button onClick={newConversation} disabled={busy} title="開新對話" className="rounded px-2 py-1 text-sm text-river hover:bg-river-wash disabled:opacity-50">＋</button>
          <button onClick={() => setOpen(false)} aria-label="關閉數據助理" title="關閉（Ctrl／⌘ + J）" className="rounded px-2 py-1 text-muted hover:bg-paper hover:text-ink">✕</button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {turns.length === 0 && (
            <div className="space-y-3">
              <p className="text-sm text-muted">
                {me.name}，你的資料範圍是「{me.scope}」。良率、直通率、不良率、準交率、進料品質都可以問，也可以問「為什麼變了」。
                指標由系統依定義計算，回答附口徑與被排除的資料筆數。
              </p>
              <div className="flex flex-wrap gap-2">
                {suggestionsFor(me.role).map((s) => (
                  <button key={s} onClick={() => void send(s)} className={CHIP}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) =>
            t.role === "user" ? (
              <p key={i} className="ml-auto w-fit max-w-[85%] rounded-md bg-river-wash px-3 py-2 text-sm break-words">{t.text}</p>
            ) : t.role === "error" ? (
              <p key={i} role="alert" className="border-l-2 border-seal pl-3 text-sm text-seal">{t.text}</p>
            ) : (
              <div key={i} className="text-sm"><AiReply t={t} /></div>
            ),
          )}
          {busy && <p className="animate-pulse pl-3 text-sm text-muted motion-reduce:animate-none">查詢中…</p>}
          <div ref={end} />
        </div>

        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="border-t border-rule bg-sheet p-3">
          <div className="flex gap-2">
            <label htmlFor="ai-ask" className="sr-only">輸入問題</label>
            <textarea id="ai-ask" ref={field} rows={2} value={input} maxLength={2000} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="例如：上個月良率最差的是哪條線？"
              className="min-w-0 flex-1 resize-none rounded-md border border-rule bg-paper px-3 py-2 text-sm focus:border-river focus:outline-none" />
            <button disabled={busy || !input.trim()} className="shrink-0 self-end rounded-md bg-river px-4 py-2 text-sm text-white hover:bg-river-deep disabled:opacity-40">送出</button>
          </div>
        </form>
      </aside>
    </>
  );
}
