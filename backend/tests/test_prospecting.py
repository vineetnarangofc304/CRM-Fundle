# AI Prospecting module regression tests (overview, prospects list/detail, RBAC, cron auth/idempotency, lead linkage)
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}


def _secret():
    for line in open("/app/backend/.env"):
        if line.startswith("WEBHOOK_CRON_SECRET="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return s


@pytest.fixture(scope="module")
def caller(admin):
    email = f"test_caller_{uuid.uuid4().hex[:6]}@example.com"
    r = admin.post(f"{BASE_URL}/api/users", json={"name": "TEST_caller", "email": email, "password": "Test@12345", "role": "caller"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    s = requests.Session()
    lr = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "Test@12345"})
    assert lr.status_code == 200
    s.headers["Authorization"] = f"Bearer {lr.json()['access_token']}"
    yield s
    admin.delete(f"{BASE_URL}/api/users/{uid}")


# ---- overview ----
def test_overview(admin):
    r = admin.get(f"{BASE_URL}/api/prospecting/overview")
    assert r.status_code == 200
    d = r.json()
    assert len(d["all_categories"]) == 33
    assert d["settings"]["countries"] == ["India"]
    assert d["totals"]["brands"] > 0
    assert set(d["segments"]) == {"Offline Retail", "D2C / E-commerce", "Omnichannel"}
    india = [c for c in d["categories"] if c["country"] == "India"]
    assert len(india) == 33
    assert sum(c["brands"] for c in d["categories"]) == d["totals"]["brands"]


# ---- prospects list + filters ----
def test_list_default_sorted_by_stores(admin):
    r = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 50})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] > 0 and len(d["items"]) <= 50
    sc = [i["store_count"] for i in d["items"] if i.get("store_count") is not None]
    assert sc == sorted(sc, reverse=True)
    assert all("_id" not in i and "sources" not in i for i in d["items"])


@pytest.mark.parametrize("params,check", [
    ({"country": "India"}, lambda i: i["country"] == "India"),
    ({"segment": "Offline Retail"}, lambda i: i["segment"] == "Offline Retail"),
    ({"status": "discovered"}, lambda i: i["status"] == "discovered"),
    ({"min_stores": 100}, lambda i: (i.get("store_count") or 0) >= 100),
    ({"has_ceo": "true"}, lambda i: bool(i.get("ceo_name"))),
])
def test_list_filters(admin, params, check):
    r = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={**params, "limit": 200})
    assert r.status_code == 200
    assert all(check(i) for i in r.json()["items"])


def test_list_category_and_q(admin):
    first = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 1}).json()["items"][0]
    r = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"category": first["category"], "limit": 200})
    assert all(i["category"] == first["category"] for i in r.json()["items"])
    q = first["brand_name"][:4]
    r2 = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"q": q.lower()})
    assert any(i["id"] == first["id"] for i in r2.json()["items"])
    r3 = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"q": "(.*"})
    assert r3.status_code == 200


def test_pagination_and_sorts(admin):
    p1 = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 10, "page": 1}).json()
    p2 = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 10, "page": 2}).json()
    assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})
    b = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"sort": "brand", "limit": 30}).json()["items"]
    names = [i["brand_name"] for i in b]
    assert names == sorted(names)
    rec = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"sort": "recent", "limit": 30}).json()["items"]
    assert [i["id"] for i in rec] == sorted([i["id"] for i in rec], reverse=True)


def test_detail_and_404(admin):
    first = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 1}).json()["items"][0]
    r = admin.get(f"{BASE_URL}/api/prospecting/prospects/{first['id']}")
    assert r.status_code == 200
    d = r.json()
    assert d["brand_name"] == first["brand_name"] and "sources" in d
    assert admin.get(f"{BASE_URL}/api/prospecting/prospects/99999999").status_code == 404


# ---- RBAC ----
@pytest.mark.parametrize("method,path", [
    ("get", "/api/prospecting/overview"), ("get", "/api/prospecting/prospects"),
    ("get", "/api/prospecting/prospects/1"), ("post", "/api/prospecting/run"),
    ("post", "/api/prospecting/prospects/1/research"), ("put", "/api/prospecting/settings"),
    ("post", "/api/prospecting/stop"),
])
def test_caller_forbidden(caller, method, path):
    kw = {"json": {"enabled": False}} if method == "put" else {}
    r = getattr(caller, method)(f"{BASE_URL}{path}", **kw)
    assert r.status_code == 403, f"{path} -> {r.status_code}"


def test_unauthenticated():
    assert requests.get(f"{BASE_URL}/api/prospecting/overview").status_code in (401, 403)


def test_settings_invalid_phase(admin):
    r = admin.put(f"{BASE_URL}/api/prospecting/settings", json={"enabled": True, "countries": ["India"], "phase": "bogus", "concurrency": 3, "create_leads": True})
    assert r.status_code == 400
    assert admin.get(f"{BASE_URL}/api/prospecting/overview").json()["settings"]["phase"] == "auto"


# ---- cron ----
def test_cron_auth():
    assert requests.post(f"{BASE_URL}/api/cron/prospecting").status_code == 401
    assert requests.post(f"{BASE_URL}/api/cron/prospecting", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert requests.post(f"{BASE_URL}/api/cron/prospecting", headers={"Authorization": "wrong"}).status_code == 401


def test_cron_ok_idempotent_and_lock(admin):
    ov = admin.get(f"{BASE_URL}/api/prospecting/overview").json()
    if not ov["running"]:
        pytest.skip("engine not running; skip to avoid starting an extra LLM batch")
    started = ov["lock"]["started"]
    wid = f"TEST_{uuid.uuid4().hex}"
    h = {"Authorization": f"Bearer {_secret()}", "X-Webhook-Id": wid}
    r1 = requests.post(f"{BASE_URL}/api/cron/prospecting", headers=h)
    assert r1.status_code == 200 and r1.json().get("queued") is True
    r2 = requests.post(f"{BASE_URL}/api/cron/prospecting", headers=h)
    assert r2.status_code == 200 and r2.json().get("duplicate") is True
    time.sleep(3)
    ov2 = admin.get(f"{BASE_URL}/api/prospecting/overview").json()
    assert ov2["lock"]["started"] == started, "lock was stolen by a second worker"


# ---- prospects as CRM leads ----
def test_prospect_leads(admin):
    items = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 20}).json()["items"]
    missing = [i["id"] for i in items if not i.get("lead_id")]
    assert not missing, f"prospects without lead: {missing}"
    p = items[0]
    lead = admin.get(f"{BASE_URL}/api/leads/{p['lead_id']}").json()
    assert lead["source_lead"] == "AI Prospecting"
    assert lead["company_name"] == p["brand_name"]
    assert lead["prospect_category"] == p["category"]
    assert lead["prospect_id"] == p["id"]
    assert lead.get("store_count") == p.get("store_count")


def test_prospect_count_matches_leads(admin):
    total = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 1}).json()["total"]
    lt = admin.get(f"{BASE_URL}/api/leads", params={"source_lead": "AI Prospecting", "limit": 1}).json()["total"]
    # counts are cached 30s and engine is adding live — allow a margin
    assert lt == -1 or abs(lt - total) <= 60, f"leads={lt} prospects={total}"


def test_leads_prospect_filters_and_sort(admin):
    r = admin.get(f"{BASE_URL}/api/leads", params={"prospect_segment": "Offline Retail", "min_stores": 50, "sort": "store_count", "order": "desc", "limit": 50})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "no leads for filter"
    assert all(i.get("prospect_segment") == "Offline Retail" and i["store_count"] >= 50 for i in items)
    sc = [i["store_count"] for i in items]
    assert sc == sorted(sc, reverse=True)
    cat = items[0]["prospect_category"]
    r2 = admin.get(f"{BASE_URL}/api/leads", params={"prospect_category": cat, "limit": 50}).json()["items"]
    assert r2 and all(i["prospect_category"] == cat for i in r2)
    assert admin.get(f"{BASE_URL}/api/leads", params={"min_stores": "abc"}).status_code == 200
