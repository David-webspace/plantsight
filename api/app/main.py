"""plantsight API。權限一律依登入身分在伺服器端判斷；前端只負責顯示。"""

import io
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent import audit
from app.agent.llm import get_client, not_configured
from app.agent.loop import run_chat
from app.auth import COOKIE, LOGIN_FAILED, Principal, authenticate, current_principal, issue_token, load_principal, require_role
from app.config import TZ, settings, today
from app.db import engine, get_db
from app.etl.review import ReviewError, decide
from app.mcp import oauth
from app.mcp.server import http_app as mcp_http_app
from app.mcp.server import mcp
from app.ratelimit import SlidingWindow
from app.semantic.query import AccessDenied, QuerySpec, SpecError, run_query

log = logging.getLogger("plantsight")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="plantsight", lifespan=lifespan, docs_url="/docs" if settings.enable_api_docs else None,
              redoc_url=None, openapi_url="/openapi.json" if settings.enable_api_docs else None)
login_limit = SlidingWindow(10, 300)
chat_limit = SlidingWindow(settings.chat_rate_per_minute, 60)


@app.exception_handler(AccessDenied)
def _denied(_: Request, e: AccessDenied) -> JSONResponse:
    return JSONResponse({"detail": f"無權限：{e}"}, status_code=403)


@app.exception_handler(SpecError)
def _spec(_: Request, e: SpecError) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.get("/health")
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


def me_view(p: Principal) -> dict:
    return {"username": p.username, "name": p.name, "role": p.role, "role_label": p.policy.label, "scope": p.scope_text,
            "can_query": bool(p.policy.facts), "can_review": p.role == "qa", "is_admin": p.role == "sysadmin"}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    login_limit.check(f"{request.client.host if request.client else '-'}:{body.username}")
    username = authenticate(db, body.username.strip(), body.password)
    if username is None:
        audit.write(db, None, "login_failed", question=body.username[:64])
        raise HTTPException(401, LOGIN_FAILED)
    p = load_principal(db, username)
    response.set_cookie(COOKIE, issue_token(username), httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.jwt_ttl_hours * 3600)
    audit.write(db, p, "login")
    return me_view(p)


@app.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return me_view(p)


# ── 總覽 ───────────────────────────────────────────────────

KPIS = ["yield", "fpy", "dppm", "otd", "incoming_reject_rate", "scrap_cost"]


def _month(offset: int) -> str:
    t = today().year * 12 + today().month - 1 + offset
    return f"{t // 12}-{t % 12 + 1:02d}"


@app.get("/api/overview")
def overview(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    """上個月的關鍵指標（和前一個月比）與近 12 個月趨勢；登入者看不到的指標直接略過。"""
    last, first = _month(-1), _month(-12)
    cards, trends = [], {}
    for k in KPIS:
        try:
            card = run_query(db, QuerySpec.model_validate({"metrics": [k], "period": {"from": last, "to": last},
                                                           "compare": {"type": "previous_period"}}), p)
            trend = run_query(db, QuerySpec.model_validate({"metrics": [k], "period": {"grain": "month", "from": first, "to": last},
                                                            "limit": 12}), p)
        except AccessDenied:
            continue
        row = card["rows"][0] if card["rows"] else {}
        cards.append({**card["metrics"][0], "value": row.get(k), "previous": row.get(f"{k}__compare"),
                      "delta": row.get(f"{k}__delta")})
        trends[k] = [{"period": r["period"], "value": r[k]} for r in trend["rows"]]
    return {"period": last, "scope": p.scope_text, "cards": cards, "trends": trends}


# ── 助理 ───────────────────────────────────────────────────

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None


def _tokens_today(db: Session, username: str) -> int:
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return db.execute(text("""SELECT COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0)
                              FROM audit_log WHERE username = :u AND ts >= :s"""), {"u": username, "s": start}).scalar()


@app.post("/api/chat")
def chat(body: ChatIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    # 會呼叫付費模型的端點：單獨限流＋每人每日 token 上限
    if reason := not_configured():
        raise HTTPException(503, f"助理尚未接上模型（{reason}）。總覽與其他頁面不受影響；請管理員依 README 設定 .env 後重啟。")
    chat_limit.check(p.username)
    if _tokens_today(db, p.username) >= settings.chat_daily_token_cap:
        raise HTTPException(429, "今日助理使用量已達上限，請明天再試")
    try:
        r = run_chat(db, get_client(), p, body.message.strip(), body.conversation_id)
    except Exception:
        db.rollback()
        log.exception("chat failed")
        raise HTTPException(503, "助理暫時無法回應，請稍後再試")
    return {"conversation_id": r.conversation_id, "answer": r.answer, "results": r.results,
            "meta": {"model": r.model, "tools": r.tools, "grounding_ok": r.grounding_ok, "latency_ms": r.latency_ms,
                     "cost_usd": r.cost_usd}}


@app.get("/api/conversations")
def conversations(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(text("""
        SELECT c.id, c.title, COALESCE(MAX(m.created_at), c.created_at) AS last_at
        FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.username = :u GROUP BY c.id ORDER BY last_at DESC LIMIT 10"""), {"u": p.username}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/conversations/{cid}/messages")
def messages(cid: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    # 擁有者條件寫在 SQL：別人的對話一律查不到
    rows = db.execute(text("""
        SELECT m.role, m.text, m.meta FROM messages m JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id = :c AND c.username = :u ORDER BY m.id"""), {"c": cid, "u": p.username}).mappings().all()
    if not rows:
        raise HTTPException(404, "找不到對話")
    return [dict(r) for r in rows]


@app.post("/api/export")
def export(spec: dict, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> Response:
    """把查詢結果匯出成 Excel：用登入者的權限重新查一次，不信任前端傳來的表格內容。"""
    try:
        r = run_query(db, QuerySpec.model_validate(spec), p)
    except ValidationError:
        raise HTTPException(400, "查詢規格格式不正確")
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append([c["label"] + (f"（{c['unit']}）" if c.get("unit") else "") for c in r["columns"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in r["rows"]:
        # Excel 會把 = + - @ 開頭的字串當公式執行，一律加單引號
        ws.append([("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v for v in (row.get(c["key"]) for c in r["columns"])])
    notes = wb.create_sheet("口徑")
    notes.append(["期間", r["period"]])
    notes.append(["範圍", r["scope"]])
    for m in r["metrics"]:
        notes.append([m["label"], m["basis"]])
    for e in r["excluded"]:
        notes.append(["排除", f"{e['reason']}：{e['rows']} 筆"])
    buf = io.BytesIO()
    wb.save(buf)
    name = f"plantsight_{'-'.join(QuerySpec.model_validate(spec).metrics)}_{r['period'].replace('～', '_')}.xlsx"
    audit.write(db, p, "export", question=name)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"content-disposition": f"attachment; filename=\"export.xlsx\"; filename*=UTF-8''{quote(name)}",
                             "cache-control": "no-store"})


# ── 資料品質（品保） ────────────────────────────────────────

@app.get("/api/review")
def review_list(_: Principal = Depends(require_role("qa", "sysadmin")), db: Session = Depends(get_db)) -> dict:
    items = db.execute(text("""SELECT id, source, kind, jsonb_array_length(keys) AS rows, description, suggestion, status,
                                      decided_by, decided_at FROM review_items ORDER BY status <> 'pending', kind, id""")).mappings().all()
    report = db.execute(text("SELECT report, created_at FROM cleaning_runs ORDER BY id DESC LIMIT 1")).mappings().one_or_none()
    return {"items": [dict(i) for i in items], "report": dict(report) if report else None}


class DecisionIn(BaseModel):
    approve: bool


@app.post("/api/review/{item_id}")
def review_decide(item_id: str, body: DecisionIn, p: Principal = Depends(require_role("qa")), db: Session = Depends(get_db)) -> dict:
    try:
        report = decide(db, item_id, body.approve, p.username)
    except ReviewError as e:
        raise HTTPException(400, str(e))
    audit.write(db, p, "review_approve" if body.approve else "review_reject", question=item_id)
    return {"ok": True, "report": report}


# ── 稽核（系統管理員） ──────────────────────────────────────

@app.get("/api/admin/audit")
def audit_log(event: str | None = Query(None, max_length=32), user: str | None = Query(None, max_length=32),
              channel: str | None = Query(None, pattern="^(web|mcp)$"), flagged: bool = False, _: Principal = Depends(require_role("sysadmin")), db: Session = Depends(get_db)) -> dict:
    where, params = [], {}
    if event:
        where.append("event = :event")
        params["event"] = event
    if user:
        where.append("username ILIKE :user")
        params["user"] = f"%{user.strip()}%"
    if channel:
        where.append("channel = :channel")
        params["channel"] = channel
    if flagged:
        where.append("(denied OR grounding_ok = false)")
    rows = db.execute(text(f"""SELECT ts, username, role, event, question, model, tools, denied, deny_reason, input_tokens,
                                      output_tokens, cache_read_tokens, cost_usd, latency_ms, grounding_ok, channel, client_name FROM audit_log
                               {"WHERE " + " AND ".join(where) if where else ""} ORDER BY id DESC LIMIT 200"""), params).mappings().all()
    summary = db.execute(text("""
        SELECT model, COUNT(*) AS n, AVG(input_tokens + cache_read_tokens + cache_write_tokens)::int AS avg_input,
               AVG(output_tokens)::int AS avg_output, AVG(latency_ms)::int AS avg_latency_ms, SUM(cost_usd) AS cost_usd,
               SUM(CASE WHEN denied THEN 1 ELSE 0 END) AS denied, SUM(CASE WHEN grounding_ok = false THEN 1 ELSE 0 END) AS ungrounded
        FROM audit_log WHERE event = 'chat' GROUP BY model""")).mappings().all()
    return {"summary": [dict(s) for s in summary], "events": [dict(r) for r in rows]}


# ── 連接 AI 工具（MCP 的 OAuth 同意與連線管理，以網頁登入身分操作） ───────────


def same_origin(request: Request) -> None:
    # 同意會發出可查資料的權杖：只接受從 plantsight 網頁本身送出的請求。
    # 不檢查的話，惡意網頁可以趁員工已登入，自動送出同意（CSRF；同一台主機不同埠在 SameSite 規則下算同站，cookie 會帶上）。
    if request.headers.get("origin") != settings.public_web_url.rstrip("/"):
        raise HTTPException(403, "請從 plantsight 網頁操作")


@app.get("/api/oauth/requests/{rid}")
def oauth_request(rid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    try:
        req, info = oauth.pending(db, rid)
    except oauth.RequestGone:
        raise HTTPException(404, "這個連線要求已過期或不存在，請回到 AI 工具重新連線")
    return {"client_name": oauth.client_label(info), "redirect_host": urlparse(req.params["redirect_uri"]).netloc,
            "user": me_view(p), "scope": "唯讀查詢生產數據（良率、不良率、準交率等），範圍和你在網頁看到的相同"}


@app.post("/api/oauth/requests/{rid}/approve")
def oauth_approve(rid: str, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    same_origin(request)
    if not p.policy.facts:
        raise HTTPException(403, "這個帳號沒有可查詢的生產數據，無法連接 AI 工具")
    try:
        req, info = oauth.pending(db, rid)
        redirect = oauth.approve(db, rid, p.username)
    except oauth.RequestGone:
        raise HTTPException(404, "這個連線要求已過期或不存在，請回到 AI 工具重新連線")
    audit.write(db, p, "mcp_connect", question=oauth.client_label(info), channel="mcp", client_name=oauth.client_label(info))
    return {"redirect": redirect}


@app.post("/api/oauth/requests/{rid}/deny")
def oauth_deny(rid: str, request: Request, _: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    same_origin(request)
    try:
        return {"redirect": oauth.deny(db, rid)}
    except oauth.RequestGone:
        raise HTTPException(404, "這個連線要求已過期或不存在")


@app.get("/api/oauth/grants")
def oauth_grants(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    return {"grants": oauth.grants(db, p.username), "mcp_url": oauth.RESOURCE, "can_connect": bool(p.policy.facts)}


@app.post("/api/oauth/grants/{grant_id}/revoke")
def oauth_revoke(grant_id: int, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    same_origin(request)
    client = oauth.revoke_own(db, p.username, grant_id)
    if client is None:
        raise HTTPException(404, "找不到這個連線")
    audit.write(db, p, "mcp_revoke", question=client, channel="mcp", client_name=client)
    return {"ok": True}


# MCP 與 OAuth 端點（/mcp、/authorize、/token、/register、/revoke、/.well-known/*）；放在最後，前面的 API 路由優先比對
app.mount("/", mcp_http_app)
