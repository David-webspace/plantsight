"""API：登入、總覽依角色略過看不到的指標、Excel 匯出重新套權限、品保與管理頁的角色限制。"""

import io
import os

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app, login_limit

OCT = {"from": "2026-10", "to": "2026-10"}


@pytest.fixture
def client():
    login_limit.hits.clear()
    return TestClient(app)


def login(c, user):
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"username": user, "password": os.environ["SEED_DEMO_PASSWORD"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_failure_is_generic(client):
    a = client.post("/api/auth/login", json={"username": "gm", "password": "wrong"})
    b = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    assert client.get("/api/me").status_code == 401


def test_overview_skips_metrics_outside_role(client):
    me = login(client, "tw.smt1.lead")
    assert me["scope"] == "產線 TW-SMT1"
    keys = {c["key"] for c in client.get("/api/overview").json()["cards"]}
    assert {"yield", "fpy", "dppm"} <= keys and not keys & {"otd", "scrap_cost", "incoming_reject_rate"}
    login(client, "gm")
    ov = client.get("/api/overview").json()
    assert {c["key"] for c in ov["cards"]} == {"yield", "fpy", "dppm", "otd", "incoming_reject_rate", "scrap_cost"}
    assert ov["period"] == "2026-10" and len(ov["trends"]["yield"]) == 12


def test_export_requeries_with_user_permissions(client):
    login(client, "tw.manager")
    spec = {"metrics": ["yield"], "group_by": ["line"], "period": OCT}
    r = client.post("/api/export", json=spec)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    ws = load_workbook(io.BytesIO(r.content))["data"]
    lines = [row[0].value for row in ws.iter_rows(min_row=2)]
    assert lines and all(x.startswith("TW-") for x in lines)
    assert client.post("/api/export", json={**spec, "filters": {"plant": ["VN"]}}).status_code == 403


def test_review_and_admin_roles(client):
    login(client, "tw.manager")
    assert client.get("/api/review").status_code == 403
    assert client.get("/api/admin/audit").status_code == 403
    login(client, "qa.engineer")
    items = client.get("/api/review").json()["items"]
    assert items and client.get("/api/admin/audit").status_code == 403
    login(client, "sysadmin")
    assert client.post(f"/api/review/{items[0]['id']}", json={"approve": False}).status_code == 403
    assert "summary" in client.get("/api/admin/audit").json()


def test_other_users_conversation_is_hidden(client):
    login(client, "gm")
    assert client.get("/api/conversations/999999/messages").status_code == 404
