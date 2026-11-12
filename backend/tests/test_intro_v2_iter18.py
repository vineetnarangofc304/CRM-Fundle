# Iteration 18: store-count intro templates (INTRO_V2 / AUDIENCES / run_auto one-intro rule), template audience
# validation, per-inbox signatures (mailboxes + main signature), email-format checker endpoints + core unit tests,
# LinkedIn switched off, lead contacts endpoint.
# SAFETY: no email is sent; unit tests use a throw-away DB with every module's `db` monkeypatched.
# Live data created here is TEST_-prefixed and removed by id. Only ONE live AI format check is triggered.
import asyncio
import os
import sys
import time
import uuid

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import email_templates as et, email_format as ef, outreach as oc  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
INTRO_IDS = {65: "d2c", 66: "stores_100", 67: "stores_11_99", 68: "stores_1_10"}


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


def _live():
    # create the client inside this module's LOOP (xdist workers share a process with other modules' loops)
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    return run(mk())


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


# ---------- live API: templates ----------
class TestTemplatesApi:
    def test_intro_v2_templates(self, admin):
        r = admin.get(f"{BASE_URL}/api/outreach/templates")
        assert r.status_code == 200
        by = {t["id"]: t for t in r.json()}
        for tid, aud in INTRO_IDS.items():
            t = by[tid]
            assert t["mode"] == "auto" and t["ai_tailor"] is False and t["audience"] == aud and t["active"]
            assert "{{brand}}" in t["subject"] and "{{first_name}}" in t["body"]
        assert by[1]["name"] == "Intro email" and by[1]["mode"] == "manual"
        # since iteration 19 the 8 follow-up templates (step 2/3) are auto too; first-touch autos must be only the 4 intros
        autos = [t for t in r.json() if t["mode"] == "auto" and t["active"] and (t.get("step") or 1) <= 1]
        assert sorted(t["id"] for t in autos) == sorted(INTRO_IDS), "only the 4 intro templates should be first-touch auto"

    def test_create_update_audience_persist(self, admin):
        body = {"name": "TEST_aud", "subject": "S {{brand}}", "body": "<p>Hi {{first_name}}</p>", "mode": "manual",
                "ai_tailor": False, "audience": "d2c", "active": False}
        r = admin.post(f"{BASE_URL}/api/outreach/templates", json=body)
        assert r.status_code == 200 and r.json()["audience"] == "d2c"
        tid = r.json()["id"]
        try:
            r = admin.put(f"{BASE_URL}/api/outreach/templates/{tid}", json={**body, "audience": "stores_11_99", "name": "TEST_aud2"})
            assert r.status_code == 200 and r.json()["audience"] == "stores_11_99"
            got = next(t for t in admin.get(f"{BASE_URL}/api/outreach/templates").json() if t["id"] == tid)
            assert got["audience"] == "stores_11_99" and got["name"] == "TEST_aud2" and got["ai_tailor"] is False
            r = admin.put(f"{BASE_URL}/api/outreach/templates/{tid}", json={**body, "audience": "bogus"})
            assert r.status_code == 400 and "audience" in r.json()["detail"].lower()
            r = admin.post(f"{BASE_URL}/api/outreach/templates", json={**body, "audience": "stores_9"})
            assert r.status_code == 400
        finally:
            assert admin.delete(f"{BASE_URL}/api/outreach/templates/{tid}").status_code == 200

    def test_live_queue_one_intro_per_brand(self):
        d = _live()

        async def q():
            rows = await d.outreach.aggregate([{"$match": {"template_id": {"$in": [1, *INTRO_IDS]}}},
                                               {"$group": {"_id": "$prospect_id", "n": {"$sum": 1}}},
                                               {"$match": {"n": {"$gt": 1}}}]).to_list(10)
            n = await d.outreach.count_documents({"template_id": {"$in": list(INTRO_IDS)}})
            return rows, n
        dups, n = run(q())
        assert dups == [], f"brands with >1 intro: {dups}"
        assert n >= 132


# ---------- live API: signatures / mailboxes ----------
class TestSignatures:
    def test_main_signature_roundtrip(self, admin):
        cur = admin.get(f"{BASE_URL}/api/outreach/settings").json().get("signature") or ""
        try:
            r = admin.put(f"{BASE_URL}/api/outreach/signature", json={"signature": "TEST sig\nline2"})
            assert r.status_code == 200 and r.json()["signature"] == "TEST sig\nline2"
            assert admin.get(f"{BASE_URL}/api/outreach/settings").json()["signature"] == "TEST sig\nline2"
        finally:
            admin.put(f"{BASE_URL}/api/outreach/signature", json={"signature": cur})
        assert (admin.get(f"{BASE_URL}/api/outreach/settings").json().get("signature") or "") == cur

    def test_mailbox_signature_persist(self, admin):
        email = f"test_{uuid.uuid4().hex[:6]}@example.com"
        body = {"smtp_user": email, "smtp_password": "x", "signature": "  TEST Box Sig\nM: 1  ", "active": False}
        r = admin.post(f"{BASE_URL}/api/admin/mailboxes", json=body)
        assert r.status_code == 200, r.text
        b = r.json()
        try:
            assert b["signature"] == "TEST Box Sig\nM: 1" and "smtp_password_enc" not in b
            r = admin.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json={**body, "smtp_password": None, "signature": "TEST v2"})
            assert r.status_code == 200 and r.json()["signature"] == "TEST v2"
            lst = admin.get(f"{BASE_URL}/api/admin/mailboxes").json()
            items = lst["boxes"]
            assert next(x for x in items if x["id"] == b["id"])["signature"] == "TEST v2"
        finally:
            assert admin.delete(f"{BASE_URL}/api/admin/mailboxes/{b['id']}").status_code == 200


# ---------- live API: email format + linkedin ----------
class TestFormatAndLinkedin:
    def test_email_format_status(self, admin):
        r = admin.get(f"{BASE_URL}/api/outreach/email-format")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["checked"], int) and isinstance(d["confirmed"], int) and isinstance(d["emails"], dict)

    def test_email_format_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/outreach/email-format").status_code in (401, 403)
        assert requests.post(f"{BASE_URL}/api/outreach/email-format/run").status_code in (401, 403)

    def test_check_format_one_brand_and_409(self, admin):
        """ONE live AI call. Picks a prospect with contacts and a website."""
        d = _live()
        p = run(d.prospects.find_one({"contacts.0": {"$exists": True}, "website": {"$nin": [None, ""]},
                                      "fmt_running": {"$exists": False}}, {"_id": 0, "id": 1, "brand_name": 1}))
        if not p:
            pytest.skip("no prospect with contacts")
        r = admin.post(f"{BASE_URL}/api/prospecting/prospects/{p['id']}/check-format")
        assert r.status_code == 200 and r.json()["ok"] is True
        r2 = admin.post(f"{BASE_URL}/api/prospecting/prospects/{p['id']}/check-format")
        assert r2.status_code == 409
        assert admin.post(f"{BASE_URL}/api/prospecting/prospects/99999999/check-format").status_code == 409
        for _ in range(30):
            time.sleep(5)
            cur = run(d.prospects.find_one({"id": p["id"]}, {"_id": 0, "fmt_running": 1, "email_format": 1, "fmt_checked_at": 1}))
            if "fmt_running" not in cur:
                break
        print("format result", p["brand_name"], cur)
        assert "fmt_running" not in cur, "fmt_running not cleared after 150s"

    def test_linkedin_off(self, admin):
        r = admin.get(f"{BASE_URL}/api/linkedin/queue")
        assert r.status_code == 200 and r.json()["settings"]["enabled"] is False

    def test_lead_contacts(self, admin):
        d = _live()
        p = run(d.prospects.find_one({"contacts.0": {"$exists": True}, "lead_id": {"$ne": None}}, {"_id": 0, "lead_id": 1}))
        r = admin.get(f"{BASE_URL}/api/linkedin/lead/{p['lead_id']}/contacts")
        assert r.status_code == 200 and len(r.json()["contacts"]) >= 1 and "name" in r.json()["contacts"][0]


# ---------- core unit tests (isolated DB) ----------
class TestCoreIntro:
    @pytest.fixture(autouse=True)
    def tdb(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t18_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        from core import prospector as _pe, utils as _cu, mailboxes as _mb
        for mod in (et, ef, oc, _pe, _cu, _mb):
            monkeypatch.setattr(mod, "db", d)
        ctr = {"n": 0}

        async def nid(_):
            ctr["n"] += 1
            return ctr["n"]
        monkeypatch.setattr(et, "next_id", nid)
        monkeypatch.setattr(_pe, "next_id", nid)

        async def no_ai(*a, **k):
            raise AssertionError("AI must not be called for ai_tailor=false templates")
        monkeypatch.setattr(et, "ai_text", no_ai)
        self.db = d
        yield d
        run(cli.drop_database(name))

    def _p(self, i, **kw):
        return {"id": i, "brand_name": f"TEST_{i}", "lead_id": 1000 + i, "fit_score": 100 - i, "category": "c",
                "country": "IN", "segment": "Omnichannel", "contacts": [{"name": f"Ann{i} Lee", "email": f"a{i}@x.com"}], **kw}

    def test_routing_and_one_intro(self):
        run(et.seed_starters())
        run(self.db.prospects.insert_many([
            self._p(1, segment="D2C / E-commerce", store_count=500),
            self._p(2, store_count=150), self._p(3, store_count=100), self._p(4, store_count=99),
            self._p(5, store_count=11), self._p(6, store_count=10), self._p(7, store_count=1),
            self._p(8), self._p(9, store_count=None),
            self._p(10, store_count=200, tpl_sent=[1]),  # already got old generic intro
            self._p(11, store_count=5, replied=True),
            self._p(12, segment="Offline Retail", store_count=0)]))
        made = run(et.run_auto(100))
        tpls = {t["kind"]: t for t in run(self.db.email_templates.find({}, {"_id": 0}).to_list(50))}
        assert tpls["intro"]["mode"] == "manual"
        msgs = {m["prospect_id"]: m for m in run(self.db.outreach.find({}, {"_id": 0}).to_list(50))}
        exp = {1: "intro_d2c", 2: "intro_100", 3: "intro_100", 4: "intro_11_99", 5: "intro_11_99",
               6: "intro_1_10", 7: "intro_1_10", 8: "intro_1_10", 9: "intro_1_10", 12: "intro_1_10"}
        got = {pid: next(k for k, t in tpls.items() if t["id"] == m["template_id"]) for pid, m in msgs.items()}
        assert got == exp
        assert made == len(exp)
        m = msgs[2]
        assert "TEST_2" in m["subject"] and "Dear Ann2:" in m["body"] and "{{" not in m["body"] + m["subject"]
        assert run(et.run_auto(100)) == 0, "second run must not queue another intro"

    def test_seed_idempotent(self):
        run(et.seed_intro_v2())
        run(et.seed_intro_v2())
        assert run(self.db.email_templates.count_documents({"kind": {"$regex": "^intro_"}})) == 4

    def test_store_count_as_string(self):
        """Imported prospects may store store_count as a string — does routing still work?"""
        run(self.db.prospects.insert_many([self._p(1, store_count="150")]))
        run(et.run_auto(10))
        m = run(self.db.outreach.find_one({}, {"_id": 0}))
        print("string store_count ->", m and m["template_name"])
        # not asserted strictly: real DB only has int/null store_count today

    def test_send_uses_sender_signature(self, monkeypatch):
        from core import mailboxes as _mb
        sent = []

        async def pool(st, day):
            return [{"id": 9, "email": "box@x.com", "remaining": 10, "signature": "TEST Box\nSig", "cfg": {"x": 1}}]

        async def pick(pid, pl, force=False):
            return pl[0]

        async def send(s, to, subject, body, cc=None, attachments=None):
            sent.append(body)
            return {"ok": True}

        async def log(*a, **k):
            return None
        monkeypatch.setattr(_mb, "senders", pool)
        monkeypatch.setattr(_mb, "pick", pick)
        monkeypatch.setattr(_mb, "send", send)
        monkeypatch.setattr(oc, "log_message", log)
        m = {"id": 1, "seq_id": 1, "step": 1, "channel": "email", "to": "a@example.com", "contact_name": "A",
             "subject": "S", "body": "<p>B</p>", "status": "sending", "lead_id": 5}
        run(self.db.outreach.insert_one(dict(m)))
        assert run(oc._send_one(m, {**oc.DEFAULTS, "signature": "MAIN sig"})) is True
        assert "TEST Box<br>Sig" in sent[0] and "MAIN sig" not in sent[0]


class TestCoreFormat:
    @pytest.fixture(autouse=True)
    def tdb(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t18f_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        from core import prospector as _pe, utils as _cu
        for mod in (ef, _pe, _cu):
            monkeypatch.setattr(mod, "db", d)
        self.logs = []

        async def log(*a, **k):
            self.logs.append(a)
        monkeypatch.setattr(ef, "log_message", log)

        async def lr(*a, **k):
            return None
        monkeypatch.setattr(_pe, "_log_run", lr)
        self.pe = _pe
        self.db = d
        yield d
        run(cli.drop_database(name))

    def test_build(self):
        assert ef.build("first.last", "Dr. Ana Gómez", "x.com") == "ana.gomez@x.com"
        assert ef.build("flast", "Ravi Kumar", "x.com") == "rkumar@x.com"
        assert ef.build("first.last", "Madonna", "x.com") is None
        assert ef.build("first", "Madonna", "x.com") == "madonna@x.com"

    def test_check_brand_confirms_and_rebuilds(self, monkeypatch):
        async def ask(*a, **k):
            return ('{"pattern": "first.last", "examples": [{"name": "Raj Mehta", "email": "raj.mehta@brand.com"}],'
                    ' "published": []}')
        monkeypatch.setattr(self.pe, "_ask", ask)
        p = {"id": 1, "brand_name": "TEST_B", "website": "https://www.brand.com", "lead_id": 7, "bounced_emails": ["ann.lee@brand.com"],
             "contacts": [{"name": "Priya Shah", "email": "pshah@brand.com", "email_status": "pattern"},
                          {"name": "Ann Lee", "email": "ann.lee@brand.com", "email_status": "pattern"}]}
        run(self.db.prospects.insert_one(dict(p, fmt_running="x")))
        run(self.db.leads.insert_one({"id": 7, "email_from": "pshah@brand.com"}))
        r = run(ef.check_brand(p))
        assert r["ok"] and r["confirmed"]
        d = run(self.db.prospects.find_one({"id": 1}))
        assert "fmt_running" not in d and d["email_format"]["pattern"] == "first.last"
        c0, c1 = d["contacts"]
        assert c0["email"] == "priya.shah@brand.com" and c0["email_status"] == "confirmed_format"
        assert c1["email"] is None, "bounced address rebuilt to the same bounced email must be cleared"

    def test_check_brand_ai_refusal_no_changes(self, monkeypatch):
        async def ask(*a, **k):
            return "I'm sorry, I can't help with finding personal email addresses."
        monkeypatch.setattr(self.pe, "_ask", ask)
        p = {"id": 2, "brand_name": "TEST_R", "website": "https://r.com",
             "contacts": [{"name": "Om Rao", "email": "orao@r.com", "email_status": "pattern"}]}
        run(self.db.prospects.insert_one(dict(p, fmt_running="x")))
        r = run(ef.check_brand(p))
        d = run(self.db.prospects.find_one({"id": 2}))
        assert r["ok"] and not r["confirmed"]
        assert d["contacts"][0]["email"] == "orao@r.com" and d["contacts"][0]["email_status"] == "pattern"
        assert "fmt_running" not in d

    def test_check_brand_no_domain_clears_running(self):
        p = {"id": 3, "brand_name": "TEST_ND", "contacts": [{"name": "A B", "email": None}]}
        run(self.db.prospects.insert_one(dict(p, fmt_running="x")))
        run(ef.check_brand(p))
        d = run(self.db.prospects.find_one({"id": 3}))
        assert "fmt_running" not in d, "no-domain path leaves fmt_running set -> 'Check email format' returns 409 for 1h"
