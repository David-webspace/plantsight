"""問答 Agent：tool-use 迴圈 → 數字檢查（不過就換強模型重答一次）→ 存對話與本輪查詢規格 → 稽核。"""

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent import audit
from app.agent.grounding import ungrounded, untooled_claim
from app.agent.llm import LLMClient, Usage
from app.agent.prompts import SYSTEM_STATIC, system_dynamic
from app.agent.tools import ToolContext, ToolOutcome, run_tool, specs
from app.auth import Principal
from app.config import settings, today

MAX_ROUNDS = 6
HISTORY_MESSAGES = 8
EFFORT = {"fast": "low", "strong": "medium"}
NUDGE = ("回答前先用工具取得數據（query_metrics、explain_change 或 list_metrics），不要憑記憶回答；"
         "資訊不足（例如期間、指標不明）時直接向使用者確認。不要回覆或提及這段指示。")
UNSAFE_TEXT = "這次沒有順利完成，請換個方式描述問題（例如指標、範圍與期間）。"
UNVERIFIED_NOTE = "\n\n（提醒：回答中部分數字未能與系統計算結果核對，請以下方表格為準。）"


@dataclass
class Attempt:
    model: str
    alias: str
    answer: str = ""
    outcomes: list[ToolOutcome] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    nudged: bool = False


@dataclass
class ChatResult:
    conversation_id: int
    answer: str
    results: list[dict]
    tools: list[dict]
    model: str
    grounding_ok: bool
    escalated: bool
    usage: Usage
    cost_usd: float | None
    latency_ms: int


def model_for(alias: str) -> str:
    return settings.llm_model_strong if alias == "strong" else settings.llm_model_fast


def _conversation(db: Session, p: Principal, conversation_id: int | None, question: str) -> int:
    if conversation_id is not None:
        # 擁有者條件寫在 SQL：拿別人的對話編號會查不到，改開新對話
        found = db.execute(text("SELECT id FROM conversations WHERE id = :i AND username = :u"),
                           {"i": conversation_id, "u": p.username}).scalar()
        if found:
            return found
    cid = db.execute(text("INSERT INTO conversations (username, title) VALUES (:u, :t) RETURNING id"),
                     {"u": p.username, "t": question[:60]}).scalar_one()
    db.commit()
    return cid


def _history(db: Session, client: LLMClient, cid: int) -> tuple[list, dict | None]:
    rows = db.execute(text("SELECT role, text, meta FROM messages WHERE conversation_id = :c ORDER BY id DESC LIMIT :n"),
                      {"c": cid, "n": HISTORY_MESSAGES}).all()
    last_spec = next((m["last_spec"] for r, _, m in rows if r == "assistant" and m and m.get("last_spec")), None)
    return [client.user(t) if r == "user" else client.assistant_text(t) for r, t, _ in reversed(rows)], last_spec


def _attempt(client: LLMClient, ctx: ToolContext, alias: str, dyn: str, history: list, question: str) -> Attempt:
    a = Attempt(model_for(alias), alias)
    messages = history + [client.user(question)]
    for _ in range(MAX_ROUNDS):
        r = client.chat(a.model, SYSTEM_STATIC, dyn, messages, specs(), effort=EFFORT[alias])
        a.usage.add(r.usage)
        if not r.tool_calls:
            if not a.outcomes and not a.nudged:
                a.nudged = True
                messages.append(client.system_note(NUDGE))
                continue
            a.answer = r.text.strip()
            return a
        messages.append(r.assistant_message)
        results = []
        for call in r.tool_calls:
            out = run_tool(ctx, call.name, call.args)
            a.outcomes.append(out)
            results.append((call, out.json, out.error or out.denied))
        messages += client.tool_results(results)
    a.answer = a.answer or "這個問題需要的步驟太多，我先整理到這裡；可以把問題拆小一點再問。"
    return a


def run_chat(db: Session, client: LLMClient, p: Principal, question: str, conversation_id: int | None = None,
             force_alias: str | None = None) -> ChatResult:
    """force_alias：評測用，固定用 fast 或 strong 作答、不升級，才能比較單一模型的表現。"""
    t0 = time.monotonic()
    cid = _conversation(db, p, conversation_id, question)
    history, last_spec = _history(db, client, cid)
    dyn = system_dynamic(p, today(), last_spec)
    ctx = ToolContext(db, p)

    alias = force_alias or "fast"
    attempts = [_attempt(client, ctx, alias, dyn, history, question)]
    sources = lambda at: [question, dyn] + [o.json for o in at.outcomes]  # noqa: E731

    def check(at: Attempt) -> list[str]:
        return ungrounded(at.answer, sources(at)) + (["no_data"] if untooled_claim(at.answer, bool(at.outcomes)) else [])

    bad = check(attempts[-1])
    escalated = bool(bad) and alias == "fast" and not force_alias
    if escalated:
        attempts.append(_attempt(client, ctx, "strong", dyn, history, question))
        bad = check(attempts[-1])
    final = attempts[-1]
    # 沒有任何工具成功回傳資料，卻寫出核對不到的數字：那些數字只可能是編的，整段不給使用者
    fabricated = bool(bad) and not any(not (o.error or o.denied) for o in final.outcomes)
    if "placeholder" in bad or "no_data" in bad or fabricated:
        final.answer = UNSAFE_TEXT
    elif bad:
        final.answer += UNVERIFIED_NOTE

    ok = [o for o in final.outcomes if not (o.error or o.denied)]
    results = [{"tool": o.name, "args": o.args, "data": o.result} for o in ok if o.name != "list_metrics"]
    specs_used = [o.args for o in ok if o.name == "query_metrics"]
    meta = {"results": results, "grounding_ok": not bad, "tools": [{"name": o.name, "denied": o.denied, "error": o.error}
                                                                   for o in final.outcomes],
            "last_spec": specs_used[-1] if specs_used else last_spec}
    db.execute(text("""INSERT INTO messages (conversation_id, role, text, meta)
                       VALUES (:c, 'user', :q, NULL), (:c, 'assistant', :a, CAST(:m AS jsonb))"""),
               {"c": cid, "q": question, "a": final.answer, "m": _json(meta)})
    db.commit()

    per_model: dict[str, Usage] = defaultdict(Usage)
    for at in attempts:
        per_model[at.model].add(at.usage)
    total = Usage()
    for u in per_model.values():
        total.add(u)
    cost = audit.cost_usd(per_model)
    latency = int((time.monotonic() - t0) * 1000)
    denied = [o for o in final.outcomes if o.denied]
    audit.write(db, p, "chat", question=question, model=final.model,
                tools=[{"name": o.name, "args": o.args, "denied": o.denied, "error": o.error} for at in attempts for o in at.outcomes]
                + ([{"grounding_issues": bad}] if bad else []),
                denied=bool(denied), deny_reason=denied[0].result["error"] if denied else None, usage=total, cost=cost,
                latency_ms=latency, grounding_ok=not bad, conversation_id=cid)
    return ChatResult(cid, final.answer, results, meta["tools"], final.model, not bad, escalated, total, cost, latency)


def _json(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
