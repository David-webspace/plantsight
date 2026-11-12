"""python -m app.etl.clean：讀 data/raw/*.csv，依 DATA_RULES.md 清理後整批寫入資料庫，產生清理報告。

原則：
- 不推估、不補值。有問題的列保留下來並在 excluded_reason 註明原因，指標只用 excluded_reason 為空的列。
- 系統有把握的修正（例如代碼把 O 打成 0）只產生「建議」，進待確認清單；品保核准後才真的套用。
- 每次都從原始檔重跑，已核准或駁回的決定保存在 review_items，重跑時照樣套用。
"""

import csv
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from app import models as m
from app.auth import DEMO_USERS
from app.config import settings
from app.db import SessionLocal

MASTER = [("business_groups", m.BusinessGroup), ("plants", m.Plant), ("lines", m.Line), ("parts", m.Part),
          ("defect_codes", m.DefectCode), ("customers", m.Customer), ("suppliers", m.Supplier)]
FACTS = [m.Defect, m.Inspection, m.WorkOrder, m.Shipment, m.SupplierLot, m.StandardCost, m.Target]
REASON = {
    "qty_invalid": "數量不是有效整數，整列無法使用",
    "date_invalid": "完工日格式錯誤，不納入以完工日歸屬的指標",
    "date_swapped": "完工日早於開工日（疑似對調），待確認",
    "qty_overflow": "良品數＋報廢數大於投入數，待確認",
    "orphan_wo": "工單號在工單主檔找不到，無法歸屬產線與料號",
    "wo_invalid": "所屬工單數量無效、整列未寫入，無法歸屬",
    "code_typo": "不良代碼不在代碼表，疑似誤植，待確認",
    "code_unknown": "不良代碼不在代碼表，無法判定",
    "rejected_over_qty": "不良數大於進料數",
    "promised_missing": "缺少承諾交期，不計入準交率（出貨量照計）",
}


def read(name: str) -> list[dict]:
    with (settings.data_dir / "raw" / f"{name}.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def norm(v: str) -> str:
    return unicodedata.normalize("NFKC", v).strip().upper()


def to_int(v: str) -> int | None:
    v = v.strip()
    return int(v) if v.isdigit() else None


def to_date(v: str) -> date | None:
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    seen, out = set(), []
    for r in rows:
        key = tuple(r.values())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out, len(rows) - len(out)


def typo_fix(code: str, valid: set[str]) -> str | None:
    """只處理「字母 O 和數字 0、字母 I 和數字 1 打錯」這種有把握的誤植；其他一律不猜。"""
    for a, b in (("O", "0"), ("I", "1")):
        cand = code.replace(a, b)
        if cand != code and cand in valid:
            return cand
    return None


class Run:
    def __init__(self, decisions: dict[str, dict]) -> None:
        self.decisions = decisions  # review_item id → {status, suggestion}
        self.report: dict[str, dict] = {}
        self.reviews: dict[str, dict] = {}

    def review(self, source: str, kind: str, key: str, description: str, suggestion: dict | None) -> str:
        """登記待確認項目；回傳目前狀態（pending／approved／rejected）。"""
        rid = f"{kind}:{key}"
        item = self.reviews.setdefault(rid, {"id": rid, "source": source, "kind": kind, "keys": [], "description": description,
                                             "suggestion": suggestion})
        item["keys"].append(key)
        return self.decisions.get(rid, {}).get("status", "pending")

    def stat(self, table: str, **counts: int) -> None:
        self.report.setdefault(table, Counter()).update({k: v for k, v in counts.items() if v})


def clean_work_orders(run: Run, lines: dict[str, str]) -> list[dict]:
    rows, dup = dedupe(read("work_orders"))
    run.stat("work_orders", raw=len(rows) + dup, duplicate=dup)
    out = []
    for r in rows:
        part, line = norm(r["part_no"]), norm(r["line"])
        if (part, line) != (r["part_no"], r["line"]):
            run.stat("work_orders", normalized=1)
        qty = [to_int(r[k]) for k in ("input_qty", "good_qty", "scrap_qty")]
        if None in qty:
            run.stat("work_orders", excluded_qty_invalid=1)
            continue
        n, good, scrap = qty
        start, done = to_date(r["start_date"]), to_date(r["complete_date"])
        row = {"wo_no": r["wo_no"].strip(), "plant": lines.get(line, norm(r["plant"])), "line": line, "part_no": part,
               "start_date": start, "complete_date": done, "input_qty": n, "good_qty": good, "scrap_qty": scrap,
               "excluded_reason": None}
        if done is None:
            row["excluded_reason"] = "date_invalid"
        elif start and done < start:
            status = run.review("work_orders", "date_swapped", row["wo_no"],
                                f"工單 {row['wo_no']} 開工日 {start}、完工日 {done}，完工早於開工",
                                {"start_date": done.isoformat(), "complete_date": start.isoformat()})
            if status == "approved":
                row["start_date"], row["complete_date"] = done, start
                run.stat("work_orders", corrected_date_swapped=1)
            else:
                row["excluded_reason"] = "date_swapped"
        elif good + scrap > n:
            status = run.review("work_orders", "qty_overflow", row["wo_no"],
                                f"工單 {row['wo_no']} 投入 {n}、良品 {good}、報廢 {scrap}，良品＋報廢多了 {good + scrap - n}",
                                {"scrap_qty": n - good})
            if status == "approved":
                row["scrap_qty"] = n - good
                run.stat("work_orders", corrected_qty_overflow=1)
            else:
                row["excluded_reason"] = "qty_overflow"
        if row["excluded_reason"]:
            run.stat("work_orders", **{f"excluded_{row['excluded_reason']}": 1})
        out.append(row)
    run.stat("work_orders", loaded=len(out))
    return out


def wo_reason(wo: str, loaded: set[str], raw: set[str]) -> str | None:
    if wo in loaded:
        return None
    return "wo_invalid" if wo in raw else "orphan_wo"


def clean_inspections(run: Run, loaded: set[str], raw: set[str]) -> list[dict]:
    rows, dup = dedupe(read("inspections"))
    run.stat("inspections", raw=len(rows) + dup, duplicate=dup)
    out = []
    for r in rows:
        reason = wo_reason(r["wo_no"], loaded, raw)
        if reason:
            run.stat("inspections", **{f"excluded_{reason}": 1})
        out.append({"wo_no": r["wo_no"], "station": r["station"], "inspect_date": to_date(r["inspect_date"]),
                    "inspected_qty": int(r["inspected_qty"]), "defect_qty": int(r["defect_qty"]), "excluded_reason": reason})
    run.stat("inspections", loaded=len(out))
    return out


def clean_defects(run: Run, valid: set[str], loaded: set[str], raw: set[str]) -> list[dict]:
    rows = read("defects")
    run.stat("defects", raw=len(rows))
    out = []
    for r in rows:
        code, reason = norm(r["defect_code"]), wo_reason(r["wo_no"], loaded, raw)
        if reason is None and code not in valid:
            fix = typo_fix(code, valid)
            if fix:
                status = run.review("defects", "code_typo", code, f"不良代碼 {code} 不在代碼表，疑似 {fix} 的誤植",
                                    {"defect_code": fix})
                if status == "approved":
                    code = fix
                    run.stat("defects", corrected_code_typo=1)
                else:
                    reason = "code_typo"
            else:
                run.review("defects", "code_unknown", code, f"不良代碼 {code} 不在代碼表，無法判定", None)
                reason = "code_unknown"
        if reason:
            run.stat("defects", **{f"excluded_{reason}": 1})
        out.append({"wo_no": r["wo_no"], "station": r["station"], "defect_code": code, "qty": int(r["qty"]),
                    "excluded_reason": reason})
    run.stat("defects", loaded=len(out))
    return out


def clean_shipments(run: Run) -> list[dict]:
    rows, dup = dedupe(read("shipments"))
    run.stat("shipments", raw=len(rows) + dup, duplicate=dup)
    out = []
    for r in rows:
        cust = norm(r["customer_id"])
        if cust != r["customer_id"]:
            run.stat("shipments", normalized=1)
        promised = to_date(r["promised_date"])
        if promised is None:
            run.stat("shipments", promised_missing=1)
        out.append({"so_no": r["so_no"], "plant": r["plant"], "part_no": r["part_no"], "customer_id": cust,
                    "promised_date": promised, "shipped_date": to_date(r["shipped_date"]), "qty": int(r["qty"]),
                    "excluded_reason": None})
    run.stat("shipments", loaded=len(out))
    return out


def clean_lots(run: Run) -> list[dict]:
    rows, dup = dedupe(read("supplier_lots"))
    run.stat("supplier_lots", raw=len(rows) + dup, duplicate=dup)
    out = []
    for r in rows:
        qty, rej = int(r["qty"]), int(r["rejected_qty"])
        reason = "rejected_over_qty" if rej > qty else None
        if reason:
            run.stat("supplier_lots", excluded_rejected_over_qty=1)
        out.append({"lot_no": r["lot_no"], "supplier_id": r["supplier_id"], "plant": r["plant"],
                    "received_date": to_date(r["received_date"]), "qty": qty, "rejected_qty": rej, "excluded_reason": reason})
    run.stat("supplier_lots", loaded=len(out))
    return out


def run_cleaning(db: Session) -> dict:
    decisions = {r.id: {"status": r.status} for r in db.scalars(select(m.ReviewItem))}
    run = Run(decisions)
    master = {name: read(name) for name, _ in MASTER}
    lines = {r["code"]: r["plant"] for r in master["lines"]}
    wos = clean_work_orders(run, lines)
    loaded, raw_wos = {w["wo_no"] for w in wos}, {r["wo_no"].strip() for r in read("work_orders")}
    inspections = clean_inspections(run, loaded, raw_wos)
    defects = clean_defects(run, {r["code"] for r in master["defect_codes"]}, loaded, raw_wos)
    shipments = clean_shipments(run)
    lots = clean_lots(run)
    costs = [{**r, "unit_cost_twd": float(r["unit_cost_twd"])} for r in read("standard_costs")]
    targets = [{**r, "target_value": float(r["target_value"])} for r in read("targets")]

    for model in FACTS:
        db.execute(delete(model))
    for _, model in reversed(MASTER):
        db.execute(delete(model))
    for name, model in MASTER:
        db.execute(insert(model), master[name])
    for model, rows in ((m.WorkOrder, wos), (m.Inspection, inspections), (m.Defect, defects), (m.Shipment, shipments),
                        (m.SupplierLot, lots), (m.StandardCost, costs), (m.Target, targets)):
        db.execute(insert(model), rows)

    for username, name, role, kind, value in DEMO_USERS:
        db.execute(text("""INSERT INTO users (username, name, role, scope_kind, scope_value) VALUES (:u, :n, :r, :k, :v)
                           ON CONFLICT (username) DO NOTHING"""), {"u": username, "n": name, "r": role, "k": kind, "v": value})

    for item in run.reviews.values():
        db.execute(text("""
            INSERT INTO review_items (id, source, kind, keys, description, suggestion, status)
            VALUES (:id, :source, :kind, CAST(:keys AS jsonb), :description, CAST(:suggestion AS jsonb), 'pending')
            ON CONFLICT (id) DO UPDATE SET keys = EXCLUDED.keys, description = EXCLUDED.description,
                                           suggestion = EXCLUDED.suggestion
        """), {**item, "keys": json.dumps(item["keys"]), "suggestion": json.dumps(item["suggestion"], ensure_ascii=False)})

    statuses = Counter(decisions.get(rid, {}).get("status", "pending") for rid in run.reviews)
    report = {"tables": {k: dict(v) for k, v in run.report.items()},
              "review": {"items": len(run.reviews), **statuses,
                         "rows": sum(len(i["keys"]) for i in run.reviews.values())}}
    db.add(m.CleaningRun(report=report))
    db.commit()
    (settings.data_dir / "CLEANING_REPORT.md").write_text(render(report))
    return report


TABLE_NAME = {"work_orders": "工單", "inspections": "檢驗", "defects": "不良明細", "shipments": "出貨", "supplier_lots": "進料檢驗"}


def render(report: dict) -> str:
    lines = ["# 資料清理報告", "", "由 `python -m app.etl.clean` 產生；規則見 DATA_RULES.md。", "",
             "| 資料 | 原始筆數 | 重複 | 格式正規化 | 寫入筆數 | 排除（原因） | 核准後修正 |", "|---|---|---|---|---|---|---|"]
    for t, c in report["tables"].items():
        notes = [f"{REASON[k[9:]]}：{v}" for k, v in c.items() if k.startswith("excluded_")]
        if c.get("promised_missing"):
            notes.append(f"{REASON['promised_missing']}：{c['promised_missing']}")
        corrected = sum(v for k, v in c.items() if k.startswith("corrected_"))
        lines.append(f"| {TABLE_NAME[t]} | {c.get('raw', 0)} | {c.get('duplicate', 0)} | {c.get('normalized', 0)} | "
                     f"{c.get('loaded', 0)} | {'；'.join(notes) or '—'} | {corrected or '—'} |")
    r = report["review"]
    lines += ["", f"待確認：{r['items']} 項（涵蓋 {r['rows']} 筆）；未決定 {r.get('pending', 0)}、"
                  f"已核准 {r.get('approved', 0)}、已駁回 {r.get('rejected', 0)}。",
              "", "排除的列仍保留在資料表（excluded_reason 註明原因），指標計算只用未排除的列。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    with SessionLocal() as db:
        report = run_cleaning(db)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
