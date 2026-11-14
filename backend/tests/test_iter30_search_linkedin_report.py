# Iter30: lead word-search, country/sender/source filters, LinkedIn contacts report (mark sent, template)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}


@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=30)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


def _items(r):
    d = r.json()
    return d.get("items") if isinstance(d, dict) else d


def _names(items):
    return [((x.get("name") or "") + " | " + (x.get("contact_name") or "") + " | " + (x.get("company_name") or "")).lower() for x in items]


# --- Leads search by any word ---
class TestLeadSearch:
    @pytest.mark.parametrize("q", ["bansal", "deepak bansal", "Bansal"])
    def test_bansal_finds_cantabil(self, s, q):
        r = s.get(f"{BASE_URL}/api/leads", params={"search": q, "limit": 50}, timeout=30)
        assert r.status_code == 200, r.text
        names = _names(_items(r))
        assert any("cantabil" in n for n in names), names

    def test_raymond(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"search": "raymond", "limit": 50}, timeout=30)
        assert r.status_code == 200
        assert any("raymond" in n for n in _names(_items(r)))

    def test_nonsense_search_empty(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"search": "zzqxjunkword", "limit": 50}, timeout=30)
        assert r.status_code == 200
        assert _items(r) == []


# --- Country / source / sender filters ---
class TestLeadFilters:
    def test_country_uae(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"country": "UAE", "limit": 50, "scope": "all"}, timeout=30)
        assert r.status_code == 200
        items = _items(r)
        assert items
        # list projection has no 'country'; total is lazy (-1). Verify via cities instead.
        cities = {x.get("city") for x in items}
        print("UAE cities", cities)
        assert not cities & {"Kuwait City", "Doha", "Riyadh", "Muscat", "Manama", "Mumbai", "Delhi"}

    def test_source_ai_prospecting(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"source_lead": "AI Prospecting", "limit": 20, "scope": "all"}, timeout=30)
        assert r.status_code == 200
        for x in _items(r):
            assert (x.get("source_lead") or "").lower() == "ai prospecting"

    def test_senders(self, s):
        r = s.get(f"{BASE_URL}/api/leads/senders", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        print("senders", r.json())

    def test_sender_0(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"sender": "0", "limit": 20}, timeout=30)
        assert r.status_code == 200, r.text

    def test_sender_non_int(self, s):
        r = s.get(f"{BASE_URL}/api/leads", params={"sender": "abc", "limit": 20}, timeout=30)
        print("sender=abc ->", r.status_code)
        assert r.status_code in (200, 400, 422), r.status_code


# --- LinkedIn report ---
class TestLinkedInReport:
    def test_report_basic(self, s):
        r = s.get(f"{BASE_URL}/api/linkedin/report", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d["items"], list) and d["total"] >= len(d["items"])
        assert d["template"]
        assert d["items"], "no contacts"
        x = d["items"][0]
        for k in ("prospect_id", "brand", "name"):
            assert k in x

    def test_source_sheet(self, s):
        d = s.get(f"{BASE_URL}/api/linkedin/report", params={"source": "sheet"}, timeout=60).json()
        assert all(x.get("uploaded") for x in d["items"])

    def test_search(self, s):
        d = s.get(f"{BASE_URL}/api/linkedin/report", timeout=60).json()
        brand = d["items"][0]["brand"]
        d2 = s.get(f"{BASE_URL}/api/linkedin/report", params={"q": brand}, timeout=60).json()
        assert d2["total"] >= 1
        assert any(x["brand"] == brand for x in d2["items"])

    def test_pagination(self, s):
        p1 = s.get(f"{BASE_URL}/api/linkedin/report", params={"limit": 5, "page": 1}, timeout=60).json()
        p2 = s.get(f"{BASE_URL}/api/linkedin/report", params={"limit": 5, "page": 2}, timeout=60).json()
        if p1["total"] > 5:
            k1 = {(x["prospect_id"], x["name"]) for x in p1["items"]}
            k2 = {(x["prospect_id"], x["name"]) for x in p2["items"]}
            assert not (k1 & k2)

    def test_mark_sent_unmark(self, s):
        todo = s.get(f"{BASE_URL}/api/linkedin/report", params={"status": "todo", "limit": 5}, timeout=60).json()
        x = todo["items"][0]
        key = (x["prospect_id"], x["name"])
        try:
            r = s.post(f"{BASE_URL}/api/linkedin/report/sent", json={"prospect_id": x["prospect_id"], "name": x["name"], "sent": True}, timeout=30)
            assert r.status_code == 200
            sent = s.get(f"{BASE_URL}/api/linkedin/report", params={"status": "sent", "q": x["name"], "limit": 200}, timeout=60).json()
            m = [y for y in sent["items"] if (y["prospect_id"], y["name"]) == key]
            assert m and m[0]["li_sent_at"] and m[0]["li_sent_by"]
            todo2 = s.get(f"{BASE_URL}/api/linkedin/report", params={"status": "todo", "q": x["name"], "limit": 200}, timeout=60).json()
            assert not [y for y in todo2["items"] if (y["prospect_id"], y["name"]) == key]
        finally:
            r = s.post(f"{BASE_URL}/api/linkedin/report/sent", json={"prospect_id": x["prospect_id"], "name": x["name"], "sent": False}, timeout=30)
            assert r.status_code == 200
        todo3 = s.get(f"{BASE_URL}/api/linkedin/report", params={"status": "todo", "q": x["name"], "limit": 200}, timeout=60).json()
        assert [y for y in todo3["items"] if (y["prospect_id"], y["name"]) == key]

    def test_mark_sent_bad_prospect(self, s):
        r = s.post(f"{BASE_URL}/api/linkedin/report/sent", json={"prospect_id": 999999999, "name": "x", "sent": True}, timeout=30)
        assert r.status_code == 404

    def test_mark_sent_bad_name(self, s):
        x = s.get(f"{BASE_URL}/api/linkedin/report", params={"limit": 1}, timeout=60).json()["items"][0]
        r = s.post(f"{BASE_URL}/api/linkedin/report/sent", json={"prospect_id": x["prospect_id"], "name": "TEST_nobody_xyz", "sent": True}, timeout=30)
        print("bad name ->", r.status_code, r.text)
        assert r.status_code == 404, "returns ok for a contact name that does not exist"

    def test_template_persist(self, s):
        orig = s.get(f"{BASE_URL}/api/linkedin/report", params={"limit": 1}, timeout=60).json()["template"]
        try:
            r = s.put(f"{BASE_URL}/api/linkedin/report/template", json={"text": "TEST_ Hi {first_name} from {brand}"}, timeout=30)
            assert r.status_code == 200
            t = s.get(f"{BASE_URL}/api/linkedin/report", params={"limit": 1}, timeout=60).json()["template"]
            assert t == "TEST_ Hi {first_name} from {brand}"
        finally:
            s.put(f"{BASE_URL}/api/linkedin/report/template", json={"text": orig}, timeout=30)

    def test_unauth(self):
        r = requests.get(f"{BASE_URL}/api/linkedin/report", timeout=30)
        assert r.status_code in (401, 403)
