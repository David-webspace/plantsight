"use client";

import { createContext, use, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatReply, ConversationSummary, HistoryMessage, Me, ToolResult, ToolUse } from "@/lib/types";

export type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; results: ToolResult[]; tools: ToolUse[]; latencyMs?: number; groundingOk: boolean }
  | { role: "error"; text: string };

type ChatState = {
  me: Me | null;
  open: boolean;
  setOpen: (open: boolean) => void;
  turns: Turn[];
  busy: boolean;
  conversationId: number | null;
  conversations: ConversationSummary[];
  available: boolean;
  floating: boolean;
  setFloating: (value: boolean) => void;
  send: (text: string) => Promise<void>;
  ask: (text: string) => void;
  newConversation: () => void;
  loadConversation: (id: number) => Promise<void>;
};

const ChatContext = createContext<ChatState | null>(null);
// 只記「面板開或關、浮動或固定」這種介面偏好；對話內容含成本等機密，不放在瀏覽器儲存空間
const OPEN_KEY = "plantsight.ai-panel-open";
const FLOAT_KEY = "plantsight.ai-panel-floating";

function remember(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // 無痕模式或封鎖儲存空間時只是不記住偏好
  }
}

function toTurns(messages: HistoryMessage[]): Turn[] {
  return messages.map((m) =>
    m.role === "user"
      ? { role: "user", text: m.text }
      : { role: "assistant", text: m.text, results: m.meta?.results ?? [], tools: m.meta?.tools ?? [],
          groundingOk: m.meta?.grounding_ok ?? true },
  );
}

async function getJson<T>(path: string): Promise<T | null> {
  const res = await fetch(path).catch(() => null);
  return res && res.ok ? ((await res.json()) as T) : null;
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpenState] = useState(false);
  const [floating, setFloatingState] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const loaded = useRef(false);
  // 使用者在歷史對話載入前就送出問題時，不要用載入的舊對話蓋掉剛送出的這一輪
  const touched = useRef(false);

  // 身分載入前不顯示助理；系統管理員沒有任何資料權限，不提供助理
  const available = me !== null && me.can_query;

  const setOpen = useCallback((value: boolean) => {
    setOpenState(value);
    remember(OPEN_KEY, value);
  }, []);

  const setFloating = useCallback((value: boolean) => {
    setFloatingState(value);
    remember(FLOAT_KEY, value);
  }, []);

  const refreshList = useCallback(async () => {
    const list = await getJson<ConversationSummary[]>("/api/conversations");
    if (list) setConversations(list);
    return list ?? [];
  }, []);

  const loadConversation = useCallback(async (id: number) => {
    const messages = await getJson<HistoryMessage[]>(`/api/conversations/${id}/messages`);
    if (!messages) return;
    setConversationId(id);
    setTurns(toTurns(messages));
  }, []);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void (async () => {
      setMe(await getJson<Me>("/api/me"));
      try {
        // 只在寬螢幕自動重開（面板與內容並排）；手機上全螢幕會蓋住剛打開的頁面，等使用者自己點
        if (localStorage.getItem(OPEN_KEY) === "1" && window.innerWidth >= 1024) setOpenState(true);
        if (localStorage.getItem(FLOAT_KEY) === "1") setFloatingState(true);
      } catch {
        // 同上
      }
      const list = await refreshList();
      if (list.length && !touched.current) await loadConversation(list[0].id);
    })();
  }, [loadConversation, refreshList]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!available) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen(!open);
      } else if (e.key === "Escape" && open && (floating || window.innerWidth < 1024)) {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [available, floating, open, setOpen]);

  const send = useCallback(async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    touched.current = true;
    setTurns((t) => [...t, { role: "user", text: message }]);
    setBusy(true);
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversationId }),
    }).catch(() => null);
    setBusy(false);
    if (!res || !res.ok) {
      const detail = res ? ((await res.json().catch(() => ({}))) as { detail?: string }).detail : undefined;
      setTurns((t) => [...t, { role: "error", text: detail ?? "連不上助理，請檢查網路後再試一次" }]);
      return;
    }
    const r = (await res.json()) as ChatReply;
    setConversationId(r.conversation_id);
    setTurns((t) => [...t, {
      role: "assistant", text: r.answer, results: r.results, tools: r.meta.tools,
      latencyMs: r.meta.latency_ms, groundingOk: r.meta.grounding_ok,
    }]);
    void refreshList();
  }, [busy, conversationId, refreshList]);

  const ask = useCallback((text: string) => {
    setOpen(true);
    void send(text);
  }, [send, setOpen]);

  const newConversation = useCallback(() => {
    setConversationId(null);
    setTurns([]);
  }, []);

  return (
    <ChatContext value={{ me, open, setOpen, turns, busy, conversationId, conversations, available, floating, setFloating, send, ask, newConversation, loadConversation }}>
      {children}
    </ChatContext>
  );
}

export function useChat(): ChatState {
  const ctx = use(ChatContext);
  if (!ctx) throw new Error("useChat 必須在 ChatProvider 內使用");
  return ctx;
}
