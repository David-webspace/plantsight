"""清理後的資料表。事實表保留每一筆可解析的資料，有問題的在 excluded_reason 註明原因；
指標計算一律只用 excluded_reason 為空的列，排除了多少筆、為什麼，都能從資料本身查到。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BusinessGroup(Base):
    __tablename__ = "business_groups"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))


class Plant(Base):
    __tablename__ = "plants"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))


class Line(Base):
    __tablename__ = "lines"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    plant: Mapped[str] = mapped_column(ForeignKey("plants.code"))
    business_group: Mapped[str] = mapped_column(ForeignKey("business_groups.code"))


class Part(Base):
    __tablename__ = "parts"
    part_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    family: Mapped[str] = mapped_column(String(16))
    family_name: Mapped[str] = mapped_column(String(16))
    business_group: Mapped[str] = mapped_column(ForeignKey("business_groups.code"))


class DefectCode(Base):
    __tablename__ = "defect_codes"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    category: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(String(32))


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))  # 敏感
    region: Mapped[str] = mapped_column(String(8))
    tier: Mapped[str] = mapped_column(String(1))


class Supplier(Base):
    __tablename__ = "suppliers"
    supplier_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))  # 敏感
    country: Mapped[str] = mapped_column(String(8))
    material: Mapped[str] = mapped_column(String(16))


class WorkOrder(Base):
    __tablename__ = "work_orders"
    wo_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    plant: Mapped[str] = mapped_column(String(8), index=True)
    line: Mapped[str] = mapped_column(String(16), index=True)
    part_no: Mapped[str] = mapped_column(String(16), index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    complete_date: Mapped[date | None] = mapped_column(Date, index=True)
    input_qty: Mapped[int] = mapped_column(Integer)
    good_qty: Mapped[int] = mapped_column(Integer)
    scrap_qty: Mapped[int] = mapped_column(Integer)
    excluded_reason: Mapped[str | None] = mapped_column(String(32))


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[int] = mapped_column(primary_key=True)
    wo_no: Mapped[str] = mapped_column(String(16), index=True)
    station: Mapped[str] = mapped_column(String(8))
    inspect_date: Mapped[date | None] = mapped_column(Date, index=True)
    inspected_qty: Mapped[int] = mapped_column(Integer)
    defect_qty: Mapped[int] = mapped_column(Integer)
    excluded_reason: Mapped[str | None] = mapped_column(String(32))


class Defect(Base):
    __tablename__ = "defects"
    id: Mapped[int] = mapped_column(primary_key=True)
    wo_no: Mapped[str] = mapped_column(String(16), index=True)
    station: Mapped[str] = mapped_column(String(8))
    defect_code: Mapped[str] = mapped_column(String(16))
    qty: Mapped[int] = mapped_column(Integer)
    excluded_reason: Mapped[str | None] = mapped_column(String(32))


class Shipment(Base):
    __tablename__ = "shipments"
    so_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    plant: Mapped[str] = mapped_column(String(8), index=True)
    part_no: Mapped[str] = mapped_column(String(16))
    customer_id: Mapped[str] = mapped_column(String(8), index=True)
    promised_date: Mapped[date | None] = mapped_column(Date, index=True)
    shipped_date: Mapped[date] = mapped_column(Date)
    qty: Mapped[int] = mapped_column(Integer)
    excluded_reason: Mapped[str | None] = mapped_column(String(32))


class SupplierLot(Base):
    __tablename__ = "supplier_lots"
    lot_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(8), index=True)
    plant: Mapped[str] = mapped_column(String(8))
    received_date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[int] = mapped_column(Integer)
    rejected_qty: Mapped[int] = mapped_column(Integer)
    excluded_reason: Mapped[str | None] = mapped_column(String(32))


class StandardCost(Base):
    __tablename__ = "standard_costs"
    part_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    unit_cost_twd: Mapped[float] = mapped_column(Numeric(10, 2))  # 機密


class Target(Base):
    __tablename__ = "targets"
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)  # 產線代碼或廠區代碼
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    metric: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_value: Mapped[float] = mapped_column(Numeric(8, 4))


class ReviewItem(Base):
    """清理時無法自動決定的資料。附系統建議；核准後建議會真的套用並重算，駁回則維持排除。"""

    __tablename__ = "review_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # kind:鍵值，重跑清理時保留決定
    source: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    keys: Mapped[list] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / approved / rejected
    decided_by: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """登入者與權限範圍。scope_kind：all／business_group／plant／line；範圍內的資料由編譯器在 SQL 層過濾。"""

    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16))
    scope_kind: Mapped[str] = mapped_column(String(16))
    scope_value: Mapped[str | None] = mapped_column(String(16))
    password_hash: Mapped[str | None] = mapped_column(String(128))


class CleaningRun(Base):
    __tablename__ = "cleaning_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    report: Mapped[dict] = mapped_column(JSONB)
