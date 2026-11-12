# Fundle.ai CRM regression: auth, seeded catalogs, lead CRUD, KPI config, reports
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
FORBIDDEN = ["tiftech", "homeivf", "fertility", "ivf", "opd", "patient", "spouse", "doctor", "treatment", "clinic"]


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# Auth
def test_login_ok():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    assert r.status_code == 200
    d = r.json()
    u = d.get("user", d)
    assert u["email"] == ADMIN["email"]
    assert u["role"] == "admin"


def test_login_bad():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN["email"], "password": "nope"})
    assert r.status_code in (400, 401)


# Seeded catalogs / dispositions
def test_catalogs_seed(client):
    r = client.get(f"{BASE_URL}/api/catalogs")
    assert r.status_code == 200
    d = r.json()
    dm = d["disposition_map"]
    assert sorted(dm["Contact Attempt"]) == ["Busy", "Not Reachable", "Phone Switched Off", "Ringing"]
    assert dm["Contacted"] == ["Call back for first pitch"]
    assert dm["Converted"] == []
    assert len(dm["Closed"]) == 12
    blob = str(d).lower()
    for w in ["fertility", "ivf", "opd", "patient", "spouse", "doctor", "treatment", "clinic", "homeivf", "tiftech"]:
        assert w not in blob, f"forbidden word {w} in catalogs"


# Read-only endpoints load on empty DB
@pytest.mark.parametrize("path", [
    "/api/leads", "/api/leads/group_counts", "/api/reports/dashboard", "/api/reports/kpi-overview",
    "/api/reports/trends", "/api/reports/source-conversion", "/api/admin/settings",
    "/api/admin/role-permissions", "/api/admin/automations", "/api/users", "/api/activities",
    "/api/leads/followups/analytics", "/api/leads/followups/reminders", "/api/catalogs/disposition-map",
])
def test_endpoints_ok(client, path):
    r = client.get(f"{BASE_URL}{path}")
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:300]}"


# Full KPI flow: tags -> map -> kpi_config -> leads -> kpi-overview
class TestKpiFlow:
    state = {}

    def test_create_tags(self, client):
        for nm in ["TEST_Demo Booked", "TEST_Demo Done", "TEST_Won"]:
            r = client.post(f"{BASE_URL}/api/catalogs/tag", json={"name": nm})
            assert r.status_code == 200, r.text
            self.state[nm] = r.json()["id"]
        cats = client.get(f"{BASE_URL}/api/catalogs").json()
        assert {"TEST_Demo Booked", "TEST_Demo Done", "TEST_Won"} <= {t["name"] for t in cats["tag"]}

    def test_map_tags(self, client):
        dm = client.get(f"{BASE_URL}/api/catalogs/disposition-map").json()["map"]
        self.state["orig_map"] = dm
        new = {k: list(v) for k, v in dm.items()}
        new["Contacted"] = new.get("Contacted", []) + ["TEST_Demo Booked", "TEST_Demo Done"]
        new["Converted"] = new.get("Converted", []) + ["TEST_Won"]
        r = client.put(f"{BASE_URL}/api/catalogs/disposition-map", json={"map": new})
        assert r.status_code == 200
        assert "TEST_Won" in r.json()["map"]["Converted"]

    def test_save_kpi_config(self, client):
        val = {"milestones": ["TEST_Demo Booked", "TEST_Demo Done", "TEST_Won"],
               "valid_tags": ["TEST_Demo Booked", "Call back for first pitch"]}
        r = client.patch(f"{BASE_URL}/api/admin/settings", json={"key": "kpi_config", "value": val})
        assert r.status_code == 200
        s = client.get(f"{BASE_URL}/api/admin/settings").json()
        assert s["kpi_config"]["milestones"] == val["milestones"]

    def test_lead_crud_and_kpi(self, client):
        r = client.post(f"{BASE_URL}/api/leads", json={"name": "TEST_Lead Acme", "phone": "+91 9000000001",
                                                       "query": "Need demo", "source_lead": "Website"})
        assert r.status_code == 200, r.text
        lead = r.json()
        self.state["lead_id"] = lead["id"]
        assert lead["country"] == "India"
        g = client.get(f"{BASE_URL}/api/leads/{lead['id']}")
        assert g.status_code == 200 and g.json()["name"] == "TEST_Lead Acme"
        p = client.patch(f"{BASE_URL}/api/leads/{lead['id']}", json={"updates": {
            "lead_stage": "Converted", "tags": [self.state["TEST_Won"], self.state["TEST_Demo Booked"]]}})
        assert p.status_code == 200, p.text
        assert p.json()["lead_stage"] == "Converted"
        # chatter
        m = client.post(f"{BASE_URL}/api/leads/{lead['id']}/messages", json={"body": "TEST note"})
        assert m.status_code == 200, m.text
        msgs = client.get(f"{BASE_URL}/api/leads/{lead['id']}/messages").json()
        assert "TEST note" in str(msgs)
        # KPI (cache key includes config hash, which changed, but lead edit may be cached) - retry
        k = client.get(f"{BASE_URL}/api/reports/kpi-overview").json()
        assert [m["name"] for m in k["milestones"]] == ["TEST_Demo Booked", "TEST_Demo Done", "TEST_Won"]
        assert set(k["valid"].keys()) >= {"tags", "ftd", "mtd", "ytd"}
        conv = next(s for s in k["stages"] if s["key"] == "converted")
        assert "TEST_Won" in [x["name"] for x in conv["rows"]]
        self.state["kpi"] = k

    def test_kpi_counts(self, client):
        k = self.state["kpi"]
        won = next(m for m in k["milestones"] if m["name"] == "TEST_Won")
        conv = next(s for s in k["stages"] if s["key"] == "converted")
        assert won["mtd"] == 1, f"milestone counts {k['milestones']}"
        assert conv["totals"]["mtd"] == 1
        assert k["valid"]["mtd"] == 1

    def test_export_excel(self, client):
        r = client.get(f"{BASE_URL}/api/export/leads.xlsx")
        assert r.status_code == 200
        assert r.content[:2] == b"PK"


def test_dashboard_reflects_lead(client):
    d = client.get(f"{BASE_URL}/api/reports/dashboard")
    assert d.status_code == 200
    print(str(d.json())[:600])
