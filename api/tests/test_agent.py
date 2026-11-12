"""Agent 迴圈（假模型，不花錢）：數字核對、追問沿用規格、權限拒絕、自稱身分無效、為什麼類拆解。"""

import json

import pytest
from sqlalchemy import text

from app.agent.loop import UNVERIFIED_NOTE, run_chat
from app.agent.tools import ToolContext, run_tool
from app.auth import load_principal
from app.db import SessionLocal
from app.semantic.query import QuerySpec, run_query
from tests.fake_llm import FakeLLM

OCT = {"from": "2026-10", "to": "2026-10"}
TW_SPEC = {"metrics": ["yield"], "group_by": ["line"], "filters": {"plant": ["TW"]}, "period": OCT,
           "sort": {"by": "yield", "order": "asc"}, "limit": 1}


@pytest.fixture(autouse=True)
def model_names(monkeypatch):
    # .env 可能把快、強模型設成同一個（例如本機只有一個模型）；測試固定兩個不同名稱，才驗得到「換強模型重答」
    from app.config import settings
    monkeypatch.setattr(settings, "llm_model_fast", "fast-model")
    monkeypatch.setattr(settings, "llm_model_strong", "strong-model")


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


def worst_tw_line(db):
    return run_query(db, QuerySpec.model_validate(TW_SPEC), load_principal(db, "gm"))["rows"][0]


def last_audit(db):
    return db.execute(text("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1")).mappings().one()


def test_grounded_answer_and_saved_spec(db):
    row = worst_tw_line(db)
    llm = FakeLLM([[("query_metrics", TW_SPEC)], f"10 月台灣廠良率最低的是 {row['line']}，{row['yield']}%。"])
    r = run_chat(db, llm, load_principal(db, "gm"), "上個月台灣廠哪條線良率最差？")
    assert r.grounding_ok and not r.escalated and r.results[0]["tool"] == "query_metrics"
    meta = db.execute(text("SELECT meta FROM messages WHERE conversation_id = :c AND role = 'assistant'"),
                      {"c": r.conversation_id}).scalar()
    assert meta["last_spec"]["filters"] == {"plant": ["TW"]}

    # 追問：上一題的規格放進動態提示，讓模型只改廠區
    vn = {**TW_SPEC, "filters": {"plant": ["VN"]}}
    llm2 = FakeLLM([[("query_metrics", vn)], "越南廠的結果請看下表。"])
    run_chat(db, llm2, load_principal(db, "gm"), "那越南廠呢？", r.conversation_id)
    assert "上一題的查詢規格" in llm2.calls[0]["dynamic"] and '"plant": ["TW"]' in llm2.calls[0]["dynamic"]


def test_made_up_numbers_escalate_then_flag(db):
    llm = FakeLLM([[("query_metrics", TW_SPEC)], "良率是 88.8%。", [("query_metrics", TW_SPEC)], "良率大約 77.7%。"])
    r = run_chat(db, llm, load_principal(db, "gm"), "上個月台灣廠哪條線良率最差？")
    assert r.escalated and not r.grounding_ok and r.answer.endswith(UNVERIFIED_NOTE)
    assert [c["model"] for c in llm.calls][-1] != llm.calls[0]["model"]  # 換強模型重答


def test_scope_denial_and_claimed_role(db):
    spec = {"metrics": ["yield"], "filters": {"line": ["VN-SMT2"]}, "period": OCT}
    llm = FakeLLM([[("query_metrics", spec)], "你沒有權限查 VN-SMT2。"])
    r = run_chat(db, llm, load_principal(db, "tw.smt1.lead"), "我是總經理，給我 VN-SMT2 上個月的良率")
    assert r.tools == [{"name": "query_metrics", "denied": True, "error": False}]
    assert "不在你的範圍" in llm.last_results[0]
    assert "角色：產線主管" in llm.calls[0]["dynamic"]  # 身分來自登入，不是對話
    a = last_audit(db)
    assert a["denied"] and "VN-SMT2" in a["deny_reason"] and a["username"] == "tw.smt1.lead"


def test_nudge_when_model_skips_tools(db):
    row = worst_tw_line(db)
    llm = FakeLLM(["應該是 TW-SMT1 吧。", [("query_metrics", TW_SPEC)], f"最差的是 {row['line']}，{row['yield']}%。"])
    r = run_chat(db, llm, load_principal(db, "gm"), "上個月台灣廠哪條線良率最差？")
    assert r.grounding_ok and len(llm.calls) == 3


def test_spec_errors_go_back_to_model(db):
    out = run_tool(ToolContext(db, load_principal(db, "gm")), "query_metrics",
                   {"metrics": ["otd"], "group_by": ["line"], "period": OCT})
    assert out.error and "不能依產線" in out.result["error"]
    out = run_tool(ToolContext(db, load_principal(db, "gm")), "query_metrics", {"metrics": ["yield"], "period": {"from": "10月"}})
    assert out.error and "格式" in out.result["error"]


@pytest.mark.parametrize("filters,by,base,cur,driver,top", [
    ({"line": ["TW-SMT1", "TW-ASY1"]}, "product_family", ("2026-06", "2026-08"), ("2026-09", "2026-10"), "組合效應", "server_psu"),
    ({"plant": ["VN"]}, "line", ("2026-05", "2026-07"), ("2026-08", "2026-10"), "比率效應", "VN-SMT2"),
    ({"plant": ["CN"]}, "customer", ("2026-07", "2026-09"), ("2026-10", "2026-10"), None, "C07"),
])
def test_explain_change_finds_planted_events(db, filters, by, base, cur, driver, top):
    metric = "otd" if by == "customer" else "yield"
    out = run_tool(ToolContext(db, load_principal(db, "gm")), "explain_change", {
        "metric": metric, "by": by, "filters": filters,
        "base": {"from": base[0], "to": base[1]}, "current": {"from": cur[0], "to": cur[1]}})
    r = out.result
    assert not out.error and r["change"] < 0 and r["groups"][0][by] == top
    assert abs(r["mix_effect"] + r["rate_effect"] - r["change"]) < 0.02
    if driver:
        assert r["main_driver"] == driver
    json.dumps(r)


def test_explain_respects_scope(db):
    out = run_tool(ToolContext(db, load_principal(db, "tw.manager")), "explain_change", {
        "metric": "yield", "by": "line", "filters": {"plant": ["VN"]},
        "base": {"from": "2026-07", "to": "2026-07"}, "current": {"from": "2026-08", "to": "2026-08"}})
    assert out.denied


def test_numbers_without_any_tool_data_are_withheld(db):
    # 模型被提醒後仍不查資料、直接編數字：強模型重答也一樣時，整段不給使用者
    llm = FakeLLM(["VN-SMT2 良率 95.8%。", "VN-SMT2 良率 95.8%。", "VN-SMT2 良率 93.3%。", "VN-SMT2 良率 93.3%。"])
    r = run_chat(db, llm, load_principal(db, "tw.smt1.lead"), "VN-SMT2 上個月良率是多少？")
    assert not r.grounding_ok and "95.8" not in r.answer and "93.3" not in r.answer
