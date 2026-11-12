# Iteration 17: POST /api/outreach/mark-bounced (brand list -> bounced, queue stopped, lead + log), contacts priority
# queue + find_contacts lead upsert/log/email_from swap, enrich always upserting the lead, create_message creating the
# lead, PUT /api/outreach/signature + settings not wiping it, GET/PUT /api/admin/mailboxes/read-app, iter16 fixes.
# SAFETY: API tests only insert TEST_ prospects/outreach/leads (status 'testing', replied=True so no worker/auto
# emailer picks them) and delete them by id. Unit tests use a throw-away DB that is dropped afterwards.
import asyncio
import json
import os
import random
import sys
import uuid

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import outreach as oc, mailboxes as mb, utils as cu, email_templates as et  # noqa: E402
from core import inbox_sync as ib, contacts as ct, prospector as pe, outlook_send as ol  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LIVE = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
LOOP = asyncio.new_event_loop()


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


@pytest.fixture(scope="module")
def made():
    c = {"prospects": [], "outreach": [], "leads": []}
    yield c
    lids = set(c["leads"]) | {p.get("lead_id") for p in LIVE.prospects.find({"id": {"$in": c["prospects"]}}, {"lead_id": 1}) if p.get("lead_id")}
    LIVE.messages.delete_many({"lead_id": {"$in": list(lids)}})
    LIVE.leads.delete_many({"id": {"$in": list(lids)}, "name": {"$regex": "^TEST_"}})
    LIVE.outreach.delete_many({"id": {"$in": c["outreach"]}})
    LIVE.prospects.delete_many({"id": {"$in": c["prospects"]}, "brand_name": {"$regex": "^TEST_"}})


def _prospect(made, name, website=None, **kw):
    pid = 9_800_000 + random.randint(1, 199_999)
    doc = {"id": pid, "brand_name": name, "brand_key": pe.brand_key(name), "category": "Apparel", "country": "India",
           "website": website, "status": "testing", "replied": True, "tpl_sent": [11, 12], "contacts_status": "done",
           "contacts": [{"name": "Asha Rao", "role": "CMO", "email": "Asha@test-bnc.example"},
                        {"name": "No Mail", "role": "CEO", "email": None}], **kw}
    LIVE.prospects.insert_one(dict(doc))
    made["prospects"].append(pid)
    return doc


def _msg(made, pid, status, to="sent@test-bnc.example", **kw):
    mid = 9_800_000 + random.randint(1, 199_999)
    LIVE.outreach.insert_one({"id": mid, "seq_id": mid, "channel": "email", "step": 1, "kind": "template", "template_id": 11,
                              "prospect_id": pid, "brand": "TEST_", "to": to, "status": status, "created_by": "TEST", **kw})
    made["outreach"].append(mid)
    return mid


# ---------------- mark-bounced API ----------------
class TestMarkBounced:
    def test_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/outreach/mark-bounced", json={"brands": ["x"]})
        assert r.status_code in (401, 403)

    def test_exact_match_full_flow_creates_lead(self, s, made):
        tag = uuid.uuid4().hex[:6]
        p = _prospect(made, f"TEST_Bnc Exact {tag}", contact_email="sent@test-bnc.example")
        sent = _msg(made, p["id"], "sent")
        q1 = _msg(made, p["id"], "approved")
        q2 = _msg(made, p["id"], "pending_approval")
        r = s.post(f"{BASE_URL}/api/outreach/mark-bounced", json={"brands": [p["brand_name"], "  "]})
        assert r.status_code == 200, r.text
        d = r.json()
        assert p["brand_name"] in d["matched"] and d["not_found"] == []
        o = {m["id"]: m for m in LIVE.outreach.find({"id": {"$in": [sent, q1, q2]}})}
        assert o[sent]["status"] == "bounced" and o[sent].get("bounced_at")
        assert o[q1]["status"] == "stopped" and o[q2]["status"] == "stopped" and "bounced" in o[q1]["stop_reason"].lower()
        pr = LIVE.prospects.find_one({"id": p["id"]})
        assert set(pr["bounced_emails"]) >= {"asha@test-bnc.example", "sent@test-bnc.example"}
        assert pr["tpl_sent"] == [] and pr["contacts_priority"] == 1 and "contacts_status" not in pr
        assert pr.get("lead_id"), "lead must be created"
        lead = LIVE.leads.find_one({"id": pr["lead_id"]})
        assert lead and lead.get("email_from") is None, f"bounced email_from must be cleared: {lead.get('email_from')}"
        logs = [m["body"] for m in LIVE.messages.find({"lead_id": pr["lead_id"]})]
        assert any("Emails bounced" in b and "sent@test-bnc.example" in b for b in logs), logs

    def test_existing_lead_logged_and_email_cleared(self, s, made):
        tag = uuid.uuid4().hex[:6]
        lid = 9_800_000 + random.randint(1, 199_999)
        LIVE.leads.insert_one({"id": lid, "name": f"TEST_Bnc Lead {tag}", "email_from": "sent@test-bnc.example", "active": True})
        made["leads"].append(lid)
        p = _prospect(made, f"TEST_Bnc Lead {tag}", lead_id=lid)
        _msg(made, p["id"], "sent")
        d = s.post(f"{BASE_URL}/api/outreach/mark-bounced", json={"brands": [p["brand_name"]]}).json()
        assert p["brand_name"] in d["matched"]
        assert LIVE.leads.find_one({"id": lid})["email_from"] is None
        assert LIVE.messages.count_documents({"lead_id": lid, "body": {"$regex": "Emails bounced"}}) == 1

    def test_prefix_and_website_match(self, s, made):
        tag = uuid.uuid4().hex[:8]
        a = _prospect(made, f"TEST_zqpre{tag} Foods India")                      # brand_key prefix
        b = _prospect(made, f"TEST_Other {tag}", website=f"https://www.zqweb{tag}.example")  # website contains key
        d = s.post(f"{BASE_URL}/api/outreach/mark-bounced",
                   json={"brands": [f"TEST_zqpre{tag}", f"zqweb{tag}"]}).json()
        assert a["brand_name"] in d["matched"] and b["brand_name"] in d["matched"], d

    def test_not_found_and_short_keys_ignored(self, s):
        d = s.post(f"{BASE_URL}/api/outreach/mark-bounced",
                   json={"brands": ["TEST_NoSuchBrand_zz9q", "ab", "!!"]}).json()
        assert d["matched"] == [] and d["not_found"] == ["TEST_NoSuchBrand_zz9q"], d

    def test_exact_match_also_marks_unrelated_prefix_brands(self, s, made):
        """Documents a risk: an exact brand also drags in any brand whose key starts with it (e.g. 'Max' -> Maxima...)."""
        tag = uuid.uuid4().hex[:6]
        exact = _prospect(made, f"TEST_Fp{tag}")
        other = _prospect(made, f"TEST_Fp{tag} Watches Pvt")
        d = s.post(f"{BASE_URL}/api/outreach/mark-bounced", json={"brands": [exact["brand_name"]]}).json()
        assert exact["brand_name"] in d["matched"]
        assert other["brand_name"] not in d["matched"], f"unrelated brand also marked bounced: {d['matched']}"


# ---------------- signature + read-app API ----------------
class TestSettingsAPI:
    def test_signature_endpoint_and_settings_keep_it(self, s):
        cur = s.get(f"{BASE_URL}/api/outreach/settings").json()
        orig = cur.get("signature") or ""
        body = {k: v for k, v in cur.items() if k not in ("mail_connected", "mail_provider", "last_run", "sender_name", "signature")}
        try:
            r = s.put(f"{BASE_URL}/api/outreach/signature", json={"signature": "  TEST_Sig17\nLine2  "})
            assert r.status_code == 200 and r.json()["signature"] == "TEST_Sig17\nLine2"
            r = s.put(f"{BASE_URL}/api/outreach/settings", json=body)   # no signature key
            assert r.status_code == 200 and r.json()["signature"] == "TEST_Sig17\nLine2"
            assert s.get(f"{BASE_URL}/api/outreach/settings").json()["signature"] == "TEST_Sig17\nLine2"
        finally:
            s.put(f"{BASE_URL}/api/outreach/signature", json={"signature": orig})
        assert (s.get(f"{BASE_URL}/api/outreach/settings").json()["signature"] or "") == orig

    def test_signature_admin_only_unauth(self):
        assert requests.put(f"{BASE_URL}/api/outreach/signature", json={"signature": "x"}).status_code in (401, 403)

    def test_read_app_get_put_keeps_send_method(self, s):
        before_doc = LIVE.settings.find_one({"key": "outlook"})
        prov = s.get(f"{BASE_URL}/api/outreach/settings").json().get("mail_provider")
        try:
            g = s.get(f"{BASE_URL}/api/admin/mailboxes/read-app")
            assert g.status_code == 200 and set(g.json()) >= {"tenant_id", "client_id", "has_client_secret"}
            assert s.put(f"{BASE_URL}/api/admin/mailboxes/read-app", json={"client_id": "  "}).status_code == 400
            r = s.put(f"{BASE_URL}/api/admin/mailboxes/read-app",
                      json={"tenant_id": "TEST-tenant", "client_id": "TEST-client-17", "client_secret": "TEST-secret"})
            assert r.status_code == 200 and r.json()["ok"] is True
            g = s.get(f"{BASE_URL}/api/admin/mailboxes/read-app").json()
            assert g["client_id"] == "TEST-client-17" and g["tenant_id"] == "TEST-tenant" and g["has_client_secret"] is True
            assert s.get(f"{BASE_URL}/api/outreach/settings").json().get("mail_provider") == prov
            doc = LIVE.settings.find_one({"key": "outlook"})
            assert (before_doc or {}).get("method") == doc.get("method")
            u = s.get(f"{BASE_URL}/api/admin/mailboxes/1/read-auth-url", params={"origin": BASE_URL})
            assert u.status_code in (200, 404)
        finally:
            if before_doc is None:
                LIVE.settings.delete_one({"key": "outlook", "client_id": "TEST-client-17"})
            else:
                LIVE.settings.replace_one({"_id": before_doc["_id"]}, before_doc)


# ---------------- unit tests (throw-away DB) ----------------
@pytest.fixture
def tdb(monkeypatch):
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])
    cli = run(mk())
    name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
    d = cli[name]
    for mod in (oc, mb, cu, et, ib, ct, ol, pe):
        monkeypatch.setattr(mod, "db", d)
    logs = []

    async def log(lead_id, body, author=None, **k):
        logs.append((lead_id, body))
    for mod in (oc, ib, ct, pe):
        monkeypatch.setattr(mod, "log_message", log)

    async def lr(*a, **k):
        return None
    monkeypatch.setattr(pe, "_log_run", lr)
    d.logs = logs
    yield d
    run(cli.drop_database(name))


class TestUnits:
    def test_contacts_priority_first(self, tdb):
        run(tdb.prospects.insert_many([
            {"id": 1, "status": "enriched", "store_count": 999},
            {"id": 2, "status": "enriched", "store_count": 1, "contacts_priority": 1},
            {"id": 3, "status": "discovered", "store_count": 5000, "contacts_priority": 1}]))
        got = [run(ct.next_contacts_job())["id"] for _ in range(3)]
        assert set(got[:2]) == {2, 3} and got[2] == 1, got

    def test_find_contacts_upserts_lead_logs_and_swaps_bounced(self, tdb, monkeypatch):
        cs = [{"name": "Old", "role": "CMO", "email": "old@x.com", "email_status": "published"},
              {"name": "New Person", "role": "CEO", "email": "new@x.com", "email_status": "published"}]

        async def ask(*a):
            return json.dumps({"email_pattern": None, "contacts": cs})

        async def mx(dom):
            return True
        monkeypatch.setattr(pe, "_ask", ask)
        monkeypatch.setattr(ct, "_mx_ok", mx)
        run(tdb.leads.insert_one({"id": 50, "name": "X", "email_from": "old@x.com"}))
        p = {"id": 1, "brand_name": "X", "category": "c", "country": "IN", "website": "x.com", "lead_id": 50,
             "bounced_emails": ["old@x.com"], "contacts_priority": 1}
        run(tdb.prospects.insert_one(dict(p)))
        assert run(ct.find_contacts(p))["ok"] is True
        pr = run(tdb.prospects.find_one({"id": 1}))
        assert pr["contacts_priority"] == 0 and [c["email"] for c in pr["contacts"]] == [None, "new@x.com"]
        assert run(tdb.leads.find_one({"id": 50}))["email_from"] == "new@x.com"
        assert any(lid == 50 and "AI contact search" in b and "new@x.com" in b for lid, b in tdb.logs), tdb.logs

    def test_find_contacts_creates_lead_when_missing(self, tdb, monkeypatch):
        async def ask(*a):
            return json.dumps({"contacts": [{"name": "A B", "role": "CEO", "email": "a@y.com"}]})

        async def mx(dom):
            return False
        monkeypatch.setattr(pe, "_ask", ask)
        monkeypatch.setattr(ct, "_mx_ok", mx)
        p = {"id": 2, "brand_name": "Y", "category": "c", "country": "IN", "website": "y.com"}
        run(tdb.prospects.insert_one(dict(p)))
        run(ct.find_contacts(p))
        pr = run(tdb.prospects.find_one({"id": 2}))
        assert pr.get("lead_id") and run(tdb.leads.find_one({"id": pr["lead_id"]}))["email_from"] == "a@y.com"

    def test_enrich_always_upserts_lead_and_logs(self, tdb, monkeypatch):
        async def ask(*a):
            return json.dumps({"store_count": 40, "ceo_name": "Raj K", "summary": "s"})
        monkeypatch.setattr(pe, "_ask", ask)
        p = {"id": 3, "brand_name": "Z", "category": "c", "country": "IN", "status": "enriching"}
        run(tdb.prospects.insert_one(dict(p)))
        run(tdb.settings.insert_one({"key": "prospecting", "create_leads": False}))
        assert run(pe.enrich(p))["ok"] is True
        pr = run(tdb.prospects.find_one({"id": 3}))
        assert pr.get("lead_id") and pr["status"] == "enriched"
        assert any(lid == pr["lead_id"] and "AI deep research done" in b for lid, b in tdb.logs), tdb.logs

    def test_create_message_creates_lead(self, tdb, monkeypatch):
        async def no_ai(*a, **k):
            raise RuntimeError("off")
        monkeypatch.setattr(et, "ai_text", no_ai)
        p = {"id": 4, "brand_name": "W", "category": "c", "country": "IN"}
        run(tdb.prospects.insert_one(dict(p)))
        t = {"id": 9, "name": "T", "subject": "Hi {{brand}}", "body": "<p>x</p>"}
        doc = run(et.create_message(t, p, {"name": "A", "email": "a@w.com"}, "pending_approval", "TEST"))
        pr = run(tdb.prospects.find_one({"id": 4}))
        assert doc["lead_id"] and doc["lead_id"] == pr["lead_id"]

    def test_warm_cap_and_sent_today(self, tdb):
        assert mb.warm_cap({"daily_cap": 150}, "2026-01-10") == 150
        run(tdb.outreach.insert_many([
            {"id": 1, "channel": "email", "status": "sent", "sent_at": "2026-01-10 01:00:00", "sent_via": 3},
            {"id": 2, "channel": "email", "status": "bounced", "sent_at": "2026-01-10 02:00:00", "sent_via": 3},
            {"id": 3, "channel": "email", "status": "failed", "sent_at": "2026-01-10 02:00:00", "sent_via": 3}]))
        assert run(mb._sent_today("2026-01-10")) == {3: 2}

    @pytest.mark.parametrize("subj", ["Automatic reply: Hello", "Out of office: back Monday", "OOO until 5th"])
    def test_reply_skips_auto_replies(self, tdb, subj):
        run(tdb.outreach.insert_one({"id": 1, "channel": "email", "status": "sent", "to": "a@brand.com", "prospect_id": 1,
                                     "sent_at": "2026-01-01"}))
        m = {"from": {"emailAddress": {"address": "a@brand.com"}}, "subject": subj}
        assert run(ib._reply(m, "2026-01-02")) == 0

    def test_reply_freemail_no_domain_fallback_and_latest_only(self, tdb):
        run(tdb.outreach.insert_many([
            {"id": 1, "channel": "email", "status": "sent", "to": "x@gmail.com", "prospect_id": 1, "sent_at": "2026-01-01"},
            {"id": 2, "channel": "email", "status": "sent", "to": "a@brand.com", "prospect_id": 2, "sent_at": "2026-01-01", "lead_id": 7},
            {"id": 3, "channel": "email", "status": "sent", "to": "a@brand.com", "prospect_id": 2, "sent_at": "2026-01-05", "lead_id": 7}]))
        assert run(ib._reply({"from": {"emailAddress": {"address": "other@gmail.com"}}, "subject": "hi"}, "n")) == 0
        assert run(ib._reply({"from": {"emailAddress": {"address": "boss@brand.com"}}, "subject": "Re: hi"}, "n")) == 1
        got = {m["id"]: m.get("replied_at") for m in run(tdb.outreach.find({}).to_list(10))}
        assert got == {1: None, 2: None, 3: "n"}
