"""python -m app.seed.generate：產生示範用的原始 CSV（含刻意放入的髒資料）到 data/raw/。

固定亂數種子，每次產生的內容完全相同。埋了三個「有故事」的事件，讓「為什麼」類問題有標準答案：
  E1 越南廠 VN-SMT2 自 2026-08 改用新錫膏供應商 S09：焊接不良上升、良率下降，進料不良率同時上升。
  E2 台灣廠電源產線 2026-09 起伺服器電源的工單比重由 25% 提高到 70%：整體良率下降，但各產品良率持平（組合效應）。
  E3 東莞廠客戶 C07 在 2026-10 把交期提前 7 天：出貨照原計畫，準交率因此下降。
髒資料的種類與筆數寫進 data/raw/manifest.json，測試用來核對清理報告。
"""

import calendar
import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from app.catalog import CUSTOMERS, DEFECT_CODES, FAMILIES, LINES, PARTS, PLANTS, SUPPLIERS, BUSINESS_GROUPS
from app.config import settings

SEED = 20261110
MONTHS = [(y, m) for y in (2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 10)]

FAMILY_CUSTOMERS = {"server_psu": ["C01", "C04", "C09"], "adapter": ["C05", "C07", "C10", "C12"],
                    "led_module": ["C03", "C08", "C12"], "auto_lamp": ["C02", "C06", "C11"]}
# 各站不良類別的比重
STATION_CATEGORY = {"ICT": {"焊接": 0.55, "零件": 0.45}, "FCT": {"功能": 0.8, "零件": 0.2}, "OQC": {"外觀": 1.0}}
FAMILY_FUNCTION_CODES = {"server_psu": ["FUN-01", "FUN-02"], "adapter": ["FUN-01", "FUN-02"],
                         "led_module": ["FUN-03", "FUN-04"], "auto_lamp": ["FUN-03", "FUN-04"]}
MATERIAL_SUPPLIERS = {"錫膏": "S01", "PCB": "S02", "IC": "S03", "被動元件": "S04", "LED 晶粒": "S05",
                      "連接器": "S06", "外殼": "S08", "磁性元件": "S10"}


def period(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def e1(line: str, y: int, m: int) -> bool:
    return line == "VN-SMT2" and (y, m) >= (2026, 8)


def e2(line: str, y: int, m: int) -> bool:
    return line in ("TW-SMT1", "TW-ASY1") and (y, m) >= (2026, 9)


def binomial_good(rng: random.Random, n: int, p: float) -> int:
    sd = math.sqrt(n * p * (1 - p))
    return max(0, min(n, round(n * p + rng.gauss(0, sd))))


def split(rng: random.Random, total: int, parts: int) -> list[int]:
    cuts = sorted(rng.randint(0, total) for _ in range(parts - 1))
    return [b - a for a, b in zip([0, *cuts], [*cuts, total])]


def generate(rng: random.Random) -> dict[str, list[dict]]:
    part_offset = {p: rng.uniform(-0.006, 0.006) for p in PARTS}
    line_offset = {ln: rng.uniform(-0.004, 0.003) for ln in LINES}
    wos, inspections, defects, shipments, lots, costs, targets = [], [], [], [], [], [], []
    wo_seq, so_seq, lot_seq = 1000001, 2000001, 3000001
    plant_parts: dict[str, set[str]] = {p: set() for p in PLANTS}

    for y, m in MONTHS:
        days = calendar.monthrange(y, m)[1]
        for line, (plant, _bg, families) in LINES.items():
            noise = rng.gauss(0, 0.002)
            planned = []
            for _ in range(rng.randint(80, 100)):
                if "server_psu" in families and len(families) > 1:
                    share = 0.7 if e2(line, y, m) else 0.25
                    fam = "server_psu" if rng.random() < share else next(f for f in families if f != "server_psu")
                else:
                    fam = families[0] if len(families) == 1 or rng.random() < 0.7 else families[1]
                part = rng.choice([p for p, f in PARTS.items() if f == fam])
                plant_parts[plant].add(part)
                base = FAMILIES[fam][2]
                p_good = base + part_offset[part] + line_offset[line] + noise - (0.025 if e1(line, y, m) else 0)
                lo, hi = FAMILIES[fam][3]
                n = rng.randint(lo, hi)
                good = binomial_good(rng, n, p_good)
                start = date(y, m, rng.randint(1, days - 4))
                done = start + timedelta(days=rng.randint(1, 4))
                wo = f"WO{wo_seq}"
                wo_seq += 1
                wos.append({"wo_no": wo, "plant": plant, "line": line, "part_no": part, "start_date": start.isoformat(),
                            "complete_date": done.isoformat(), "input_qty": n, "good_qty": good, "scrap_qty": n - good})
                planned.append(base + part_offset[part] + line_offset[line])
                fails = n - good
                ict, fct, oqc = (round(fails * 0.5), round(fails * 0.35), 0)
                oqc = fails - ict - fct
                inspected = n
                for station, d_qty, d in (("ICT", ict, start), ("FCT", fct, start + timedelta(days=1)), ("OQC", oqc, done)):
                    inspections.append({"wo_no": wo, "station": station, "inspect_date": d.isoformat(),
                                        "inspected_qty": inspected, "defect_qty": d_qty})
                    inspected -= d_qty
                    if d_qty == 0:
                        continue
                    by_code: dict[str, int] = {}
                    for qty in [q for q in split(rng, d_qty, min(2, d_qty)) if q > 0]:
                        cats = STATION_CATEGORY[station]
                        if station == "ICT" and e1(line, y, m):
                            cats = {"焊接": 0.85, "零件": 0.15}
                        cat = rng.choices(list(cats), weights=list(cats.values()))[0]
                        pool = FAMILY_FUNCTION_CODES[fam] if cat == "功能" else [c for c, (k, _) in DEFECT_CODES.items() if k == cat]
                        code = rng.choice(pool)
                        by_code[code] = by_code.get(code, 0) + qty
                    # 同一工單、同一站、同一代碼合併成一列，原始檔就不會有「看起來重複」的合法資料
                    defects += [{"wo_no": wo, "station": station, "defect_code": c, "qty": q} for c, q in by_code.items()]
            targets.append({"scope": line, "period": f"{y}-{m:02d}", "metric": "yield",
                            "target_value": round(sum(planned) / len(planned), 3)})

        for plant in PLANTS:
            targets.append({"scope": plant, "period": f"{y}-{m:02d}", "metric": "otd", "target_value": 0.95})
            for part in sorted(plant_parts[plant]):
                fam = PARTS[part]
                for _ in range(rng.randint(2, 4)):
                    cust = rng.choice(FAMILY_CUSTOMERS[fam])
                    original = date(y, m, rng.randint(1, days))
                    late = rng.random() < 0.06
                    shipped = original + timedelta(days=rng.randint(1, 6) if late else -rng.randint(0, 3))
                    promised = original - timedelta(days=7) if (plant == "CN" and cust == "C07" and (y, m) == (2026, 10)) else original
                    shipments.append({"so_no": f"SO{so_seq}", "plant": plant, "part_no": part, "customer_id": cust,
                                      "promised_date": promised.isoformat(), "shipped_date": shipped.isoformat(),
                                      "qty": rng.randint(50, 800) * 10})
                    so_seq += 1
            for material, supplier in MATERIAL_SUPPLIERS.items():
                if material == "錫膏" and plant == "VN" and (y, m) >= (2026, 8):
                    supplier = "S09"
                if material == "PCB" and plant == "CN":
                    supplier = "S07"
                for _ in range(rng.randint(2, 4)):
                    qty = rng.randint(20, 200) * 50
                    rate = 0.035 if supplier == "S09" else rng.uniform(0.001, 0.007)
                    lots.append({"lot_no": f"LT{lot_seq}", "supplier_id": supplier, "plant": plant,
                                 "received_date": date(y, m, rng.randint(1, days)).isoformat(), "qty": qty,
                                 "rejected_qty": binomial_good(rng, qty, rate)})
                    lot_seq += 1
        for part, fam in PARTS.items():
            base = {"server_psu": 2800, "adapter": 380, "led_module": 520, "auto_lamp": 1500}[fam]
            costs.append({"part_no": part, "period": f"{y}-{m:02d}", "unit_cost_twd": round(base * rng.uniform(0.97, 1.03), 2)})

    return {"work_orders": wos, "inspections": inspections, "defects": defects, "shipments": shipments,
            "supplier_lots": lots, "standard_costs": costs, "targets": targets}


FULLWIDTH = str.maketrans({c: chr(ord(c) + 0xFEE0) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"})


class Dirt:
    """在乾淨資料上放入髒資料。每一列最多被動一次，筆數才能和清理報告一一對上。"""

    def __init__(self, rng: random.Random, rows: list[dict]) -> None:
        self.rng, self.rows = rng, rows
        self.pool = list(range(len(rows)))
        rng.shuffle(self.pool)

    def take(self, n: int, where=lambda r: True) -> list[dict]:
        picked = [i for i in self.pool if where(self.rows[i])][:n]
        self.pool = [i for i in self.pool if i not in set(picked)]
        return [self.rows[i] for i in picked]

    def duplicate(self, n: int) -> None:
        self.rows.extend(dict(r) for r in self.take(n))


def dirty(rng: random.Random, data: dict[str, list[dict]]) -> dict:
    manifest: dict[str, dict] = {}
    w = Dirt(rng, data["work_orders"])
    for r in w.take(120):
        r["part_no"] = f" {r['part_no'].lower()} " if rng.random() < 0.5 else r["part_no"].lower()
        r["line"] = r["line"].lower() if rng.random() < 0.5 else f"{r['line']} "
    for r in w.take(30):
        r["line"] = r["line"].translate(FULLWIDTH)
    for r in w.take(25, lambda r: r["good_qty"] > 0):
        r["scrap_qty"] = r["input_qty"] - r["good_qty"] + rng.randint(1, 30)
    for r in w.take(18):
        r["start_date"], r["complete_date"] = r["complete_date"], r["start_date"]
    for r in w.take(12):
        r["complete_date"] = rng.choice(["2026/13/05", "N/A", "", "31-02-2026"])
    for r in w.take(15):
        r["input_qty"] = rng.choice(["N/A", "-", "", "1,2O0"])
    w.duplicate(150)
    manifest["work_orders"] = {"duplicate": 150, "normalized": 150, "qty_overflow": 25, "date_swapped": 18,
                               "date_invalid": 12, "qty_invalid": 15}

    i = Dirt(rng, data["inspections"])
    i.duplicate(200)
    for k in range(20):
        data["inspections"].append({"wo_no": f"WO9{k:06d}", "station": "ICT", "inspect_date": "2026-06-15",
                                    "inspected_qty": 300, "defect_qty": 2})
    manifest["inspections"] = {"duplicate": 200, "orphan_wo": 20}

    d = Dirt(rng, data["defects"])
    for r in d.take(40, lambda r: r["defect_code"] == "SLD-01"):
        r["defect_code"] = "SLD-O1"
    for r in d.take(10):
        r["defect_code"] = "XX-99"
    manifest["defects"] = {"code_typo": 40, "code_unknown": 10}

    s = Dirt(rng, data["shipments"])
    for r in s.take(35):
        r["promised_date"] = ""
    for r in s.take(40):
        r["customer_id"] = rng.choice([r["customer_id"].lower(), f" {r['customer_id']}"])
    s.duplicate(60)
    manifest["shipments"] = {"duplicate": 60, "promised_missing": 35, "normalized": 40}

    lt = Dirt(rng, data["supplier_lots"])
    for r in lt.take(8):
        r["rejected_qty"] = r["qty"] + rng.randint(1, 100)
    manifest["supplier_lots"] = {"rejected_over_qty": 8}
    return manifest


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def master() -> dict[str, list[dict]]:
    return {
        "business_groups": [{"code": k, "name": v} for k, v in BUSINESS_GROUPS.items()],
        "plants": [{"code": k, "name": v} for k, v in PLANTS.items()],
        "lines": [{"code": k, "plant": p, "business_group": bg} for k, (p, bg, _) in LINES.items()],
        "parts": [{"part_no": p, "family": f, "family_name": FAMILIES[f][0], "business_group": FAMILIES[f][1]}
                  for p, f in PARTS.items()],
        "defect_codes": [{"code": c, "category": k, "description": d} for c, (k, d) in DEFECT_CODES.items()],
        "customers": [{"customer_id": c, "name": n, "region": r, "tier": t} for c, (n, r, t) in CUSTOMERS.items()],
        "suppliers": [{"supplier_id": s, "name": n, "country": c, "material": m} for s, (n, c, m) in SUPPLIERS.items()],
    }


def main() -> None:
    rng = random.Random(SEED)
    raw = settings.data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    data = generate(rng)
    clean_counts = {k: len(v) for k, v in data.items()}
    manifest = dirty(rng, data)
    for name, rows in {**master(), **data}.items():
        write_csv(raw / f"{name}.csv", rows)
    manifest["clean_rows"] = clean_counts
    (raw / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print({k: len(v) for k, v in data.items()})


if __name__ == "__main__":
    main()
