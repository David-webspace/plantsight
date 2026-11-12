"""「為什麼變了」：把指標在兩個期間的變化拆給各組，全部由程式計算，模型只負責講解。

比率型指標（良率、不良率、準交率…）整體值 R = Σ wᵢ·rᵢ，wᵢ 為各組分母占比。變化拆成：
  組合效應 Σ (wᵢ¹ − wᵢ⁰)·(rᵢ⁰ − R⁰)  —— 各組占比改變（例如高難度產品做得比較多）
  比率效應 Σ wᵢ¹·(rᵢ¹ − rᵢ⁰)  —— 各組自己變好或變壞
兩者相加剛好等於整體變化（Σ(wᵢ¹ − wᵢ⁰)·R⁰ = 0，所以減掉 R⁰ 不影響合計，
但能讓每一組的組合效應有意義：比平均差的組占比變大才算拉低）。加總型指標（不良數、報廢數…）直接比較各組增減。
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import Principal
from app.semantic.catalog import catalog
from app.semantic.query import Period, QuerySpec, SpecError, _excluded, check, compile_group, month_range

TOP = 8


def _parts(db: Session, spec: QuerySpec, p: Principal, metric, d0, d1) -> dict[str, tuple[float, float]]:
    c = compile_group(spec, p, [metric], d0, d1)
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        rows = db.execute(text(c.sql), c.params).mappings().all()
    finally:
        db.rollback()
    out = {}
    for r in rows:
        key = str(r[c.keys[0]])
        if metric.kind == "sum":
            out[key] = (float(r[metric.key] or 0), 1.0)
        else:
            out[key] = (float(r[f"{metric.key}__num"] or 0), float(r[f"{metric.key}__den"] or 0))
    return out


def explain_change(db: Session, p: Principal, metric_key: str, by: str, base: dict, current: dict,
                   filters: dict[str, list[str]] | None = None) -> dict:
    """base／current：{"from": "YYYY-MM", "to": "YYYY-MM"}；by：要拆解的維度。"""
    spec = QuerySpec(metrics=[metric_key], group_by=[by], filters=filters or {},
                     period=Period.model_validate({"from": current["from"], "to": current["to"]}))
    metric = check(spec, p)[0]
    check(spec.model_copy(update={"period": Period.model_validate({"from": base["from"], "to": base["to"]})}), p)
    if metric.kind == "fpy":
        raise SpecError("直通率是各站通過率相乘，無法線性拆解；請改用不良率（dppm）或良率")
    a = _parts(db, spec, p, metric, *month_range(base["from"], base["to"]))
    b = _parts(db, spec, p, metric, *month_range(current["from"], current["to"]))
    label = catalog().dims[by]["label"]

    if metric.kind == "sum":
        groups = [{by: k, "before": a.get(k, (0, 1))[0], "after": b.get(k, (0, 1))[0]} for k in sorted({*a, *b})]
        for g in groups:
            g["change"] = g["after"] - g["before"]
        total0, total1 = sum(g["before"] for g in groups), sum(g["after"] for g in groups)
        groups.sort(key=lambda g: -abs(g["change"]))
        return {"metric": metric.label, "unit": metric.unit, "by": label, "base": _label(base), "current": _label(current),
                "before": round(total0), "after": round(total1), "change": round(total1 - total0),
                "groups": [{k: round(v) if isinstance(v, float) else v for k, v in g.items()} for g in groups[:TOP]],
                "method": f"{metric.label}為加總型指標，變化＝各{label}增減的合計", "basis": metric.basis,
                "excluded": _excluded_both(db, spec, p, metric, base, current)}

    den0, den1 = sum(d for _, d in a.values()), sum(d for _, d in b.values())
    if not den0 or not den1:
        raise SpecError("其中一個期間沒有資料，無法比較")
    s = metric.scale
    total0 = sum(n for n, _ in a.values()) / den0 * s
    total1 = sum(n for n, _ in b.values()) / den1 * s
    groups, mix, rate = [], 0.0, 0.0
    for k in sorted({*a, *b}):
        n0, d0 = a.get(k, (0.0, 0.0))
        n1, d1 = b.get(k, (0.0, 0.0))
        w0, w1 = d0 / den0, d1 / den1
        r0 = n0 / d0 * s if d0 else None
        r1 = n1 / d1 * s if d1 else None
        # 前期沒有這組時用整體前期值當基準，本期消失時比率效應為 0
        base_r = r0 if r0 is not None else total0
        m_eff = (w1 - w0) * (base_r - total0)
        r_eff = w1 * (r1 - base_r) if r1 is not None else 0.0
        mix, rate = mix + m_eff, rate + r_eff
        groups.append({by: k, "share_before": w0 * 100, "share_after": w1 * 100, "value_before": r0, "value_after": r1,
                       "mix_effect": m_eff, "rate_effect": r_eff, "total_effect": m_eff + r_eff})
    groups.sort(key=lambda g: -abs(g["total_effect"]))
    nd = metric.decimals + 1  # 拆解項多留一位，避免四捨五入後加總對不上
    unit = "百分點" if metric.unit == "%" else metric.unit
    driver = "組合效應" if abs(mix) > abs(rate) else "比率效應"
    return {
        "metric": metric.label, "unit": metric.unit, "effect_unit": unit, "by": label,
        "base": _label(base), "current": _label(current),
        "before": round(total0, metric.decimals), "after": round(total1, metric.decimals),
        "change": round(total1 - total0, nd), "mix_effect": round(mix, nd), "rate_effect": round(rate, nd),
        "main_driver": driver,
        "groups": [{k: (round(v, nd) if isinstance(v, float) else v) for k, v in g.items()} for g in groups[:TOP]],
        "method": (f"整體{metric.label}變化＝組合效應（各{label}占比改變）＋比率效應（各{label}自己的{metric.label}改變）；"
                   f"占比以{metric.label}的分母計算"),
        "basis": metric.basis,
        "excluded": _excluded_both(db, spec, p, metric, base, current),
    }


def _excluded_both(db: Session, spec: QuerySpec, p: Principal, metric, base: dict, current: dict) -> list[dict]:
    """基期加本期範圍內被清理排除的筆數，回答時一併說明。"""
    d0, _ = month_range(min(base["from"], current["from"]), min(base["from"], current["from"]))
    _, d1 = month_range(max(base["to"], current["to"]), max(base["to"], current["to"]))
    return _excluded(db, spec, p, [metric], d0, d1)


def _label(p: dict) -> str:
    return p["from"] if p["from"] == p["to"] else f"{p['from']}～{p['to']}"
