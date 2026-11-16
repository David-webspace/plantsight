"""MCP 的 OAuth 授權伺服器（SDK 的 OAuthAuthorizationServerProvider，狀態存在 Postgres）。

流程：client 動態註冊 → /authorize 存一筆待同意請求、轉到網頁同意頁 → 使用者以自己的帳號同意 → 授權碼 →
/token 換 access（1 小時）＋ refresh（30 天、每次換發都輪替）。PKCE、redirect_uri 比對、resource 參數由 SDK 處理。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic import AnyUrl
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError, RefreshToken,
                                      RegistrationError, TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config import settings
from app.db import SessionLocal
from app.models import OAuthClient, OAuthCode, OAuthGrant, OAuthRequest, OAuthToken as TokenRow, User

SCOPE = "metrics:read"
RESOURCE = settings.public_api_url.rstrip("/") + "/mcp"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)
MAX_CLIENTS = 500  # 動態註冊不需登入；設上限，避免被當成免費儲存空間灌爆


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_redirect(uri: str) -> bool:
    # 只收 https，或本機 loopback 的 http（桌面／CLI client 用隨機埠接回呼）。
    # 不限制的話，攻擊者註冊一個 redirect 到自己網站的 client，騙員工按同意，授權碼就送到攻擊者手上。
    u = urlparse(uri)
    if u.scheme == "https":
        return bool(u.hostname)
    if u.scheme != "http" or not u.hostname:
        return False
    if u.hostname == "localhost":
        return True
    try:
        return ip_address(u.hostname).is_loopback
    except ValueError:
        return False


def client_label(info: dict) -> str:
    return (info.get("client_name") or "未命名的 AI 工具")[:128]


class Provider:
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with SessionLocal() as db:
            row = db.get(OAuthClient, client_id)
            return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not all(_safe_redirect(str(u)) for u in client_info.redirect_uris or []):
            raise RegistrationError("invalid_redirect_uri", "redirect_uri 只接受 https 或本機 loopback 位址")
        with SessionLocal() as db:
            if db.scalar(select(func.count()).select_from(OAuthClient)) >= MAX_CLIENTS:
                raise RegistrationError("invalid_client_metadata", "註冊的 client 已達上限，請洽系統管理員")
            db.add(OAuthClient(client_id=client_info.client_id, info=client_info.model_dump(mode="json", exclude_none=True)))
            db.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # 權杖只發給本服務的 /mcp；client 要求別的 resource 就拒絕（避免拿這裡的權杖去打別的服務，或反過來）
        if params.resource not in (None, RESOURCE):
            raise AuthorizeError("invalid_target", f"這個授權伺服器只發給 {RESOURCE} 的權杖")
        if params.scopes and set(params.scopes) - {SCOPE}:
            raise AuthorizeError("invalid_scope", f"可用的 scope 只有 {SCOPE}")
        rid = secrets.token_urlsafe(24)
        with SessionLocal() as db:
            db.execute(delete(OAuthRequest).where(OAuthRequest.expires_at < _now()))
            db.add(OAuthRequest(id=rid, client_id=client.client_id, expires_at=_now() + REQUEST_TTL, params={
                "state": params.state, "code_challenge": params.code_challenge, "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly}))
            db.commit()
        return f"{settings.public_web_url.rstrip('/')}/connect?req={rid}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with SessionLocal() as db:
            row = db.get(OAuthCode, _hash(authorization_code))
            if row is None or row.client_id != client.client_id:
                return None
            p = row.params
            return AuthorizationCode(code=authorization_code, scopes=[SCOPE], expires_at=row.expires_at.timestamp(),
                                     client_id=row.client_id, code_challenge=p["code_challenge"], redirect_uri=AnyUrl(p["redirect_uri"]),
                                     redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"], resource=RESOURCE,
                                     subject=row.username)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        with SessionLocal() as db:
            # 授權碼只能用一次：先刪除，刪不到代表已經被用過
            if db.execute(delete(OAuthCode).where(OAuthCode.code_hash == _hash(authorization_code.code))).rowcount != 1:
                raise TokenError("invalid_grant", "授權碼已使用過")
            grant = OAuthGrant(username=authorization_code.subject, client_id=client.client_id, scopes=[SCOPE])
            db.add(grant)
            db.flush()
            out = _issue(db, grant.id)
            db.commit()
            return out

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with SessionLocal() as db:
            found = _active(db, refresh_token, "refresh")
            if found is None:
                return None
            row, grant = found
            if grant.client_id != client.client_id:
                return None
            if row.used_at is not None:
                # 已經換發過的 refresh token 又出現：合法 client 不會這樣做，視為外洩，整條連線作廢
                _revoke(db, grant.id, "refresh_reuse")
                db.commit()
                return None
            return RefreshToken(token=refresh_token, client_id=grant.client_id, scopes=grant.scopes,
                                expires_at=int(row.expires_at.timestamp()), resource=RESOURCE, subject=grant.username)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        with SessionLocal() as db:
            row = db.get(TokenRow, _hash(refresh_token.token))
            row.used_at = _now()
            db.execute(update(TokenRow).where(TokenRow.grant_id == row.grant_id, TokenRow.kind == "access")
                       .values(expires_at=_now()))
            out = _issue(db, row.grant_id)
            db.commit()
            return out

    async def load_access_token(self, token: str) -> AccessToken | None:
        with SessionLocal() as db:
            found = _active(db, token, "access")
            if found is None:
                return None
            row, grant = found
            # 帳號被刪除就立即失效；角色與範圍每次呼叫都重新讀取（見 server.py），調職後不會沿用舊範圍
            if db.get(User, grant.username) is None:
                return None
            grant.last_used_at = _now()
            db.commit()
            return AccessToken(token=token, client_id=grant.client_id, scopes=grant.scopes,
                               expires_at=int(row.expires_at.timestamp()), resource=RESOURCE, subject=grant.username,
                               claims={"grant_id": grant.id})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with SessionLocal() as db:
            row = db.get(TokenRow, _hash(token.token))
            if row is not None:
                _revoke(db, row.grant_id, "client_revoked")
                db.commit()


def _issue(db: Session, grant_id: int) -> OAuthToken:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    db.add_all([TokenRow(token_hash=_hash(access), grant_id=grant_id, kind="access", expires_at=now + ACCESS_TTL),
                TokenRow(token_hash=_hash(refresh), grant_id=grant_id, kind="refresh", expires_at=now + REFRESH_TTL)])
    return OAuthToken(access_token=access, token_type="Bearer", expires_in=int(ACCESS_TTL.total_seconds()),
                      refresh_token=refresh, scope=SCOPE)


def _active(db: Session, token: str, kind: str) -> tuple[TokenRow, OAuthGrant] | None:
    row = db.get(TokenRow, _hash(token))
    if row is None or row.kind != kind or row.expires_at <= _now():
        return None
    grant = db.get(OAuthGrant, row.grant_id)
    if grant is None or grant.revoked_at is not None:
        return None
    return row, grant


def _revoke(db: Session, grant_id: int, reason: str) -> None:
    db.execute(update(OAuthGrant).where(OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None))
               .values(revoked_at=_now(), revoke_reason=reason))


# ── 網頁同意流程（以網頁登入的身分呼叫） ──────────────────────────


class RequestGone(Exception):
    pass


def pending(db: Session, rid: str) -> tuple[OAuthRequest, dict]:
    req = db.get(OAuthRequest, rid)
    if req is None or req.expires_at <= _now():
        raise RequestGone
    return req, db.get(OAuthClient, req.client_id).info


def approve(db: Session, rid: str, username: str) -> str:
    req, _ = pending(db, rid)
    code = secrets.token_urlsafe(32)
    db.add(OAuthCode(code_hash=_hash(code), client_id=req.client_id, username=username, params=req.params,
                     expires_at=_now() + CODE_TTL))
    db.delete(req)
    db.commit()
    return construct_redirect_uri(req.params["redirect_uri"], code=code, state=req.params["state"])


def deny(db: Session, rid: str) -> str:
    req, _ = pending(db, rid)
    db.delete(req)
    db.commit()
    return construct_redirect_uri(req.params["redirect_uri"], error="access_denied", state=req.params["state"])


def grants(db: Session, username: str) -> list[dict]:
    rows = db.execute(select(OAuthGrant, OAuthClient.info).join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
                      .where(OAuthGrant.username == username, OAuthGrant.revoked_at.is_(None))
                      .order_by(OAuthGrant.created_at.desc())).all()
    return [{"id": g.id, "client_name": client_label(info), "created_at": g.created_at.isoformat(),
             "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None} for g, info in rows]


def revoke_own(db: Session, username: str, grant_id: int) -> str | None:
    """撤銷成功回傳 client 名稱（給稽核用）；不存在或不是自己的連線回 None。"""
    # 擁有者條件寫進 SQL：撤銷別人的連線時影響 0 列，和「不存在」一樣回 404
    client_id = db.execute(update(OAuthGrant).where(OAuthGrant.id == grant_id, OAuthGrant.username == username,
                                                    OAuthGrant.revoked_at.is_(None))
                           .values(revoked_at=_now(), revoke_reason="user_revoked").returning(OAuthGrant.client_id)).scalar()
    db.commit()
    return client_label(db.get(OAuthClient, client_id).info) if client_id else None
