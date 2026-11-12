export type Me = {
  username: string;
  name: string;
  role: string;
  role_label: string;
  scope: string;
  can_query: boolean;
  can_review: boolean;
  is_admin: boolean;
};

export type Column = { key: string; label: string; unit?: string; decimals?: number };
export type MetricInfo = { key: string; label: string; unit: string; basis: string; decimals?: number };
export type Excluded = { source: string; reason: string; rows: number };

export type QueryResult = {
  columns: Column[];
  rows: Record<string, string | number | null>[];
  truncated: boolean;
  period: string;
  grain: string;
  compare: string | null;
  scope: string;
  metrics: MetricInfo[];
  excluded: Excluded[];
};

export type ExplainGroup = Record<string, string | number | null>;
export type ExplainResult = {
  metric: string;
  unit: string;
  effect_unit?: string;
  by: string;
  by_key?: string;
  base: string;
  current: string;
  before: number;
  after: number;
  change: number;
  mix_effect?: number;
  rate_effect?: number;
  main_driver?: string;
  groups: ExplainGroup[];
  method: string;
  basis: string;
  excluded: Excluded[];
};

export type ToolResult =
  | { tool: "query_metrics"; args: Record<string, unknown>; data: QueryResult }
  | { tool: "explain_change"; args: Record<string, unknown>; data: ExplainResult };

export type ToolUse = { name: string; denied: boolean; error: boolean };

export type HistoryMessage = {
  role: "user" | "assistant";
  text: string;
  meta: { results?: ToolResult[]; tools?: ToolUse[]; grounding_ok?: boolean } | null;
};

export type ConversationSummary = { id: number; title: string | null; last_at: string };

export type ChatReply = {
  conversation_id: number;
  answer: string;
  results: ToolResult[];
  meta: { model: string; tools: ToolUse[]; grounding_ok: boolean; latency_ms: number; cost_usd: number | null };
};

export type KpiCard = MetricInfo & { value: number | null; previous: number | null; delta: number | null };
export type Overview = { period: string; scope: string; cards: KpiCard[]; trends: Record<string, { period: string; value: number }[]> };
