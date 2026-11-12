"""清理結果要和產生器記下的髒資料筆數一一對上；排除與修正規則要正確。"""

import pytest
from sqlalchemy import text

from app.etl.review import ReviewError, decide


def scalar(db, sql, **p):
    return db.execute(text(sql), p).scalar()


def test_report_matches_injected_dirt(report, manifest):
    t = report["tables"]
    wo, mw = t["work_orders"], manifest["work_orders"]
    assert wo["duplicate"] == mw["duplicate"] and wo["normalized"] == mw["normalized"]
    assert wo["excluded_qty_invalid"] == mw["qty_invalid"] and wo["excluded_date_invalid"] == mw["date_invalid"]
    assert wo["excluded_date_swapped"] == mw["date_swapped"] and wo["excluded_qty_overflow"] == mw["qty_overflow"]
    assert wo["loaded"] == manifest["clean_rows"]["work_orders"] - mw["qty_invalid"]
    assert t["inspections"]["duplicate"] == manifest["inspections"]["duplicate"]
    assert t["inspections"]["excluded_orphan_wo"] == manifest["inspections"]["orphan_wo"]
    assert t["inspections"]["excluded_wo_invalid"] == 3 * mw["qty_invalid"]  # 每張工單 3 站
    assert t["defects"]["excluded_code_typo"] == manifest["defects"]["code_typo"]
    assert t["defects"]["excluded_code_unknown"] == manifest["defects"]["code_unknown"]
    sh = t["shipments"]
    assert (sh["duplicate"], sh["promised_missing"], sh["normalized"]) == tuple(
        manifest["shipments"][k] for k in ("duplicate", "promised_missing", "normalized"))
    assert t["supplier_lots"]["excluded_rejected_over_qty"] == manifest["supplier_lots"]["rejected_over_qty"]
    # 逐筆的工單問題各一項；同一個錯誤代碼合併成一項
    assert report["review"]["items"] == mw["qty_overflow"] + mw["date_swapped"] + 2


def test_clean_rows_are_consistent(report, db):
    assert scalar(db, "SELECT COUNT(*) FROM work_orders WHERE excluded_reason IS NULL AND good_qty + scrap_qty > input_qty") == 0
    assert scalar(db, "SELECT COUNT(*) FROM work_orders WHERE line NOT IN (SELECT code FROM lines)") == 0
    assert scalar(db, "SELECT COUNT(*) FROM work_orders WHERE part_no NOT IN (SELECT part_no FROM parts)") == 0
    assert scalar(db, """SELECT COUNT(*) FROM defects WHERE excluded_reason IS NULL
                         AND defect_code NOT IN (SELECT code FROM defect_codes)""") == 0
    assert scalar(db, "SELECT COUNT(*) FROM shipments WHERE customer_id NOT IN (SELECT customer_id FROM customers)") == 0
    assert scalar(db, "SELECT COUNT(DISTINCT wo_no) FROM work_orders") == scalar(db, "SELECT COUNT(*) FROM work_orders")


def test_approved_suggestions_take_effect(report, db):
    typo = scalar(db, "SELECT id FROM review_items WHERE kind = 'code_typo'")
    after = decide(db, typo, approve=True, by="qa.tester")
    assert after["tables"]["defects"]["corrected_code_typo"] == 40 and "excluded_code_typo" not in after["tables"]["defects"]
    assert scalar(db, "SELECT COUNT(*) FROM defects WHERE defect_code = 'SLD-O1'") == 0

    wo = scalar(db, "SELECT wo_no FROM work_orders WHERE excluded_reason = 'qty_overflow' ORDER BY wo_no LIMIT 1")
    decide(db, f"qty_overflow:{wo}", approve=True, by="qa.tester")
    row = db.execute(text("SELECT input_qty, good_qty, scrap_qty, excluded_reason FROM work_orders WHERE wo_no = :w"), {"w": wo}).one()
    assert row.excluded_reason is None and row.good_qty + row.scrap_qty == row.input_qty

    swapped = scalar(db, "SELECT wo_no FROM work_orders WHERE excluded_reason = 'date_swapped' ORDER BY wo_no LIMIT 1")
    decide(db, f"date_swapped:{swapped}", approve=False, by="qa.tester")
    assert scalar(db, "SELECT excluded_reason FROM work_orders WHERE wo_no = :w", w=swapped) == "date_swapped"


def test_cannot_approve_without_suggestion_or_twice(report, db):
    unknown = scalar(db, "SELECT id FROM review_items WHERE kind = 'code_unknown'")
    with pytest.raises(ReviewError):
        decide(db, unknown, approve=True, by="qa.tester")
    decide(db, unknown, approve=False, by="qa.tester")
    with pytest.raises(ReviewError):
        decide(db, unknown, approve=False, by="qa.tester")
