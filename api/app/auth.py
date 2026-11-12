"""身分與角色。角色決定能看的指標來源、敏感名稱與機密指標；範圍決定能看哪些廠區、事業群或產線。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import hash_password, verify_password

COOKIE = "plantsight_session"
LOGIN_FAILED = "帳號或密碼錯誤"
# 帳號不存在時仍做一次雜湊比對，避免用回應時間差判斷帳號是否存在
_DUMMY_HASH = hash_password("timing-equalizer")


@dataclass(frozen=True)
class Role:
    label: str
    facts: frozenset[str]          # 可查的資料來源
    names: frozenset[str]          # 可看名稱的敏感維度（customers／suppliers）
    confidential: bool             # 可看機密指標（報廢金額）


ALL_FACTS = frozenset({"production", "inspection", "defect", "shipment", "supply"})
QUALITY = frozenset({"production", "inspection", "defect", "supply"})
BOTH = frozenset({"customers", "suppliers"})

ROLES = {
    "ceo": Role("總經理", ALL_FACTS, BOTH, True),
    "bg_head": Role("事業群主管", ALL_FACTS, BOTH, True),
    "plant_manager": Role("廠長", ALL_FACTS, BOTH, True),
    "line_lead": Role("產線主管", ALL_FACTS, frozenset(), False),
    "qa": Role("品保工程師", QUALITY, frozenset({"suppliers"}), False),
    "finance": Role("財務", ALL_FACTS, frozenset(), True),
    "sysadmin": Role("系統管理員", frozenset(), frozenset(), False),
}

SCOPE_LABEL = {"all": "全公司", "business_group": "事業群", "plant": "廠區", "line": "產線"}


@dataclass(frozen=True)
class Principal:
    username: str
    name: str
    role: str
    scope_kind: str
    scope_value: str | None

    @property
    def policy(self) -> Role:
        return ROLES[self.role]

    @property
    def scope_text(self) -> str:
        return SCOPE_LABEL[self.scope_kind] + (f" {self.scope_value}" if self.scope_value else "")


def load_principal(db: Session, username: str) -> Principal:
    u = db.get(User, username)
    if u is None:
        raise LookupError(username)
    return Principal(u.username, u.name, u.role, u.scope_kind, u.scope_value)


def authenticate(db: Session, username: str, password: str) -> str | None:
    u = db.get(User, username)
    ok = verify_password(password, u.password_hash if u and u.password_hash else _DUMMY_HASH)
    return u.username if u and u.password_hash and ok else None


def issue_token(username: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": username, "iat": now, "exp": now + timedelta(hours=settings.jwt_ttl_hours)},
                      settings.jwt_secret, algorithm="HS256")


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "請先登入")
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return load_principal(db, claims["sub"])
    except (jwt.PyJWTError, LookupError):
        raise HTTPException(401, "請重新登入")


def require_role(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, "沒有這個頁面的權限")
        return p
    return dep


# 示範帳號（密碼為 SEED_DEMO_PASSWORD，第一次清理資料時寫入）
DEMO_USERS = [
    ("gm", "Grace Morgan", "ceo", "all", None),
    ("pwr.head", "Ethan Park", "bg_head", "business_group", "PWR"),
    ("tw.manager", "Lin Wei", "plant_manager", "plant", "TW"),
    ("vn.manager", "Nguyen Thao", "plant_manager", "plant", "VN"),
    ("tw.smt1.lead", "Chen Hao", "line_lead", "line", "TW-SMT1"),
    ("vn.smt2.lead", "Tran Minh", "line_lead", "line", "VN-SMT2"),
    ("qa.engineer", "Olivia Ross", "qa", "all", None),
    ("finance.analyst", "Daniel Wu", "finance", "all", None),
    ("sysadmin", "System Admin", "sysadmin", "all", None),
]
