"""評測的標準答案：用 pandas 直接從清理後的資料表重算，完全不經過被測的語意層與編譯器。

兩邊各自實作同一套指標定義；若評測發現不一致，代表其中一邊算錯，不會出現「拿被測程式驗證自己」的情況。
"""

from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine

from app.config import settings

DECIMALS = {"yield": 1, "fpy": 1, "dppm": 0, "otd": 1, "incoming_reject_rate": 2, "scrap_cost": 0, "defect_qty": 0,
            "input_qty": 0, "shipped_qty": 0}


@lru_cache(maxsize=1)
def tables() -> dict[str, pd.DataFrame]:
    eng = create_engine(settings.database_url)
    q = lambda sql: pd.read_sql(sql, eng)  # noqa: E731
    lines = q("SELECT code AS line, business_group FROM lines")
    parts = q("SELECT part_no, family, business_group AS part_bg FROM parts")
    wo = q("SELECT * FROM work_orders WHERE excluded_reason IS NULL").merge(lines, on="line").merge(parts, on="part_no")
    wo["month"] = pd.to_datetime(wo.complete_date).dt.strftime("%Y-%m")
    ins = q("SELECT wo_no, station, inspect_date, inspected_qty, defect_qty FROM inspections WHERE excluded_reason IS NULL")
    ins = ins.merge(wo[["wo_no", "plant", "line", "business_group", "family", "part_no"]], on="wo_no")
    ins["month"] = pd.to_datetime(ins.inspect_date).dt.strftime("%Y-%m")
    dfc = q("SELECT d.wo_no, d.station, d.qty, c.category, d.defect_code FROM defects d JOIN defect_codes c ON c.code = d.defect_code "
            "WHERE d.excluded_reason IS NULL")
    dfc = dfc.merge(ins[["wo_no", "station", "month", "plant", "line", "business_group", "family"]], on=["wo_no", "station"])
    sh = q("SELECT * FROM shipments WHERE excluded_reason IS NULL").merge(parts, on="part_no")
    sh["month"] = pd.to_datetime(sh.promised_date).dt.strftime("%Y-%m")
    sh["ship_month"] = pd.to_datetime(sh.shipped_date).dt.strftime("%Y-%m")
    lots = q("SELECT * FROM supplier_lots WHERE excluded_reason IS NULL")
    lots["month"] = pd.to_datetime(lots.received_date).dt.strftime("%Y-%m")
    costs = q("SELECT part_no, period AS month, unit_cost_twd FROM standard_costs")
    costs["unit_cost_twd"] = costs.unit_cost_twd.astype(float)
    targets = q("SELECT scope, period AS month, metric, target_value FROM targets")
    return {"wo": wo, "ins": ins, "def": dfc, "sh": sh, "lots": lots, "costs": costs, "targets": targets}


# 維度名稱 → 資料表欄位
COLS = {"plant": "plant", "line": "line", "business_group": "business_group", "product_family": "family",
        "defect_category": "category", "customer": "customer_id", "supplier": "supplier_id"}


def _frame(metric: str, filters: dict, period: tuple[str, str]) -> pd.DataFrame:
    t = tables()
    df = {"yield": t["wo"], "scrap_cost": t["wo"], "input_qty": t["wo"], "dppm": t["ins"], "fpy": t["ins"],
          "defect_qty": t["def"], "otd": t["sh"], "shipped_qty": t["sh"], "incoming_reject_rate": t["lots"]}[metric]
    month = "ship_month" if metric == "shipped_qty" else "month"
    df = df[(df[month] >= period[0]) & (df[month] <= period[1])]
    if metric == "otd":
        df = df[df.promised_date.notna()]
    for dim, values in (filters or {}).items():
        df = df[df[COLS[dim]].isin(values)]
    return df


def _value(metric: str, df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    if metric == "yield":
        return df.good_qty.sum() / df.input_qty.sum() * 100
    if metric == "input_qty":
        return float(df.input_qty.sum())
    if metric == "dppm":
        return df.defect_qty.sum() / df.inspected_qty.sum() * 1e6
    if metric == "fpy":
        st = df.groupby("station")[["defect_qty", "inspected_qty"]].sum()
        return float((1 - st.defect_qty / st.inspected_qty).prod() * 100)
    if metric == "defect_qty":
        return float(df.qty.sum())
    if metric == "otd":
        return float((pd.to_datetime(df.shipped_date) <= pd.to_datetime(df.promised_date)).mean() * 100)
    if metric == "shipped_qty":
        return float(df.qty.sum())
    if metric == "incoming_reject_rate":
        return df.rejected_qty.sum() / df.qty.sum() * 100
    if metric == "scrap_cost":
        m = df.merge(tables()["costs"], on=["part_no", "month"])
        return float((m.scrap_qty * m.unit_cost_twd).sum())
    raise KeyError(metric)


def value(metric: str, period: tuple[str, str], filters: dict | None = None) -> float | None:
    return _value(metric, _frame(metric, filters or {}, period))


def by(metric: str, group: str, period: tuple[str, str], filters: dict | None = None) -> dict[str, float]:
    df = _frame(metric, filters or {}, period)
    return {str(k): _value(metric, g) for k, g in df.groupby(COLS[group])}


def shift(period: tuple[str, str], months: int) -> tuple[str, str]:
    def add(ym: str) -> str:
        y, m = map(int, ym.split("-"))
        t = y * 12 + m - 1 + months
        return f"{t // 12}-{t % 12 + 1:02d}"
    return add(period[0]), add(period[1])


def length(period: tuple[str, str]) -> int:
    (y0, m0), (y1, m1) = (map(int, p.split("-")) for p in period)
    return (y1 - y0) * 12 + m1 - m0 + 1


def target(metric: str, scope: str, period: tuple[str, str]) -> float | None:
    t = tables()["targets"]
    t = t[(t.metric == metric) & (t.scope == scope) & (t.month >= period[0]) & (t.month <= period[1])]
    return float(t.target_value.mean() * 100) if len(t) else None


@dataclass
class Decomposition:
    change: float
    mix: float
    rate: float
    top: str


def decompose(metric: str, group: str, base: tuple[str, str], current: tuple[str, str], filters: dict | None = None) -> Decomposition:
    """組合／比率效應，獨立用 pandas 重算（分母：良率用投入數、準交率用有交期的訂單數）。"""
    num, den = {"yield": ("good_qty", "input_qty"), "otd": ("on_time", "one")}[metric]

    def parts(period):
        df = _frame(metric, filters or {}, period).copy()
        if metric == "otd":
            df["on_time"] = (pd.to_datetime(df.shipped_date) <= pd.to_datetime(df.promised_date)).astype(float)
            df["one"] = 1.0
        g = df.groupby(COLS[group])[[num, den]].sum()
        return g[num] / g[den] * 100, g[den] / g[den].sum(), df[num].sum() / df[den].sum() * 100

    r0, w0, t0 = parts(base)
    r1, w1, t1 = parts(current)
    keys = sorted(set(r0.index) | set(r1.index))
    effects = {}
    mix = rate = 0.0
    for k in keys:
        rr0 = r0.get(k, t0)
        m = (w1.get(k, 0) - w0.get(k, 0)) * (rr0 - t0)
        r = w1.get(k, 0) * (r1[k] - rr0) if k in r1 else 0.0
        mix, rate = mix + m, rate + r
        effects[k] = m + r
    top = max(effects, key=lambda k: abs(effects[k]))
    return Decomposition(t1 - t0, mix, rate, str(top))
