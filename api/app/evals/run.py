"""python -m app.evals.run --models fast,strong --repeats 2：跑評測、產生報告。

每題跑完就刪掉這次產生的對話與稽核紀錄，評測不會在資料庫留下痕跡。
（不能包在一個大交易裡回滾：查詢會把交易設成唯讀，之後存對話就會失敗。）
"""

import argparse
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import yaml
from sqlalchemy import text

from app.agent.llm import Usage, get_client
from app.agent.loop import model_for, run_chat
from app.auth import load_principal
from app.db import SessionLocal
from app.evals import checks
from app.evals.judge import judge

ROOT = Path(__file__).resolve().parents[2] / "eval"
CHECK_KEYS = ("tool_ok", "numeric_ok", "mention_ok", "mention_any_ok", "refuse_ok", "semantic_ok")


def load_cases() -> list[dict]:
    return yaml.safe_load((ROOT / "cases.yaml").read_text(encoding="utf-8"))["cases"]


def run_case(client, case: dict, alias: str) -> dict:
    db = SessionLocal()
    cid = None
    try:
        p = load_principal(db, case["user"])
        r = None
        for q in case.get("turns", [case["question"]]):
            r = run_chat(db, client, p, q, cid, force_alias=alias)
            cid = r.conversation_id
        result = checks.evaluate(case, r)
        e = case.get("expect", {})
        judge_usage = Usage()
        if e.get("claims"):
            verdict, judge_usage = judge(client, model_for("strong"), case["question"], r.answer, e["claims"], [])
            result["semantic_ok"] = verdict["pass"]
            result["judge"] = verdict
        passed = all(result[k] for k in CHECK_KEYS if k in result) and not result["leaked"]
        return {"case": case["id"], "category": case["category"], "alias": alias, "model": r.model, "passed": passed,
                **result, "answer": r.answer, "tools": r.tools, "grounding_ok": r.grounding_ok,
                "latency_ms": r.latency_ms, "cost_usd": r.cost_usd, "tokens": vars(r.usage), "judge_tokens": vars(judge_usage)}
    finally:
        db.rollback()
        if cid is not None:
            for sql in ("DELETE FROM audit_log WHERE conversation_id = :c", "DELETE FROM messages WHERE conversation_id = :c",
                        "DELETE FROM conversations WHERE id = :c"):
                db.execute(text(sql), {"c": cid})
            db.commit()
        db.close()


def summarize(rows: list[dict]) -> dict:
    out = {}
    for model, rs in sorted(defaultdict(list, {m: [r for r in rows if r["model"] == m] for m in {r["model"] for r in rows}}).items()):
        num = [r["numeric_ok"] for r in rs if "numeric_ok" in r]
        sem = [r["semantic_ok"] for r in rs if "semantic_ok" in r]
        ref = [r for r in rs if r["category"] == "overreach"]
        costs = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
        out[model] = {
            "n": len(rs), "pass_rate": sum(r["passed"] for r in rs) / len(rs),
            "numeric": sum(num) / len(num) if num else None, "semantic": sum(sem) / len(sem) if sem else None,
            "leak_rate": sum(r["leaked"] for r in rs) / len(rs),
            "refuse_ok": sum(r.get("refuse_ok", not r["leaked"]) for r in ref) / len(ref) if ref else None,
            "avg_latency_ms": int(sum(r["latency_ms"] for r in rs) / len(rs)),
            "avg_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
            "by_category": {c: sum(r["passed"] for r in rs if r["category"] == c) / len([r for r in rs if r["category"] == c])
                            for c in sorted({r["category"] for r in rs})},
        }
    return out


CATEGORY = {"single": "單一指標", "ranking": "排名", "compare": "比較與目標", "trend": "趨勢", "why": "為什麼（變化拆解）",
            "follow_up": "追問", "role": "角色範圍", "data_quality": "資料品質說明", "spec": "條件不合理", "overreach": "越權與注入"}


def report(rows: list[dict], summary: dict, stamp: str, repeats: int) -> str:
    pct = lambda x: "—" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    lines = [f"# 評測報告（{stamp}）", "",
             f"評測集：eval/cases.yaml，{len({r['case'] for r in rows})} 題 × 每題 {repeats} 次。標準答案由 app/evals/oracle.py "
             "以 pandas 直接從資料表重算，不經過被測的語意層；口徑由 LLM 評審判斷（校準見 CALIBRATION.md）。", "",
             "| 模型 | 次數 | 通過率 | 數字正確 | 口徑正確 | 越權題拒絕正確 | 洩漏率 | 平均延遲 | 平均成本 USD |", "|---|---|---|---|---|---|---|---|---|"]
    for m, s in summary.items():
        lines.append(f"| {m} | {s['n']} | {pct(s['pass_rate'])} | {pct(s['numeric'])} | {pct(s['semantic'])} | {pct(s['refuse_ok'])} | "
                     f"{pct(s['leak_rate'])} | {s['avg_latency_ms'] / 1000:.1f}s | {s['avg_cost_usd']} |")
    models = list(summary)
    lines += ["", "## 依題型", "", "| 題型 | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
    for c in CATEGORY:
        if any(c in summary[m]["by_category"] for m in models):
            lines.append(f"| {CATEGORY[c]} | " + " | ".join(pct(summary[m]["by_category"].get(c)) for m in models) + " |")
    fails = [r for r in rows if not r["passed"]]
    lines += ["", "## 未通過", ""]
    for r in fails:
        why = {k: r[k] for k in ("numbers_missing", "mentions_missing", "leaked_values") if k in r}
        why.update({k: False for k in CHECK_KEYS if r.get(k) is False})
        if "judge" in r and not r["judge"]["pass"]:
            why["judge_missing"] = r["judge"].get("missing")
        lines.append(f"- **{r['case']}**（{r['model']}）：{json.dumps(why, ensure_ascii=False)}")
    if not fails:
        lines.append("無。")
    return "\n".join(lines) + "\n"


def rescore(path: Path) -> None:
    cases = {c["id"]: c for c in load_cases()}
    rows = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        fake = SimpleNamespace(answer=r["answer"], tools=r["tools"])
        result = checks.evaluate(cases[r["case"]], fake)
        if "semantic_ok" in r:
            result["semantic_ok"], result["judge"] = r["semantic_ok"], r["judge"]
        for k in ("numbers_missing", "mentions_missing", "leaked_values", *CHECK_KEYS):
            r.pop(k, None)
        r.update(result)
        r["passed"] = all(r[k] for k in CHECK_KEYS if k in r) and not r["leaked"]
        rows.append(r)
    stamp = path.stem.removeprefix("run-")
    path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))
    summary = summarize(rows)
    repeats = max(sum(1 for r in rows if r["case"] == c and r["model"] == rows[0]["model"]) for c in cases)
    note = "\n> 本報告依目前的評分規則，以既有回答重新評分（未重新呼叫模型）。\n"
    (ROOT / "REPORT.md").write_text(report(rows, summary, stamp, repeats) + note, encoding="utf-8")
    for m, s in summary.items():
        print(m, json.dumps({k: v for k, v in s.items() if k != "by_category"}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="fast,strong")
    ap.add_argument("--cases", default="", help="只跑這些題號，逗號分隔")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rescore", default="", help="不重新呼叫模型，用既有的 run-*.jsonl 依目前的評分規則重算（評審結果沿用）")
    a = ap.parse_args()
    if a.rescore:
        rescore(Path(a.rescore))
        return
    cases = load_cases()
    if a.cases:
        cases = [c for c in cases if c["id"] in set(a.cases.split(","))]
    client = get_client()
    jobs = [(c, alias) for alias in a.models.split(",") for c in cases for _ in range(a.repeats)]
    with ThreadPoolExecutor(a.workers) as pool:
        rows = list(pool.map(lambda j: run_case(client, *j), jobs))
    stamp = time.strftime("%Y%m%d-%H%M")
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results" / f"run-{stamp}.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    summary = summarize(rows)
    (ROOT / "REPORT.md").write_text(report(rows, summary, stamp, a.repeats), encoding="utf-8")
    for m, s in summary.items():
        print(m, json.dumps({k: v for k, v in s.items() if k != "by_category"}, ensure_ascii=False))
    print("report: eval/REPORT.md")


if __name__ == "__main__":
    main()
