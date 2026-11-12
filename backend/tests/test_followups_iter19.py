# Iteration 19: auto follow-ups (FOLLOW_UPS / seed_followups_v1 / run_followups), Abhinav default signature,
# mailboxes.senders() signature fallback, _send_one signature append, template step/wait_days validation.
# SAFETY: no email is sent; core tests use a throw-away DB with every module's `db` monkeypatched.
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import email_templates as et, outreach as oc  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
AUDS = ["d2c", "stores_100", "stores_11_99", "stores_1_10"]


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


# ---------- live API ----------
class TestLiveApi:
    def test_main_signature_is_abhinav(self, admin):
        r = admin.get(f"{BASE_URL}/api/outreach/settings")
        assert r.status_code == 200
        sig = r.json().get("signature") or ""
        for s in ("Abhinav Khanna", "CBO | Fundle.AI", "9910530372", "https://fundle.ai", "linkedin.com/in/abhinav-khanna"):
            assert s in sig, f"missing {s} in {sig!r}"

    def test_followup_templates(self, admin):
        r = admin.get(f"{BASE_URL}/api/outreach/templates")
        assert r.status_code == 200
        by = {t.get("kind"): t for t in r.json()}
        for a in AUDS:
            for k, step, wd in ((f"fu2_{a}", 2, 3), (f"fu3_{a}", 3, 7)):
                t = by[k]
                assert t["mode"] == "auto" and t["ai_tailor"] is False and t["active"]
                assert t["step"] == step and t["wait_days"] == wd and t["audience"] == a
                assert t["subject"] == "Re: {{intro_subject}}"
        intros = [t for t in r.json() if t.get("kind", "").startswith("intro_") and t["mode"] == "auto"]
        assert len(intros) == 4

    def test_step_wait_validation_and_persist(self, admin):
        body = {"name": "TEST_fu19", "subject": "Re: {{intro_subject}}", "body": "<p>Hi {{first_name}}</p>",
                "mode": "manual", "ai_tailor": False, "audience": "d2c", "step": 2, "wait_days": 3, "active": False}
        r = admin.post(f"{BASE_URL}/api/outreach/templates", json=body)
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        try:
            assert r.json()["step"] == 2 and r.json()["wait_days"] == 3
            r = admin.put(f"{BASE_URL}/api/outreach/templates/{tid}", json={**body, "name": "TEST_fu19b", "step": 3, "wait_days": 9})
            assert r.status_code == 200 and r.json()["step"] == 3 and r.json()["wait_days"] == 9
            got = {t["id"]: t for t in admin.get(f"{BASE_URL}/api/outreach/templates").json()}[tid]
            assert got["step"] == 3 and got["wait_days"] == 9 and got["name"] == "TEST_fu19b"
            for bad in ({"step": 4}, {"step": 0}, {"wait_days": 61}, {"wait_days": -1}):
                r = admin.put(f"{BASE_URL}/api/outreach/templates/{tid}", json={**body, **bad})
                assert r.status_code == 400, (bad, r.status_code)
        finally:
            assert admin.delete(f"{BASE_URL}/api/outreach/templates/{tid}").status_code == 200


# ---------- core unit tests (isolated DB) ----------
class TestCoreFollowups:
    @pytest.fixture(autouse=True)
    def tdb(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t19_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        from core import prospector as _pe, utils as _cu, mailboxes as _mb
        for mod in (et, oc, _pe, _cu, _mb):
            monkeypatch.setattr(mod, "db", d)
        ctr = {"n": 1000}

        async def nid(_):
            ctr["n"] += 1
            return ctr["n"]
        monkeypatch.setattr(et, "next_id", nid)
        monkeypatch.setattr(_pe, "next_id", nid)

        async def no_ai(*a, **k):
            raise AssertionError("AI must not be called")
        monkeypatch.setattr(et, "ai_text", no_ai)
        self.db = d
        run(et.seed_starters())
        run(et.seed_intro_v2())
        run(et.seed_followups_v1())
        self.t = {t["kind"]: t for t in run(d.email_templates.find({}, {"_id": 0}).to_list(50))}
        yield d
        run(cli.drop_database(name))

    def _p(self, i, **kw):
        return {"id": i, "brand_name": f"TEST_{i}", "lead_id": 5000 + i, "fit_score": 50, "category": "c",
                "segment": "Omnichannel", "store_count": 150,
                "contacts": [{"name": f"Ann{i} Lee", "email": f"a{i}@x.com"}, {"name": "Bob", "email": f"b{i}@x.com"}], **kw}

    def _intro(self, oid, pid, days, kind="intro_100", to=None, **kw):
        return {"id": oid, "seq_id": 900 + oid, "channel": "email", "step": 1, "kind": "template",
                "template_id": self.t[kind]["id"], "prospect_id": pid, "brand": f"TEST_{pid}", "to": to or f"b{pid}@x.com",
                "contact_name": "Bob", "subject": f"TEST_{pid} x Fundle.AI | Demo", "body": "<p>x</p>", "status": "sent",
                "sent_at": ago(days), "sent_via": 0, **kw}

    def test_seed_followups_and_signature(self):
        assert sum(1 for k in self.t if k.startswith("fu")) == 8
        st = run(self.db.settings.find_one({"key": "outreach"}))
        assert st["signature"] == et.ABHINAV_SIG
        run(et.seed_followups_v1())
        assert run(self.db.email_templates.count_documents({"kind": {"$regex": "^fu"}})) == 8

    def test_seed_keeps_existing_signature(self):
        run(self.db.settings.delete_many({"key": {"$in": ["tpl_followups_v1", "outreach"]}}))
        run(self.db.settings.insert_one({"key": "outreach", "signature": "Custom sig"}))
        run(et.seed_followups_v1())
        assert run(self.db.settings.find_one({"key": "outreach"}))["signature"] == "Custom sig"

    def test_step2_created_after_3_days(self):
        run(self.db.prospects.insert_many([self._p(1), self._p(2)]))
        run(self.db.outreach.insert_many([self._intro(1, 1, 3.2), self._intro(2, 2, 2)]))
        assert run(et.run_followups(30)) == 1
        fu = run(self.db.outreach.find({"step": 2}, {"_id": 0}).to_list(10))
        assert len(fu) == 1
        m = fu[0]
        assert m["prospect_id"] == 1 and m["to"] == "b1@x.com" and m["seq_id"] == 901
        assert m["subject"] == "Re: TEST_1 x Fundle.AI | Demo" and m["status"] == "approved"
        assert m["template_id"] == self.t["fu2_stores_100"]["id"] and "Dear Bob" in m["body"] and "{{" not in m["body"]
        assert run(et.run_followups(30)) == 0, "no duplicates on second run"
        assert run(self.db.outreach.count_documents({"step": 2})) == 1

    def test_step3_needs_step2_sent_and_7_days(self):
        run(self.db.prospects.insert_many([self._p(1), self._p(2)]))
        run(self.db.outreach.insert_many([self._intro(1, 1, 8), self._intro(2, 2, 5)]))
        run(et.run_followups(30))
        assert run(self.db.outreach.count_documents({"step": 3})) == 0, "step 2 not sent yet -> no step 3"
        assert run(self.db.outreach.count_documents({"step": 2})) == 2
        run(self.db.outreach.update_many({"step": 2}, {"$set": {"status": "sent", "sent_at": ago(1)}}))
        run(et.run_followups(30))
        s3 = run(self.db.outreach.find({"step": 3}, {"_id": 0}).to_list(10))
        assert [m["prospect_id"] for m in s3] == [1], "only intro 8 days old gets step 3"
        assert s3[0]["subject"] == "Re: TEST_1 x Fundle.AI | Demo" and s3[0]["seq_id"] == 901
        assert run(et.run_followups(30)) == 0

    def test_replied_and_bounced_skipped(self):
        run(self.db.prospects.insert_many([self._p(1, replied=True), self._p(2), self._p(3, bounced_emails=["B3@x.com"]),
                                           self._p(4)]))
        run(self.db.outreach.insert_many([self._intro(1, 1, 4), self._intro(2, 2, 4, replied_at=ago(1)),
                                          self._intro(3, 3, 4), self._intro(4, 4, 4, status="bounced")]))
        assert run(et.run_followups(30)) == 0
        assert run(self.db.outreach.count_documents({"step": {"$gt": 1}})) == 0

    def test_audience_routing(self):
        run(self.db.prospects.insert_many([self._p(1, segment="D2C / E-commerce"), self._p(2, store_count=20),
                                           self._p(3, store_count=None)]))
        run(self.db.outreach.insert_many([self._intro(1, 1, 4, kind="intro_d2c"), self._intro(2, 2, 4, kind="intro_11_99"),
                                          self._intro(3, 3, 4, kind="intro_1_10")]))
        assert run(et.run_followups(30)) == 3
        got = {m["prospect_id"]: m["template_id"] for m in run(self.db.outreach.find({"step": 2}).to_list(10))}
        assert got == {1: self.t["fu2_d2c"]["id"], 2: self.t["fu2_stores_11_99"]["id"], 3: self.t["fu2_stores_1_10"]["id"]}

    def test_followups_not_cold_intros(self):
        """run_auto: follow-up templates never go out as cold step-1 emails; only one intro per brand."""
        run(self.db.prospects.insert_many([self._p(1), self._p(2, segment="D2C / E-commerce")]))
        run(et.run_auto(50))
        ms = run(self.db.outreach.find({}, {"_id": 0}).to_list(50))
        fu_ids = {t["id"] for k, t in self.t.items() if k.startswith("fu")}
        assert len(ms) == 2 and all(m["template_id"] not in fu_ids and m["step"] == 1 for m in ms)
        assert run(et.run_auto(50)) == 0

    def test_starvation_other_audience(self):
        """Many intros of OTHER audiences / replied brands must not starve eligible ones (limit+50 window)."""
        ps = [self._p(i, segment="D2C / E-commerce") for i in range(1, 121)] + [self._p(500)]
        run(self.db.prospects.insert_many(ps))
        rows = [self._intro(i, i, 4, kind="intro_d2c") for i in range(1, 121)]
        run(self.db.prospects.update_many({"id": {"$lte": 120}}, {"$set": {"replied": True}}))
        rows.append(self._intro(500, 500, 4))
        run(self.db.outreach.insert_many(rows))
        run(et.run_followups(10))
        got = run(self.db.outreach.count_documents({"step": 2, "prospect_id": 500}))
        assert got == 1, "eligible 100+ store intro starved by skipped rows that are never marked"

    def test_refresh_after_template_edit_keeps_intro_subject(self):
        """After a follow-up template is edited, queued follow-ups are re-merged by _refresh_from_template."""
        run(self.db.prospects.insert_one(self._p(1)))
        run(self.db.outreach.insert_one(self._intro(1, 1, 4)))
        run(et.run_followups(30))
        m = run(self.db.outreach.find_one({"step": 2}, {"_id": 0}))
        run(self.db.email_templates.update_one({"id": m["template_id"]}, {"$set": {"updated_at": "2099-01-01 00:00:00"}}))
        m2 = run(oc._refresh_from_template(m))
        assert m2["subject"] == "Re: TEST_1 x Fundle.AI | Demo", f"subject became {m2['subject']!r}"

    def test_senders_signature_fallback(self, monkeypatch):
        from core import mailboxes as _mb, mailer, outlook_send as ol

        async def yes():
            return True

        async def cfg():
            return {"email": "abhinav@fundle.ai"}
        monkeypatch.setattr(mailer, "is_connected", yes)
        monkeypatch.setattr(ol, "get_config", cfg)
        run(self.db.mailboxes.insert_many([
            {"id": 1, "smtp_user": "x1@f.ai", "active": True, "verified": True, "signature": "Own sig"},
            {"id": 2, "smtp_user": "x2@f.ai", "active": True, "verified": True, "signature": ""},
            {"id": 3, "smtp_user": "x3@f.ai", "active": True, "verified": True}]))
        st = {**oc.DEFAULTS, "signature": et.ABHINAV_SIG}
        pool = {s["id"]: s for s in run(_mb.senders(st, ago(0)[:10]))}
        assert pool[0]["signature"] == et.ABHINAV_SIG and pool[0]["cfg"] is None
        assert pool[1]["signature"] == "Own sig"
        assert pool[2]["signature"] == et.ABHINAV_SIG and pool[3]["signature"] == et.ABHINAV_SIG

    def test_send_one_primary_appends_signature(self, monkeypatch):
        from core import mailboxes as _mb
        sent = []

        async def pick(pid, pl, force=False):
            return pl[0]

        async def send(s, to, subject, body, cc=None, attachments=None):
            sent.append((to, subject, body))
            return {"ok": True}

        async def log(*a, **k):
            return None
        monkeypatch.setattr(_mb, "pick", pick)
        monkeypatch.setattr(_mb, "send", send)
        monkeypatch.setattr(oc, "log_message", log)
        pool = [{"id": 0, "email": "abhinav@fundle.ai", "remaining": 5, "cfg": None, "signature": et.ABHINAV_SIG}]
        m = {"id": 1, "seq_id": 1, "step": 2, "channel": "email", "to": "a@x.com", "contact_name": "A",
             "subject": "Re: S", "body": "<p>B</p>", "status": "sending", "lead_id": 5}
        run(self.db.outreach.insert_one(dict(m)))
        assert run(oc._send_one(m, {**oc.DEFAULTS, "signature": et.ABHINAV_SIG}, pool)) is True
        assert "Abhinav Khanna<br>CBO | Fundle.AI<br>M: 9910530372" in sent[0][2]
        assert run(self.db.outreach.find_one({"id": 1}))["status"] == "sent"
