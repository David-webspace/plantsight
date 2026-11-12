"""身分與角色。角色決定能看的指標來源、敏感名稱與機密指標；範圍決定能看哪些廠區、事業群或產線。"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import User


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


# 示範帳號；密碼在 M4 加上登入時再設定
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
