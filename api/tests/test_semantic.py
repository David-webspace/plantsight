"""語意層：每個指標用 pandas 直接從資料表重算比對；權限矩陣；查詢規格的防呆。"""

import pandas as pd
import pytest
from pydantic import ValidationError

from app.auth import load_principal
from app.db import SessionLocal
from app.semantic.query import AccessDenied, QuerySpec, SpecError, run_query

PERIOD = {"from": "2026-08", "to": "2026-10"}
D0, D1 = pd.Timestamp("2026-08-01"), pd.Timestamp("2026-10-31")


def q(db, user, **spec):
    return run_query(db, QuerySpec.model_validate({"period": PERIOD, **spec}), load_principal(db, user))


def frame(db, sql):
    return pd.read_sql(sql, db.connection())


def by(rows, key, metric):
    return {r[key]: r[metric] for r in rows}


@pytest.fixture
def db():
    # 只讀查詢，不需要 conftest 的 db（那個會重跑清理）
    with SessionLocal() as s:
        yield s


@pytest.fixture(scope="module")
def tables(db_module):
    db = db_module
    wo = frame(db, "SELECT * FROM work_orders WHERE excluded_reason IS NULL")
    wo["complete_date"] = pd.to_datetime(wo["complete_date"])
    wo = wo[(wo.complete_date >= D0) & (wo.complete_date <= D1)]
    ins = frame(db, """SELECT i.*, w.plant FROM inspections i JOIN work_orders w ON w.wo_no = i.wo_no
                       WHERE i.excluded_reason IS NULL AND w.excluded_reason IS NULL""")
    ins["inspect_date"] = pd.to_datetime(ins["inspect_date"])
    ins = ins[(ins.inspect_date >= D0) & (ins.inspect_date <= D1)]
    sh = frame(db, "SELECT * FROM shipments WHERE excluded_reason IS NULL AND promised_date IS NOT NULL")
    sh["promised_date"], sh["shipped_date"] = pd.to_datetime(sh["promised_date"]), pd.to_datetime(sh["shipped_date"])
    sh = sh[(sh.promised_date >= D0) & (sh.promised_date <= D1)]
    lots = frame(db, "SELECT * FROM supplier_lots WHERE excluded_reason IS NULL")
    lots["received_date"] = pd.to_datetime(lots["received_date"])
    lots = lots[(lots.received_date >= D0) & (lots.received_date <= D1)]
    costs = frame(db, "SELECT part_no, period, unit_cost_twd FROM standard_costs")
    return wo, ins, sh, lots, costs


def test_production_metrics_match_direct_calc(db, tables):
    wo, *_, costs = tables
    r = q(db, "gm", metrics=["yield", "input_qty", "scrap_cost"], group_by=["plant"])["rows"]
    g = wo.groupby("plant")
    exp_yield = (g.good_qty.sum() / g.input_qty.sum() * 100).round(1)
    assert by(r, "plant", "yield") == exp_yield.to_dict()
    assert by(r, "plant", "input_qty") == g.input_qty.sum().to_dict()
    wc = wo.assign(period=wo.complete_date.dt.strftime("%Y-%m")).merge(costs, on=["part_no", "period"])
    exp_cost = (wc.scrap_qty * wc.unit_cost_twd.astype(float)).groupby(wc.plant).sum().round()
    assert by(r, "plant", "scrap_cost") == exp_cost.to_dict()


def test_inspection_metrics_match_direct_calc(db, tables):
    _, ins, *_ = tables
    r = q(db, "gm", metrics=["dppm", "fpy"], group_by=["plant"])["rows"]
    g = ins.groupby("plant")
    assert by(r, "plant", "dppm") == (g.defect_qty.sum() / g.inspected_qty.sum() * 1e6).round().to_dict()
    st = ins.groupby(["plant", "station"])[["defect_qty", "inspected_qty"]].sum()
    fpy = (1 - st.defect_qty / st.inspected_qty).groupby(level=0).prod() * 100
    assert by(r, "plant", "fpy") == fpy.round(1).to_dict()
    assert all(row["fpy"] < y for row, y in zip(r, by(q(db, "gm", metrics=["yield"], group_by=["plant"])["rows"], "plant", "yield").values()))


def test_shipment_and_supply_metrics_match_direct_calc(db, tables):
    _, _, sh, lots, _ = tables
    r = q(db, "gm", metrics=["otd"], group_by=["plant"])["rows"]
    exp = (sh.shipped_date <= sh.promised_date).groupby(sh.plant).mean().mul(100).round(1)
    assert by(r, "plant", "otd") == exp.to_dict()
    r = q(db, "gm", metrics=["incoming_reject_rate"], group_by=["supplier"])["rows"]
    g = lots.groupby("supplier_id")
    assert by(r, "supplier", "incoming_reject_rate") == (g.rejected_qty.sum() / g.qty.sum() * 100).round(2).to_dict()


def test_previous_period_and_target_compare(db):
    r = q(db, "gm", metrics=["yield"], group_by=["line"], filters={"plant": ["TW"]},
          period={"from": "2026-09", "to": "2026-10"}, compare={"type": "previous_period"})
    assert r["compare"] == "2026-07～2026-08"
    row = next(x for x in r["rows"] if x["line"] == "TW-SMT1")
    assert row["yield__delta"] == pytest.approx(row["yield"] - row["yield__compare"], abs=0.11)
    t = q(db, "gm", metrics=["yield"], group_by=["line"], period={"grain": "month", "from": "2026-10", "to": "2026-10"},
          compare={"type": "target"})
    assert all(x["yield__compare"] is not None for x in t["rows"])


def test_excluded_rows_are_reported(db):
    r = q(db, "gm", metrics=["otd"], period={"from": "2025-01", "to": "2026-10"})
    assert any("缺承諾交期" in e["reason"] for e in r["excluded"])
    r = q(db, "gm", metrics=["yield"], period={"from": "2025-01", "to": "2026-10"})
    assert any(e["reason"] == "完工日格式錯誤" for e in r["excluded"])


# ── 權限 ──────────────────────────────────────────────────

def test_line_lead_sees_only_own_line(db):
    r = q(db, "tw.smt1.lead", metrics=["yield"], group_by=["line"])
    assert [x["line"] for x in r["rows"]] == ["TW-SMT1"]
    for spec, msg in [({"filters": {"line": ["VN-SMT2"]}}, "不在你的範圍"), ({"filters": {"plant": ["VN"]}}, "不在你的範圍")]:
        with pytest.raises(AccessDenied, match=msg):
            q(db, "tw.smt1.lead", metrics=["yield"], **spec)
    with pytest.raises(AccessDenied, match="沒有產線歸屬"):
        q(db, "tw.smt1.lead", metrics=["otd"])
    with pytest.raises(AccessDenied, match="機密"):
        q(db, "tw.smt1.lead", metrics=["scrap_cost"])


def test_plant_and_group_scopes(db):
    assert {x["plant"] for x in q(db, "tw.manager", metrics=["yield", "otd"], group_by=["plant"])["rows"]} == {"TW"}
    with pytest.raises(AccessDenied):
        q(db, "tw.manager", metrics=["yield"], filters={"line": ["VN-SMT2"]})
    lines = {x["line"] for x in q(db, "pwr.head", metrics=["yield"], group_by=["line"])["rows"]}
    assert lines == {"TW-SMT1", "TW-ASY1", "CN-SMT1", "CN-ASY1", "VN-SMT2", "VN-ASY1"}
    with pytest.raises(AccessDenied, match="沒有事業群歸屬"):
        q(db, "pwr.head", metrics=["incoming_reject_rate"])


def test_role_limits_and_sensitive_names(db):
    with pytest.raises(AccessDenied):
        q(db, "qa.engineer", metrics=["otd"])
    with pytest.raises(AccessDenied, match="機密"):
        q(db, "qa.engineer", metrics=["scrap_cost"])
    assert "supplier_name" in q(db, "qa.engineer", metrics=["incoming_reject_rate"], group_by=["supplier"])["rows"][0]
    assert q(db, "finance.analyst", metrics=["scrap_cost"])["rows"]
    fin = q(db, "finance.analyst", metrics=["shipped_qty"], group_by=["customer"])["rows"]
    assert fin and "customer_name" not in fin[0]
    assert "customer_name" in q(db, "gm", metrics=["shipped_qty"], group_by=["customer"])["rows"][0]
    with pytest.raises(AccessDenied):
        q(db, "sysadmin", metrics=["yield"])


# ── 規格防呆 ──────────────────────────────────────────────

def test_spec_errors_are_specific(db):
    with pytest.raises(SpecError, match="沒有這些指標"):
        q(db, "gm", metrics=["profit"])
    with pytest.raises(SpecError, match="不能依產線"):
        q(db, "gm", metrics=["otd"], group_by=["line"])
    with pytest.raises(SpecError, match="total"):
        q(db, "gm", metrics=["yield"], period={"grain": "month", **PERIOD}, compare={"type": "previous_period"})
    with pytest.raises(SpecError, match="廠區沒有"):
        q(db, "gm", metrics=["yield"], filters={"plant": ["TW'; DROP TABLE work_orders; --"]})
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"metrics": ["yield"], "period": PERIOD, "sql": "SELECT 1"})
    assert q(db, "gm", metrics=["yield"], filters={"line": ["tw-smt1"]})["rows"]  # 大小寫不拘
