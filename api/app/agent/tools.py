"""Agent 工具。參數裡沒有身分欄位：呼叫者是誰由後端注入的 Principal 決定，權限在語意層檢查。"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.llm import ToolSpec
from app.auth import Principal
from app.semantic.analysis import explain_change
from app.semantic.catalog import catalog
from app.semantic.query import AccessDenied, QuerySpec, SpecError, run_query

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    db: Session
    principal: Principal


@dataclass
class ToolOutcome:
    name: str
    args: dict
    result: dict
    denied: bool = False
    error: bool = False

    @property
    def json(self) -> str:
        return json.dumps(self.result, ensure_ascii=False, default=str, separators=(",", ":"))


def _list_metrics(c: ToolContext, _: dict) -> dict:
    cat, policy = catalog(), c.principal.policy
    metrics = []
    for m in cat.metrics.values():
        fact = cat.facts[m.fact]
        allowed = m.fact in policy.facts and (policy.confidential or not m.confidential) and (
            c.principal.scope_kind == "all" or c.principal.scope_kind in fact.scope)
        metrics.append({"key": m.key, "label": m.label, "unit": m.unit, "basis": m.basis, "dimensions": list(fact.dims),
                        "has_target": bool(m.target), "allowed_for_you": allowed})
    return {"metrics": metrics, "dimensions": {k: v["label"] for k, v in cat.dims.items()},
            "data_range": "2025-01～2026-10", "your_scope": c.principal.scope_text}


PERIOD = {"type": "object", "properties": {"from": {"type": "string", "description": "YYYY-MM"},
                                           "to": {"type": "string", "description": "YYYY-MM"}},
          "required": ["from", "to"], "additionalProperties": False}
FILTERS = {"type": "object", "description": "維度 → 值清單，例如 {\"plant\": [\"TW\"], \"line\": [\"TW-SMT1\"]}",
           "additionalProperties": {"type": "array", "items": {"type": "string"}}}


@dataclass
class Tool:
    spec: ToolSpec
    run: Callable[[ToolContext, dict], dict]


TOOLS: dict[str, Tool] = {t.spec.name: t for t in [
    Tool(ToolSpec("list_metrics", "列出可查的指標、口徑、可用維度，以及哪些指標在你的權限內。不確定代碼時先查。",
                  {"type": "object", "properties": {}, "additionalProperties": False}), _list_metrics),
    Tool(ToolSpec(
        "query_metrics",
        "依查詢規格計算指標。metrics 為指標代碼（最多 4 個）；group_by 為分組維度（最多 2 個）；filters 為篩選；"
        "period.grain 為 month／quarter／year／total；compare.type 為 previous_period（和前一段等長期間比）、"
        "same_period_last_year（和去年同期比）或 target（和目標比，需依目標所在維度分組）；sort、limit 控制排序與筆數。"
        "結果附口徑、範圍與被排除的筆數。",
        {"type": "object", "properties": {
            "metrics": {"type": "array", "items": {"type": "string"}},
            "group_by": {"type": "array", "items": {"type": "string"}},
            "filters": FILTERS,
            "period": {"type": "object", "properties": {
                "grain": {"type": "string", "enum": ["month", "quarter", "year", "total"]},
                "from": {"type": "string", "description": "YYYY-MM"}, "to": {"type": "string", "description": "YYYY-MM"}},
                "required": ["from", "to"], "additionalProperties": False},
            "compare": {"type": "object", "properties": {"type": {"type": "string",
                                                                  "enum": ["previous_period", "same_period_last_year", "target"]}},
                        "required": ["type"], "additionalProperties": False},
            "sort": {"type": "object", "properties": {"by": {"type": "string"}, "order": {"type": "string", "enum": ["asc", "desc"]}},
                     "required": ["by"], "additionalProperties": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         "required": ["metrics", "period"], "additionalProperties": False}),
        lambda c, a: run_query(c.db, QuerySpec.model_validate(a), c.principal)),
    Tool(ToolSpec(
        "explain_change",
        "回答「為什麼變了」：把一個指標從基期到本期的變化拆給某個維度的各組。比率型指標會拆成組合效應（各組占比改變）"
        "和比率效應（各組自己變好或變壞），並列出貢獻最大的組；加總型指標列出各組增減。直通率不支援拆解。",
        {"type": "object", "properties": {
            "metric": {"type": "string"}, "by": {"type": "string", "description": "要拆解的維度"},
            "base": PERIOD, "current": PERIOD, "filters": FILTERS},
         "required": ["metric", "by", "base", "current"], "additionalProperties": False}),
        lambda c, a: explain_change(c.db, c.principal, a["metric"], a["by"], a["base"], a["current"], a.get("filters"))),
]}


def specs() -> list[ToolSpec]:
    return [t.spec for t in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, args: dict) -> ToolOutcome:
    tool = TOOLS.get(name)
    if tool is None:
        return ToolOutcome(name, args, {"error": f"沒有 {name} 這個工具"}, error=True)
    try:
        return ToolOutcome(name, args, tool.run(ctx, args))
    except AccessDenied as e:
        # 範圍與角色限制的原因本身不是機密，直接說明，使用者才知道該找誰
        return ToolOutcome(name, args, {"error": f"無權限：{e}"}, denied=True)
    except SpecError as e:
        return ToolOutcome(name, args, {"error": f"查詢條件需要修正：{e}"}, error=True)
    except (ValidationError, KeyError, TypeError) as e:
        log.info("tool %s bad args %s: %s", name, args, e)
        return ToolOutcome(name, args, {"error": "查詢規格格式不正確，請對照工具說明修正（期間用 YYYY-MM）"}, error=True)
    except Exception:
        ctx.db.rollback()
        log.exception("tool %s failed", name)
        return ToolOutcome(name, args, {"error": "系統暫時無法處理這個查詢"}, error=True)
