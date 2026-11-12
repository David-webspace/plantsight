"""查詢規格 → 參數化 SQL → 結果。

模型唯一能下的查詢就是 QuerySpec。指標、維度只能是語意層裡的代碼；篩選值一律走參數綁定；
權限條件由這裡依登入者注入，模型碰不到。以唯讀交易執行並設逾時。
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import Principal
from app.catalog import BUSINESS_GROUPS, CUSTOMERS, DEFECT_CODES, FAMILIES, LINES, PARTS, PLANTS, STATIONS, SUPPLIERS
from app.semantic.catalog import Fact, Metric, catalog

MAX_ROWS = 200
TIMEOUT_MS = 5000
PERIOD_EXPR = {"month": "to_char({d}, 'YYYY-MM')", "quarter": "to_char({d}, 'YYYY-\"Q\"Q')", "year": "to_char({d}, 'YYYY')"}
# 各維度的合法值：篩選值先對照這裡（不分大小寫），對不上就請模型改，不會拿去查出一張空表
VALUES = {
    "plant": list(PLANTS), "line": list(LINES), "business_group": list(BUSINESS_GROUPS), "product_family": list(FAMILIES),
    "part_no": list(PARTS), "station": list(STATIONS), "defect_category": sorted({c for c, _ in DEFECT_CODES.values()}),
    "defect_code": list(DEFECT_CODES), "customer": list(CUSTOMERS), "supplier": list(SUPPLIERS),
    "material": sorted({m for _, _, m in SUPPLIERS.values()}),
}
REASON_LABEL = {
    "date_invalid": "完工日格式錯誤", "date_swapped": "完工日早於開工日（待確認）", "qty_overflow": "良品＋報廢大於投入（待確認）",
    "orphan_wo": "工單號找不到", "wo_invalid": "所屬工單無效", "code_typo": "不良代碼疑似誤植（待確認）",
    "code_unknown": "不良代碼無法判定", "rejected_over_qty": "不良數大於進料數", "promised_missing": "缺承諾交期",
}


class SpecError(ValueError):
    """查詢規格本身有問題（代碼不存在、組合不合理），訊息可以直接給模型修正。"""


class AccessDenied(PermissionError):
    """登入者沒有權限；訊息說明是哪個條件，不悄悄回傳部分結果。"""


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    grain: Literal["month", "quarter", "year", "total"] = "total"
    start: str = Field(alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    end: str = Field(alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

    @field_validator("start", "end", mode="before")
    @classmethod
    def _month(cls, v):
        # 模型常給到日（2026-10-01）；期間以月為單位，只取年月
        return v[:7] if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-" else v


class Compare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["previous_period", "same_period_last_year", "target"]


class Sort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str
    order: Literal["asc", "desc"] = "desc"


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=4)
    group_by: list[str] = Field(default_factory=list, max_length=2)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    period: Period
    compare: Compare | None = None
    sort: Sort | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("metrics", "group_by", mode="before")
    @classmethod
    def _lower(cls, v):
        return [x.strip().lower() for x in v] if isinstance(v, list) else v

    @field_validator("filters")
    @classmethod
    def _filters(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not vals or len(vals) > 20 or any(len(x) > 32 for x in vals) for vals in v.values()):
            raise ValueError("每個篩選條件需有 1 到 20 個值")
        return v


def month_range(start: str, end: str, shift: int = 0) -> tuple[date, date]:
    def add(ym: str, n: int) -> tuple[int, int]:
        y, m = map(int, ym.split("-"))
        t = y * 12 + m - 1 + n
        return t // 12, t % 12 + 1
    (y0, m0), (y1, m1) = add(start, shift), add(end, shift)
    last = date(y1 + (m1 == 12), m1 % 12 + 1, 1)
    return date(y0, m0, 1), date.fromordinal(last.toordinal() - 1)


def months_between(start: str, end: str) -> int:
    (y0, m0), (y1, m1) = (map(int, x.split("-")) for x in (start, end))
    return (y1 - y0) * 12 + (m1 - m0) + 1


# ── 驗證與權限 ─────────────────────────────────────────────

def check(spec: QuerySpec, p: Principal) -> list[Metric]:
    cat = catalog()
    policy = p.policy
    if not policy.facts:
        raise AccessDenied(f"{policy.label}沒有查詢生產資料的權限")
    unknown = [m for m in spec.metrics if m not in cat.metrics]
    if unknown:
        raise SpecError(f"沒有這些指標：{'、'.join(unknown)}；可用：{'、'.join(cat.metrics)}")
    metrics = [cat.metrics[m] for m in spec.metrics]
    if spec.period.start > spec.period.end:
        raise SpecError("期間的起始月份不能晚於結束月份")
    if months_between(spec.period.start, spec.period.end) > 24:
        raise SpecError("期間最多 24 個月")
    for d in [*spec.group_by, *spec.filters]:
        if d not in cat.dims:
            raise SpecError(f"沒有維度 {d}；可用：{'、'.join(cat.dims)}")
    for m in metrics:
        fact = cat.facts[m.fact]
        bad = [d for d in [*spec.group_by, *spec.filters] if d not in fact.dims]
        if bad:
            raise SpecError(f"{m.label}不能依{'、'.join(cat.dims[d]['label'] for d in bad)}分組或篩選")
        if m.fact not in policy.facts:
            raise AccessDenied(f"{policy.label}不能查{m.label}")
        if m.confidential and not policy.confidential:
            raise AccessDenied(f"{m.label}屬機密資料，{policy.label}不能查")
        if p.scope_kind != "all" and p.scope_kind not in fact.scope:
            raise AccessDenied(f"{m.label}的資料沒有{_scope_dim_label(p.scope_kind)}歸屬，無法限定在你的範圍（{p.scope_text}）內查詢")
    for dim, values in spec.filters.items():
        canon = {v.lower(): v for v in VALUES[dim]}
        bad = [x for x in values if x.strip().lower() not in canon]
        if bad:
            raise SpecError(f"{cat.dims[dim]['label']}沒有 {'、'.join(bad)}；可用：{'、'.join(VALUES[dim])}")
        spec.filters[dim] = [canon[x.strip().lower()] for x in values]
    _check_filters_in_scope(spec, p)
    if spec.compare:
        if spec.compare.type == "target":
            for m in metrics:
                if not m.target:
                    raise SpecError(f"{m.label}沒有訂目標")
                if spec.group_by != [m.target["scope"]]:
                    raise SpecError(f"{m.label}的目標只訂到{cat.dims[m.target['scope']]['label']}，請依{cat.dims[m.target['scope']]['label']}分組")
        elif spec.period.grain != "total":
            raise SpecError("和前期或去年同期比較時，期間請用 total（整段期間合計）")
    if spec.sort and spec.sort.by not in [*spec.metrics, *spec.group_by]:
        raise SpecError("排序欄位必須是查詢的指標或分組維度")
    return metrics


def _scope_dim_label(kind: str) -> str:
    return catalog().dims[kind]["label"]


def _check_filters_in_scope(spec: QuerySpec, p: Principal) -> None:
    """篩選值超出範圍時直接拒絕並說明，而不是回一張空表讓人以為沒有資料。"""
    if p.scope_kind == "all":
        return
    v = p.scope_value
    if p.scope_kind == "plant":
        allowed = {"plant": {v}, "line": {ln for ln, (pl, _, _) in LINES.items() if pl == v}}
    elif p.scope_kind == "line":
        allowed = {"line": {v}, "plant": {LINES[v][0]}, "business_group": {LINES[v][1]}}
    else:
        allowed = {"business_group": {v}, "line": {ln for ln, (_, bg, _) in LINES.items() if bg == v}}
    for dim, values in spec.filters.items():
        if dim in allowed and not set(values) <= allowed[dim]:
            outside = sorted(set(values) - allowed[dim])
            raise AccessDenied(f"{'、'.join(outside)} 不在你的範圍（{p.scope_text}）內")


# ── 編譯 ───────────────────────────────────────────────────

@dataclass
class Compiled:
    sql: str
    params: dict
    keys: list[str]
    metrics: list[Metric]


def _where(fact: Fact, m: Metric, spec: QuerySpec, p: Principal, d0: date, d1: date, params: dict) -> list[str]:
    conds = [f"{m.date} BETWEEN :d0 AND :d1"]
    params.update(d0=d0, d1=d1)
    if p.scope_kind != "all":
        conds.append(f"{fact.scope[p.scope_kind]} = :scope")
        params["scope"] = p.scope_value
    for i, (dim, values) in enumerate(sorted(spec.filters.items())):
        conds.append(f"{fact.dims[dim]} = ANY(:f{i})")
        params[f"f{i}"] = values
    return conds


def compile_group(spec: QuerySpec, p: Principal, metrics: list[Metric], d0: date, d1: date) -> Compiled:
    """同一個資料來源、同一種計算方式的指標合成一段 SQL。"""
    m0 = metrics[0]
    fact = catalog().facts[m0.fact]
    params: dict = {}
    keys = list(spec.group_by)
    select = [f"{fact.dims[d]} AS {d}" for d in spec.group_by]
    if spec.period.grain != "total":
        select.append(f"{PERIOD_EXPR[spec.period.grain].format(d=m0.date)} AS period")
        keys.append("period")
    where = [fact.valid, *_where(fact, m0, spec, p, d0, d1, params)]
    joins = " ".join(dict.fromkeys(m.join for m in metrics if m.join))
    group = f" GROUP BY {', '.join(str(i + 1) for i in range(len(keys)))}" if keys else ""
    if m0.kind == "fpy":
        inner_keys = [*select, "i.station AS _station"]
        inner = (f"SELECT {', '.join(inner_keys)}, SUM(i.defect_qty) AS d, SUM(i.inspected_qty) AS n "
                 f"FROM {fact.from_} {joins} WHERE {' AND '.join(where)} GROUP BY {', '.join(str(i + 1) for i in range(len(inner_keys)))}")
        outer_keys = ", ".join(keys)
        sql = (f"SELECT {outer_keys + ', ' if keys else ''}EXP(SUM(LN(GREATEST(1 - d::float / NULLIF(n, 0), 1e-9)))) AS {m0.key} "
               f"FROM ({inner}) x{' GROUP BY ' + outer_keys if keys else ''}")
        return Compiled(sql, params, keys, metrics)
    aggs = []
    for m in metrics:
        if m.kind == "sum":
            aggs.append(f"SUM({m.expr}) AS {m.key}")
        else:
            aggs.append(f"SUM({m.num}) AS {m.key}__num, SUM({m.den}) AS {m.key}__den")
    sql = f"SELECT {', '.join([*select, *aggs])} FROM {fact.from_} {joins} WHERE {' AND '.join(where)}{group}"
    return Compiled(sql, params, keys, metrics)


def _run(db: Session, c: Compiled) -> list[dict]:
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_MS}"))
    rows = db.execute(text(c.sql), c.params).mappings().all()
    out = []
    for r in rows:
        row = {k: r[k] for k in c.keys}
        for m in c.metrics:
            if m.kind == "sum":
                row[m.key] = float(r[m.key] or 0)
            elif m.kind == "fpy":
                row[m.key] = float(r[m.key]) * m.scale if r[m.key] is not None else None
            else:
                den = float(r[f"{m.key}__den"] or 0)
                row[m.key] = float(r[f"{m.key}__num"] or 0) / den * m.scale if den else None
        out.append(row)
    return out


def _fetch(db: Session, spec: QuerySpec, p: Principal, metrics: list[Metric], d0: date, d1: date) -> tuple[list[str], dict]:
    groups: dict[tuple, list[Metric]] = defaultdict(list)
    for m in metrics:
        groups[(m.fact, m.kind == "fpy", m.date)].append(m)
    merged: dict[tuple, dict] = {}
    keys: list[str] = []
    try:
        for ms in groups.values():
            c = compile_group(spec, p, ms, d0, d1)
            keys = c.keys
            for row in _run(db, c):
                k = tuple(row[x] for x in keys)
                merged.setdefault(k, {x: row[x] for x in keys}).update({m.key: row[m.key] for m in ms})
            db.rollback()  # 每段查詢各自一個唯讀交易
    finally:
        db.rollback()
    return keys, merged


def _excluded(db: Session, spec: QuerySpec, p: Principal, metrics: list[Metric], d0: date, d1: date) -> list[dict]:
    """這次查詢範圍內被清理排除的筆數，回答時要說明。日期本身無效的列無法歸屬期間，一併列出。"""
    out = []
    try:
        for fact_key in dict.fromkeys(m.fact for m in metrics):
            fact = catalog().facts[fact_key]
            m = next(x for x in metrics if x.fact == fact_key)
            params: dict = {}
            conds = _where(fact, m, spec, p, d0, d1, params)
            conds[0] = f"({m.date} BETWEEN :d0 AND :d1 OR {m.date} IS NULL)"
            sql = (f"SELECT {fact.reason} AS reason, COUNT(*) AS n FROM {fact.from_} "
                   f"WHERE NOT ({fact.valid}) AND {' AND '.join(conds)} GROUP BY 1")
            db.execute(text("SET TRANSACTION READ ONLY"))
            out += [{"source": fact_key, "reason": REASON_LABEL.get(r.reason, r.reason), "rows": r.n}
                    for r in db.execute(text(sql), params)]
            db.rollback()
        for m in metrics:
            if m.missing:
                fact = catalog().facts[m.fact]
                params = {}
                conds = _where(fact, m, spec, p, d0, d1, params)
                conds[0] = f"{m.missing['date']} BETWEEN :d0 AND :d1"
                sql = f"SELECT COUNT(*) FROM {fact.from_} WHERE {fact.valid} AND {m.missing['where']} AND {' AND '.join(conds)}"
                n = db.execute(text(sql), params).scalar()
                if n:
                    out.append({"source": m.fact, "reason": f"{REASON_LABEL[m.missing['label']]}（不計入{m.label}）", "rows": n})
                db.rollback()
    finally:
        db.rollback()
    return out


def _targets(db: Session, spec: QuerySpec, m: Metric, d0: date, d1: date) -> dict[tuple, float]:
    sql = """SELECT scope, AVG(target_value) * :scale AS tv FROM targets
             WHERE metric = :metric AND period BETWEEN :a AND :b GROUP BY scope"""
    if spec.period.grain == "month":
        sql = """SELECT scope, period, target_value * :scale AS tv FROM targets
                 WHERE metric = :metric AND period BETWEEN :a AND :b"""
    rows = db.execute(text(sql), {"scale": m.scale, "metric": m.target["metric"], "a": f"{d0:%Y-%m}", "b": f"{d1:%Y-%m}"})
    return {(r.scope, r.period) if spec.period.grain == "month" else (r.scope,): float(r.tv) for r in rows}


def run_query(db: Session, spec: QuerySpec, p: Principal) -> dict:
    metrics = check(spec, p)
    cat = catalog()
    d0, d1 = month_range(spec.period.start, spec.period.end)
    keys, rows = _fetch(db, spec, p, metrics, d0, d1)

    compare_label = None
    if spec.compare and spec.compare.type != "target":
        n = months_between(spec.period.start, spec.period.end)
        shift = -n if spec.compare.type == "previous_period" else -12
        c0, c1 = month_range(spec.period.start, spec.period.end, shift)
        _, base = _fetch(db, spec, p, metrics, c0, c1)
        compare_label = f"{c0:%Y-%m}～{c1:%Y-%m}"
        for k, row in rows.items():
            for m in metrics:
                prev = base.get(k, {}).get(m.key)
                row[f"{m.key}__compare"] = prev
                row[f"{m.key}__delta"] = None if prev is None or row[m.key] is None else row[m.key] - prev
    elif spec.compare:
        for m in metrics:
            t = _targets(db, spec, m, d0, d1)
            for k, row in rows.items():
                row[f"{m.key}__compare"] = t.get(k)
                row[f"{m.key}__delta"] = None if t.get(k) is None or row[m.key] is None else row[m.key] - t[k]
        compare_label = "目標（期間內各月目標平均）" if spec.period.grain == "total" else "當月目標"

    out = list(rows.values())
    if spec.sort:
        out.sort(key=lambda r: (r[spec.sort.by] is None, r[spec.sort.by]), reverse=spec.sort.order == "desc")
    else:
        out.sort(key=lambda r: tuple(str(r[k]) for k in keys))
    truncated = len(out) > spec.limit
    out = out[:spec.limit]

    names = _names(db, spec, p, out)
    columns = [{"key": k, "label": cat.dims[k]["label"] if k != "period" else "期間"} for k in keys]
    for d, attr in names.items():
        columns.insert([c["key"] for c in columns].index(d) + 1, {"key": f"{d}_name", "label": f"{cat.dims[d]['label']}名稱"})
        for r in out:
            r[f"{d}_name"] = attr.get(r[d])
    for m in metrics:
        columns.append({"key": m.key, "label": m.label, "unit": m.unit, "decimals": m.decimals})
        if spec.compare:
            columns.append({"key": f"{m.key}__compare", "label": f"{m.label}（比較）", "unit": m.unit, "decimals": m.decimals})
            columns.append({"key": f"{m.key}__delta", "label": f"{m.label}差異", "unit": "百分點" if m.unit == "%" else m.unit,
                            "decimals": m.decimals})
        for r in out:
            for suffix in ("", "__compare", "__delta"):
                if r.get(m.key + suffix) is not None:
                    r[m.key + suffix] = round(r[m.key + suffix], m.decimals) if m.decimals else round(r[m.key + suffix])

    return {
        "columns": columns, "rows": out, "truncated": truncated,
        "period": f"{d0}～{d1}", "grain": spec.period.grain, "compare": compare_label,
        "scope": p.scope_text, "filters": spec.filters,
        "metrics": [{"key": m.key, "label": m.label, "unit": m.unit, "basis": m.basis, "decimals": m.decimals} for m in metrics],
        "excluded": _excluded(db, spec, p, metrics, d0, d1),
    }


def _names(db: Session, spec: QuerySpec, p: Principal, rows: list[dict]) -> dict[str, dict]:
    """客戶、供應商名稱是敏感資料：有權限才附上名稱，否則只給代碼。"""
    out = {}
    for d in spec.group_by:
        table = catalog().dims[d].get("names")
        if table and table in p.policy.names:
            id_col = "customer_id" if table == "customers" else "supplier_id"
            out[d] = dict(db.execute(text(f"SELECT {id_col}, name FROM {table}")).all())
            db.rollback()
    return out
