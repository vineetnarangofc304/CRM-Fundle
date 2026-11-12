# Iteration 2: SaaS starter funnel, Company & Deal fields, deal value tracking (dashboard + KPI)
import io
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
CREATED = []


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    yield s
    with open("/tmp/saas_test_leads.txt", "w") as f:
        f.write(" ".join(map(str, CREATED)))  # cleanup via mongosh (no lead DELETE API)


def kpis(client):
    return client.get(f"{BASE_URL}/api/reports/dashboard", params={"section": "kpis"}).json()


# Starter funnel tags + disposition map + kpi_config
def test_starter_tags_and_map(client):
    cats = client.get(f"{BASE_URL}/api/catalogs").json()
    names = [t["name"] for t in cats["tag"]]
    for t in ["Demo Booked", "Demo Done", "Proposal Sent", "Won"]:
        assert names.count(t) == 1, t
    dm = client.get(f"{BASE_URL}/api/catalogs/disposition-map").json()["map"]
    assert {"Demo Booked", "Proposal Sent"} <= set(dm["Contacted"])
    assert {"Demo Done", "Won"} <= set(dm["Converted"])


def test_kpi_config_prefilled(client):
    s = client.get(f"{BASE_URL}/api/admin/settings").json()
    cfg = s["kpi_config"]
    assert cfg["milestones"] == ["Demo Booked", "Demo Done", "Proposal Sent", "Won"]
    assert cfg["valid_tags"] == ["Call back for first pitch", "Demo Booked", "Demo Done", "Proposal Sent", "Won",
                                 "Valid Not Interested"]


# Lead create with company fields + deal value -> dashboard sums
def test_create_lead_company_fields_and_dashboard(client):
    before = kpis(client)
    r = client.post(f"{BASE_URL}/api/leads", json={
        "name": "TEST_SaaS Open", "phone": "9000011111", "company_name": "TEST Acme Pvt Ltd",
        "designation": "CTO", "team_size": "51-200", "deal_value": 75000})
    assert r.status_code == 200, r.text
    d = r.json()
    CREATED.append(d["id"])
    assert d["company_name"] == "TEST Acme Pvt Ltd" and d["designation"] == "CTO"
    assert d["team_size"] == "51-200" and d["deal_value"] == 75000
    g = client.get(f"{BASE_URL}/api/leads/{d['id']}").json()
    g = g.get("lead", g)
    assert g["deal_value"] == 75000 and g["company_name"] == "TEST Acme Pvt Ltd"

    r2 = client.post(f"{BASE_URL}/api/leads", json={"name": "TEST_SaaS Won", "phone": "9000022222",
                                                     "deal_value": 30000, "lead_stage": "Converted"})
    assert r2.status_code == 200
    CREATED.append(r2.json()["id"])
    time.sleep(46)  # dashboard cache TTL 45s
    after = kpis(client)
    for k in ("pipeline_value", "pipeline_count", "won_value_mtd", "won_value_range"):
        assert k in after
    assert after["pipeline_value"] - before["pipeline_value"] == 75000
    assert after["pipeline_count"] - before["pipeline_count"] == 1
    assert after["won_value_mtd"] - before["won_value_mtd"] == 30000
    assert after["won_value_range"] - before["won_value_range"] == 30000


# PATCH deal value parsing + validation + audit
def test_patch_deal_value_parsing(client):
    lid = CREATED[0]
    r = client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"deal_value": "1,20,000"}})
    assert r.status_code == 200, r.text
    assert r.json()["deal_value"] == 120000
    r = client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"deal_value": "abc"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "Deal value must be a number"
    r = client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"company_name": "TEST Beta", "designation": "CEO",
                                                                       "team_size": "1000+"}})
    assert r.status_code == 200 and r.json()["team_size"] == "1000+"


def test_patch_deal_value_edge(client):
    lid = CREATED[0]
    r = client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"deal_value": "-500"}})
    print("negative ->", r.status_code, r.text[:200])
    r2 = client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"deal_value": "12abc"}})
    print("12abc ->", r2.status_code, r2.text[:200])
    client.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"deal_value": "120000"}})
    assert r.status_code == 400, "negative deal value accepted"


def test_chatter_audit_has_deal_change(client):
    lid = CREATED[0]
    found = False
    for path in (f"/api/leads/{lid}/messages", f"/api/chatter/{lid}", f"/api/leads/{lid}/chatter",
                 f"/api/leads/{lid}/audit"):
        r = client.get(f"{BASE_URL}{path}")
        if r.status_code == 200 and "Deal Value" in r.text:
            found = True
            break
    assert found


# Export columns
def test_export_columns(client):
    from openpyxl import load_workbook
    r = client.get(f"{BASE_URL}/api/export/leads.xlsx")
    assert r.status_code == 200, r.text[:200]
    wb = load_workbook(io.BytesIO(r.content))
    hdr = [c.value for c in next(wb.active.iter_rows(max_row=1))]
    for c in ("Company", "Designation", "Team Size", "Deal Value (INR)"):
        assert c in hdr, hdr


# Webhook aliases
def test_webhook_aliases(client):
    w = client.post(f"{BASE_URL}/api/webhooks", json={"name": "TEST_hook"})
    assert w.status_code == 200, w.text
    hook = w.json()
    r = requests.post(f"{BASE_URL}/api/webhook/lead/{hook['token']}", json={
        "name": "TEST_Webhook SaaS", "phone": "9000033333", "company": "TEST HookCo",
        "job_title": "VP Sales", "company_size": "11-50"})
    assert r.status_code == 200, r.text
    body = r.json()
    lid = body.get("lead_id") or body.get("id") or (body.get("lead") or {}).get("id")
    assert lid, body
    CREATED.append(lid)
    g = client.get(f"{BASE_URL}/api/leads/{lid}").json()
    g = g.get("lead", g)
    assert g["company_name"] == "TEST HookCo" and g["designation"] == "VP Sales" and g["team_size"] == "11-50"
    client.patch(f"{BASE_URL}/api/webhooks/{hook['id']}", json={"active": False})
    client.delete(f"{BASE_URL}/api/webhooks/{hook['id']}")


# KPI overview deals block
def test_kpi_overview_deals(client):
    k = client.get(f"{BASE_URL}/api/reports/kpi-overview").json()
    d = k["deals"]
    for f in ("pipeline", "pipeline_count", "won", "won_ftd", "won_mtd", "won_ytd"):
        assert f in d and isinstance(d[f], (int, float))
    names = [m.get("name") or m.get("tag") for m in k["milestones"]]
    assert names == ["Demo Booked", "Demo Done", "Proposal Sent", "Won"], k["milestones"]
