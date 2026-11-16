"""MCP + OAuth 端對端：動態註冊 → 授權 → 使用者在網頁同意 → 換權杖 → 以本人身分呼叫工具；以及各種攻擊情境。不呼叫模型。"""

import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.main import app, login_limit
from app.mcp.oauth import RESOURCE
from app.mcp.server import rate

WEB = settings.public_web_url.rstrip("/")
REDIRECT = "http://localhost:33418/callback"
OCT = {"from": "2026-10", "to": "2026-10"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture(scope="module")
def app_client():
    # MCP 的 session manager 每個程序只能啟動一次，整個模組共用同一個 lifespan
    with TestClient(app, base_url=settings.public_api_url) as c:
        yield c
    with SessionLocal() as db:  # 測試註冊的 client（連帶授權與權杖）不留在資料庫
        db.execute(text("DELETE FROM oauth_clients WHERE info->>'client_name' = 'Test Agent'"))
        db.execute(text("DELETE FROM audit_log WHERE client_name = 'Test Agent'"))
        db.commit()


@pytest.fixture
def client(app_client):
    login_limit.hits.clear()
    rate.hits.clear()
    app_client.cookies.clear()
    return app_client


def register(c, redirect=REDIRECT, name="Test Agent"):
    r = c.post("/register", json={"client_name": name, "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
                                  "response_types": ["code"], "token_endpoint_auth_method": "none"})
    return r


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize(c, client_id, challenge, resource=RESOURCE):
    r = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "s1",
                                    "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource,
                                    "scope": "metrics:read"}, follow_redirects=False)
    return r


def login(c, user):
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"username": user, "password": os.environ["SEED_DEMO_PASSWORD"]})
    assert r.status_code == 200, r.text


def connect(c, user):
    """完整流程，回傳 (client_id, tokens)。"""
    client_id = register(c).json()["client_id"]
    verifier, challenge = pkce()
    rid = parse_qs(urlparse(authorize(c, client_id, challenge).headers["location"]).query)["req"][0]
    login(c, user)
    redirect = c.post(f"/api/oauth/requests/{rid}/approve", headers={"origin": WEB}).json()["redirect"]
    q = parse_qs(urlparse(redirect).query)
    assert q["state"] == ["s1"]
    c.cookies.clear()
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
                                 "client_id": client_id, "code_verifier": verifier, "resource": RESOURCE})
    assert tok.status_code == 200, tok.text
    return client_id, tok.json() | {"_code": q["code"][0], "_verifier": verifier}


def call(c, token, name, args):
    r = c.post("/mcp", headers=MCP_HEADERS | {"authorization": f"Bearer {token}"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r


def result(r):
    body = r.json()
    assert "result" in body, body
    return body["result"]["structuredContent"] if body["result"].get("structuredContent") else body["result"]


def test_no_token_gets_401_with_discovery(client):
    r = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
    meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == RESOURCE
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"] and asm["registration_endpoint"]


def test_tokens_carry_the_users_own_scope(client):
    _, tok = connect(client, "tw.smt1.lead")
    own = call(client, tok["access_token"], "query_metrics", {"metrics": ["yield"], "period": OCT, "filters": {"line": ["TW-SMT1"]}})
    assert own.status_code == 200 and "rows" in str(own.json())
    other = result(call(client, tok["access_token"], "query_metrics", {"metrics": ["yield"], "period": OCT, "filters": {"line": ["VN-SMT2"]}}))
    assert "無權限" in str(other)
    with SessionLocal() as db:
        row = db.execute(text("SELECT channel, client_name, denied FROM audit_log WHERE event = 'mcp_tool' ORDER BY id DESC LIMIT 1")).one()
    assert row == ("mcp", "Test Agent", True)


def test_code_is_single_use_and_needs_the_right_verifier(client):
    client_id = register(client).json()["client_id"]
    verifier, challenge = pkce()
    rid = parse_qs(urlparse(authorize(client, client_id, challenge).headers["location"]).query)["req"][0]
    login(client, "tw.manager")
    code = parse_qs(urlparse(client.post(f"/api/oauth/requests/{rid}/approve", headers={"origin": WEB}).json()["redirect"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id, "resource": RESOURCE}
    assert client.post("/token", data=form | {"code_verifier": "x" * 50}).status_code == 400
    assert client.post("/token", data=form | {"code_verifier": verifier}).status_code == 200
    assert client.post("/token", data=form | {"code_verifier": verifier}).status_code == 400  # 第二次用同一個授權碼


def test_other_resource_is_refused(client):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    r = authorize(client, client_id, challenge, resource="https://other.example.com/mcp")
    assert "invalid_target" in r.headers.get("location", "") + r.text


def test_refresh_rotates_and_reuse_kills_the_connection(client):
    client_id, tok = connect(client, "tw.manager")
    form = {"grant_type": "refresh_token", "client_id": client_id, "resource": RESOURCE}
    new = client.post("/token", data=form | {"refresh_token": tok["refresh_token"]})
    assert new.status_code == 200
    assert call(client, tok["access_token"], "list_metrics", {}).status_code == 401  # 舊 access 隨換發失效
    assert call(client, new.json()["access_token"], "list_metrics", {}).status_code == 200
    # 舊的 refresh token 被重用 → 視為外洩，整條連線作廢，連新權杖也失效
    assert client.post("/token", data=form | {"refresh_token": tok["refresh_token"]}).status_code == 400
    assert call(client, new.json()["access_token"], "list_metrics", {}).status_code == 401


def test_user_can_revoke_only_own_connection(client):
    _, tok = connect(client, "tw.manager")
    login(client, "tw.manager")
    gid = client.get("/api/oauth/grants").json()["grants"][0]["id"]
    login(client, "vn.manager")
    assert client.post(f"/api/oauth/grants/{gid}/revoke", headers={"origin": WEB}).status_code == 404
    assert call(client, tok["access_token"], "list_metrics", {}).status_code == 200
    login(client, "tw.manager")
    assert client.post(f"/api/oauth/grants/{gid}/revoke", headers={"origin": WEB}).status_code == 200
    assert call(client, tok["access_token"], "list_metrics", {}).status_code == 401


def test_non_local_http_redirect_is_rejected(client):
    assert register(client, redirect="http://evil.example.com/cb").status_code == 400
    assert register(client, redirect="https://agent.example.com/cb").status_code == 201


def test_approve_requires_the_web_origin(client):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    rid = parse_qs(urlparse(authorize(client, client_id, challenge).headers["location"]).query)["req"][0]
    login(client, "tw.manager")
    assert client.post(f"/api/oauth/requests/{rid}/approve", headers={"origin": "http://localhost:6666"}).status_code == 403
    assert client.post(f"/api/oauth/requests/{rid}/approve").status_code == 403


def test_sysadmin_cannot_connect(client):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    rid = parse_qs(urlparse(authorize(client, client_id, challenge).headers["location"]).query)["req"][0]
    login(client, "sysadmin")
    assert client.post(f"/api/oauth/requests/{rid}/approve", headers={"origin": WEB}).status_code == 403
