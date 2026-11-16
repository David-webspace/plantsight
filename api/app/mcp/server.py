"""MCP server（Streamable HTTP，掛在 /mcp）：任何支援 MCP 授權的 Agent 以使用者本人身分查詢 plantsight。

模型是接入方的；身分來自 OAuth 權杖，每次呼叫重新載入角色與範圍，再走和網頁聊天同一套工具、語意層與稽核。
只開放唯讀工具：品保核准待確認資料會改變所有人看到的指標，必須由人在畫面上確認，不透過 MCP 開放。
"""

import json
from typing import Annotated
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from app.agent import audit
from app.agent.tools import FILTERS, PERIOD, TOOLS, ToolContext, run_tool
from app.auth import load_principal
from app.config import settings
from app.db import SessionLocal
from app.mcp.oauth import RESOURCE, SCOPE, Provider, client_label
from app.models import OAuthClient
from app.ratelimit import SlidingWindow

INSTRUCTIONS = """Arcwell Electronics 的生產數據（良率、直通率、不良率、準交率、進料不良率、報廢金額）。
所有數字由語意層依固定口徑計算，請直接引用工具結果並附上口徑與期間，不要自行換算或四捨五入成別的值。
不確定指標或維度代碼時先呼叫 list_metrics。問「為什麼變了」用 explain_change。
資料範圍由使用者登入身分決定；回傳「無權限」就照實告知，不要改用其他條件繞過。"""

base = settings.public_api_url.rstrip("/")
mcp = MCPServer(
    "plantsight", instructions=INSTRUCTIONS, auth_server_provider=Provider(),
    auth=AuthSettings(
        issuer_url=base, resource_server_url=RESOURCE, required_scopes=[SCOPE], validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(enabled=settings.mcp_dynamic_registration, valid_scopes=[SCOPE],
                                                              default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True)),
)
rate = SlidingWindow(settings.mcp_rate_per_minute, 60)


def _call(name: str, args: dict) -> dict:
    token = get_access_token()
    grant_id = (token.claims or {}).get("grant_id")
    with SessionLocal() as db:
        p = load_principal(db, token.subject)
        info = db.scalar(select(OAuthClient.info).where(OAuthClient.client_id == token.client_id)) or {}
        client = client_label(info)
        try:
            # Agent 迴圈失控時次數沒有上限；查詢本身有 5 秒逾時，但不限次數仍可能把資料庫打滿
            rate.check(f"grant:{grant_id}")
        except HTTPException:
            audit.write(db, p, "mcp_tool", question=name, denied=True, deny_reason="rate_limited", channel="mcp", client_name=client)
            return {"error": "呼叫太頻繁，請稍後再試"}
        args = {k: v for k, v in args.items() if v is not None}
        out = run_tool(ToolContext(db, p), name, args)
        audit.write(db, p, "mcp_tool", question=f"{name} {json.dumps(args, ensure_ascii=False)}"[:2000],
                    tools=[{"name": name, "denied": out.denied, "error": out.error}], denied=out.denied,
                    deny_reason=out.result.get("error") if out.denied else None, channel="mcp", client_name=client)
        return out.result


def _doc(name: str) -> str:
    return TOOLS[name].spec.description


Period = Annotated[dict[str, str], Field(json_schema_extra=PERIOD)]
Filters = Annotated[dict[str, list[str]] | None, Field(description=FILTERS["description"])]


@mcp.tool(description=_doc("list_metrics"))
def list_metrics() -> dict:
    return _call("list_metrics", {})


@mcp.tool(description=_doc("query_metrics"))
def query_metrics(
    metrics: Annotated[list[str], Field(description="指標代碼，例如 [\"yield\"]")],
    period: Annotated[dict[str, str], Field(description="{\"from\": \"YYYY-MM\", \"to\": \"YYYY-MM\", \"grain\": \"month|quarter|year|total\"}")],
    group_by: Annotated[list[str] | None, Field(description="分組維度，例如 [\"line\"]")] = None,
    filters: Filters = None,
    compare: Annotated[dict[str, str] | None, Field(description="{\"type\": \"previous_period|same_period_last_year|target\"}")] = None,
    sort: Annotated[dict[str, str] | None, Field(description="{\"by\": 指標代碼, \"order\": \"asc|desc\"}")] = None,
    limit: Annotated[int | None, Field(ge=1, le=50)] = None,
) -> dict:
    return _call("query_metrics", {"metrics": metrics, "period": period, "group_by": group_by, "filters": filters,
                                   "compare": compare, "sort": sort, "limit": limit})


@mcp.tool(description=_doc("explain_change"))
def explain_change(
    metric: Annotated[str, Field(description="指標代碼")],
    by: Annotated[str, Field(description="要拆解的維度，例如 line、product_family、customer")],
    base: Period,
    current: Period,
    filters: Filters = None,
) -> dict:
    return _call("explain_change", {"metric": metric, "by": by, "base": base, "current": current, "filters": filters})


def _hosts() -> list[str]:
    host = urlparse(base).netloc
    return [host, "localhost:*", "127.0.0.1:*"]


# 沒有有效權杖的請求由 SDK 在 HTTP 層回 401（附 WWW-Authenticate 指向授權資訊），連工具清單都看不到
http_app = mcp.streamable_http_app(
    streamable_http_path="/mcp", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=_hosts(),
                                                 allowed_origins=[settings.public_web_url, f"{urlparse(base).scheme}://{urlparse(base).netloc}"]),
)
