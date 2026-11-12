"""產生器埋的三個事件要真的出現在清理後的資料裡，之後「為什麼」類問題才有標準答案。"""

from sqlalchemy import text

YIELD = """SELECT SUM(good_qty)::float / SUM(input_qty) FROM work_orders w JOIN parts p ON p.part_no = w.part_no
           WHERE excluded_reason IS NULL AND to_char(complete_date, 'YYYY-MM') BETWEEN :a AND :b {extra}"""


def y(db, a, b, extra="", **p):
    return db.execute(text(YIELD.format(extra=extra)), {"a": a, "b": b, **p}).scalar()


def test_e1_vn_smt2_solder_problem(report, db):
    line = "AND line = 'VN-SMT2'"
    assert y(db, "2026-05", "2026-07", line) - y(db, "2026-08", "2026-10", line) > 0.015
    share = """SELECT SUM(d.qty) FILTER (WHERE c.category = '焊接')::float / SUM(d.qty)
               FROM defects d JOIN work_orders w ON w.wo_no = d.wo_no JOIN defect_codes c ON c.code = d.defect_code
               WHERE d.excluded_reason IS NULL AND d.station = 'ICT' AND w.line = 'VN-SMT2'
                 AND to_char(w.complete_date, 'YYYY-MM') BETWEEN :a AND :b"""
    assert db.execute(text(share), {"a": "2026-08", "b": "2026-10"}).scalar() > \
        db.execute(text(share), {"a": "2026-05", "b": "2026-07"}).scalar() + 0.15
    rate = """SELECT SUM(rejected_qty)::float / SUM(qty) FROM supplier_lots
              WHERE excluded_reason IS NULL AND supplier_id = :s AND plant = 'VN'"""
    assert db.execute(text(rate), {"s": "S09"}).scalar() > 0.02 > db.execute(text(rate), {"s": "S01"}).scalar()


def test_e2_tw_mix_shift_not_product_yield(report, db):
    tw = "AND line IN ('TW-SMT1', 'TW-ASY1')"
    assert y(db, "2026-06", "2026-08", tw) - y(db, "2026-09", "2026-10", tw) > 0.008
    for fam in ("server_psu", "adapter"):
        f = tw + " AND p.family = :fam"
        assert abs(y(db, "2026-06", "2026-08", f, fam=fam) - y(db, "2026-09", "2026-10", f, fam=fam)) < 0.005


def test_e3_cn_c07_pull_in_hurts_otd(report, db):
    otd = """SELECT AVG(CASE WHEN shipped_date <= promised_date THEN 1.0 ELSE 0 END) FROM shipments
             WHERE excluded_reason IS NULL AND promised_date IS NOT NULL AND plant = 'CN' AND customer_id = 'C07'
               AND to_char(promised_date, 'YYYY-MM') BETWEEN :a AND :b"""
    assert db.execute(text(otd), {"a": "2026-10", "b": "2026-10"}).scalar() < 0.3
    assert db.execute(text(otd), {"a": "2026-01", "b": "2026-09"}).scalar() > 0.85
