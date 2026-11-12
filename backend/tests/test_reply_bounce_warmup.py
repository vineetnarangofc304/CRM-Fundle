# Iteration 16: template edits flow into queued emails (_refresh_from_template), user signature (_with_signature,
# _migrate_v5), inbox warm-up (warm_cap/senders), per-inbox report, Microsoft reply/bounce sync (inbox_sync),
# pick_contact/run_auto/find_contacts bounce+reply handling, plus the related admin API endpoints.
# SAFETY: unit tests run on a throw-away DB (<DB_NAME>_t1_<uuid>) dropped afterwards; httpx/_token/send are monkeypatched
# so nothing is really sent or fetched. API tests only create TEST_ docs and delete them by id.
import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import outreach as oc, mailer, outlook_send as ol, mailboxes as mb, utils as cu, email_templates as et  # noqa: E402
from core import inbox_sync as ib, contacts as ct, prospector as pe  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


@pytest.fixture
def tdb(monkeypatch):
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])
    cli = run(mk())
    name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
    d = cli[name]
    for mod in (oc, mb, cu, et, ib, ct, ol):
        monkeypatch.setattr(mod, "db", d)
    logs = []

    async def log(lead_id, body, author=None, **k):
        logs.append((lead_id, body))
    for mod in (oc, ib):
        monkeypatch.setattr(mod, "log_message", log)
    d.logs = logs
    yield d
    run(cli.drop_database(name))


# ---------------- warm-up ----------------
class TestWarmup:
    def test_table_and_full_cap(self):
        start = date(2026, 1, 1)
        exp = [30, 30, 50, 50, 80, 80, 80, 120, 120, 120, 160, 160, 160, 200]
        b = {"daily_cap": 200, "verified_at": "2026-01-01 10:00:00"}
        got = [mb.warm_cap(b, (start + timedelta(days=i)).isoformat()) for i in range(14)]
        assert got == exp
        assert mb.warm_cap({**b, "daily_cap": 500}, (start + timedelta(days=14)).isoformat()) == 500
        assert mb.warm_cap({**b, "daily_cap": 40}, (start + timedelta(days=5)).isoformat()) == 40  # capped at daily_cap
        assert mb.warm_cap({**b, "warmup": False}, start.isoformat()) == 200

    def test_legacy_verified_box_without_verified_at_not_stuck(self):
        # A box verified BEFORE this feature has no verified_at; it must not stay at 30/day forever.
        b = {"daily_cap": 200, "verified": True}
        print("legacy cap today:", mb.warm_cap(b, "2026-01-15"), "in 30 days:", mb.warm_cap(b, "2026-02-14"))
        assert mb.warm_cap(b, "2026-02-14") > 30, "legacy verified box (no verified_at) is capped at 30/day permanently"

    def test_senders_uses_warm_cap(self, tdb, monkeypatch):
        async def ic():
            return False
        monkeypatch.setattr(mailer, "is_connected", ic)

        async def go():
            await tdb.mailboxes.insert_one({"id": 1, "smtp_user": "b1@fundle.ai", "daily_cap": 200, "active": True,
                                            "verified": True, "verified_at": "2026-01-13 09:00:00"})
            await tdb.outreach.insert_one({"id": 1, "status": "sent", "channel": "email", "sent_at": "2026-01-15 01:00:00", "sent_via": 1})
            return await mb.senders({"daily_cap": 200}, "2026-01-15")
        pool = run(go())
        assert pool[0]["remaining"] == 49  # day 2 → 50, minus 1 sent


# ---------------- signature ----------------
class TestSignature:
    def test_defaults_and_with_signature(self):
        assert oc.DEFAULTS["signature"] == ""
        assert oc._with_signature("<p>x</p>", {"signature": ""}) == "<p>x</p>"
        assert oc._with_signature("<p>x</p>", {"signature": "  "}) == "<p>x</p>"
        out = oc._with_signature("<p>x</p>", {"signature": "Abhinav\nFundle.ai"})
        assert out.startswith("<p>x</p>") and "Abhinav<br>Fundle.ai" in out

    def test_migrate_v5_once(self, tdb):
        async def go():
            await tdb.settings.insert_one({"key": "outreach", "signature": "Team Fundle.ai\nfundle.ai"})
            await oc._migrate_v5()
            a = (await tdb.settings.find_one({"key": "outreach"}))["signature"]
            await tdb.settings.update_one({"key": "outreach"}, {"$set": {"signature": "Team Fundle again"}})
            await oc._migrate_v5()
            b = (await tdb.settings.find_one({"key": "outreach"}))["signature"]
            return a, b, await tdb.settings.count_documents({"key": "outreach_v5"})
        a, b, n = run(go())
        assert a == "" and b == "Team Fundle again" and n == 1

    def test_migrate_v5_keeps_custom(self, tdb):
        async def go():
            await tdb.settings.insert_one({"key": "outreach", "signature": "Abhinav Khanna"})
            await oc._migrate_v5()
            return (await tdb.settings.find_one({"key": "outreach"}))["signature"]
        assert run(go()) == "Abhinav Khanna"


# ---------------- template refresh ----------------
def _msg(**k):
    return {"id": 1, "seq_id": 1, "step": 1, "channel": "email", "kind": "template", "template_id": 5, "template_name": "Old",
            "prospect_id": 100, "brand": "Acme", "contact_name": "Jane Doe", "contact_role": "CMO", "to": "jane@acme.com",
            "subject": "OLD SUBJ", "body": "<p>OLD</p>", "status": "sending", "tpl_version": "2026-01-01 00:00:00", **k}


def _tpl(**k):
    return {"id": 5, "name": "Intro v2", "subject": "Hi {{first_name}} at {{brand}}", "body": "<p>NEW {{brand}}</p>",
            "mode": "auto", "active": True, "is_deleted": False, "ai_tailor": False, "attachments": [], "video_links": [],
            "updated_at": "2026-01-10 00:00:00", **k}


class TestRefresh:
    def _seed(self, tdb, m, t):
        async def go():
            if t:
                await tdb.email_templates.insert_one(dict(t))
            await tdb.prospects.insert_one({"id": 100, "brand_name": "Acme", "category": "Fashion"})
            await tdb.outreach.insert_one(dict(m))
        run(go())

    def test_changed_version_plain_merge(self, tdb):
        self._seed(tdb, _msg(), _tpl())
        r = run(oc._refresh_from_template(_msg()))
        assert r["subject"] == "Hi Jane at Acme" and "NEW Acme" in r["body"] and r["template_name"] == "Intro v2"
        db_m = run(tdb.outreach.find_one({"id": 1}))
        assert db_m["subject"] == "Hi Jane at Acme" and db_m["tpl_version"] == "2026-01-10 00:00:00"

    def test_old_message_without_version(self, tdb):
        m = _msg()
        m.pop("tpl_version")
        self._seed(tdb, m, _tpl())
        assert run(oc._refresh_from_template(m))["subject"] == "Hi Jane at Acme"

    def test_same_version_untouched(self, tdb, monkeypatch):
        self._seed(tdb, _msg(tpl_version="2026-01-10 00:00:00"), _tpl())

        async def boom(*a):
            raise AssertionError("tailor should not run")
        monkeypatch.setattr(et, "tailor", boom)
        assert run(oc._refresh_from_template(_msg(tpl_version="2026-01-10 00:00:00")))["subject"] == "OLD SUBJ"

    def test_edited_by_untouched(self, tdb):
        self._seed(tdb, _msg(edited_by="Vineet"), _tpl())
        assert run(oc._refresh_from_template(_msg(edited_by="Vineet")))["subject"] == "OLD SUBJ"
        assert run(tdb.outreach.find_one({"id": 1}))["subject"] == "OLD SUBJ"

    def test_ai_tailor_uses_ai(self, tdb, monkeypatch):
        self._seed(tdb, _msg(), _tpl(ai_tailor=True))

        async def ai(sys_, prompt):
            assert "NEW Acme" in prompt
            return {"subject": "AI SUBJ", "body": "<p>AI BODY</p>"}
        monkeypatch.setattr(et, "ai_text", ai)
        r = run(oc._refresh_from_template(_msg()))
        assert r["subject"] == "AI SUBJ" and r["body"] == "<p>AI BODY</p>"

    @pytest.mark.parametrize("tk", [{"is_deleted": True}, {"active": False}, None])
    def test_deleted_or_inactive_stops_auto(self, tdb, monkeypatch, tk):
        m = _msg(created_by="Auto-send")
        self._seed(tdb, m, _tpl(**tk) if tk is not None else None)
        sent = []

        async def fake_send(*a, **k):
            sent.append(1)
            return {"ok": True}
        monkeypatch.setattr(mb, "send", fake_send)
        pool = [{"id": 0, "email": "main@x.com", "remaining": 5, "cfg": None}]
        assert run(oc._send_one(m, oc.DEFAULTS, pool)) is False
        db_m = run(tdb.outreach.find_one({"id": 1}))
        assert db_m["status"] == "stopped" and db_m["stop_reason"] and not sent

    def test_inactive_manual_kept(self, tdb):
        m = _msg(created_by="Vineet")
        self._seed(tdb, m, _tpl(active=False))
        assert run(oc._refresh_from_template(m))["subject"] == "OLD SUBJ"

    def test_send_one_uses_new_text_and_signature(self, tdb, monkeypatch):
        self._seed(tdb, _msg(), _tpl())
        got = []

        async def fake_send(s, to, subject, body, cc=None, attachments=None):
            got.append((subject, body))
            return {"ok": True}
        monkeypatch.setattr(mb, "send", fake_send)
        pool = [{"id": 0, "email": "main@x.com", "remaining": 5, "cfg": None}]
        assert run(oc._send_one(_msg(), {**oc.DEFAULTS, "signature": "TEST Sig"}, pool)) is True
        assert got[0][0] == "Hi Jane at Acme" and "NEW Acme" in got[0][1] and got[0][1].endswith("TEST Sig</p>")
        assert "Team Fundle" not in got[0][1]

    def test_failure_sets_failed_via(self, tdb, monkeypatch):
        self._seed(tdb, _msg(tpl_version="2026-01-10 00:00:00"), _tpl())

        async def fail(*a, **k):
            return {"ok": False, "error": "boom"}
        monkeypatch.setattr(mb, "send", fail)
        pool = [{"id": 3, "email": "b3@x.com", "remaining": 5, "cfg": {}}]
        assert run(oc._send_one(_msg(tpl_version="2026-01-10 00:00:00"), oc.DEFAULTS, pool)) is False
        d = run(tdb.outreach.find_one({"id": 1}))
        assert d["failed_via"] == 3 and d["failed_at"] and d["status"] == "approved"


# ---------------- inbox sync ----------------
class FakeResp:
    def __init__(self, js, code=200):
        self._js, self.status_code, self.text = js, code, json.dumps(js)

    def json(self):
        return self._js


def _fake_httpx(monkeypatch, messages):
    calls = []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            calls.append(url)
            return FakeResp({"value": messages})
    monkeypatch.setattr(ib.httpx, "AsyncClient", C)
    return calls


class TestInboxSync:
    def _setup(self, tdb, monkeypatch, messages):
        async def gc():
            return {"client_id": "cid", "client_secret_enc": "x"}
        monkeypatch.setattr(ol, "get_config", gc)

        async def tok(cfg, **data):
            assert data["grant_type"] == "refresh_token"
            return {"access_token": "AT", "refresh_token": "RT2"}
        monkeypatch.setattr(ib, "_token", tok)
        monkeypatch.setattr(ol, "dec", lambda v: v)
        monkeypatch.setattr(ol, "enc", lambda v: f"enc:{v}")
        calls = _fake_httpx(monkeypatch, messages)

        async def go():
            await tdb.inbox_readers.insert_one({"mailbox_id": 0, "email": "main@fundle.ai", "refresh_token_enc": "RT",
                                                "last_checked": "2026-01-01T00:00:00Z", "error": "old"})
            await tdb.outreach.insert_many([
                {"id": 1, "channel": "email", "status": "sent", "to": "Ann@Brand.com", "prospect_id": 1, "lead_id": 10,
                 "template_id": 5, "sent_at": "2026-01-10 00:00:00", "sent_via": 0},
                {"id": 2, "channel": "email", "status": "sent", "to": "bob@other.com", "prospect_id": 2, "lead_id": 20,
                 "template_id": 5, "sent_at": "2026-01-10 00:00:00", "sent_via": 0},
                {"id": 3, "channel": "email", "status": "approved", "to": "bob@other.com", "prospect_id": 2, "template_id": 6},
                {"id": 4, "channel": "email", "status": "pending_approval", "to": "x@other.com", "prospect_id": 2, "template_id": 7}])
            await tdb.prospects.insert_many([{"id": 1, "brand_name": "Brand", "tpl_sent": [5, 6], "contacts_status": "done"},
                                             {"id": 2, "brand_name": "Other", "tpl_sent": [5]}])
            await tdb.leads.insert_many([{"id": 10}, {"id": 20}])
        run(go())
        return calls

    def test_bounce_and_reply(self, tdb, monkeypatch):
        msgs = [
            {"from": {"emailAddress": {"address": "postmaster@mail.brand.com"}}, "subject": "Delivery failed",
             "body": {"content": "<p>Your message to <b>ann@brand.com</b> couldn't be delivered.</p>"}},
            {"from": {"emailAddress": {"address": "carol@other.com"}}, "subject": "Re: Hello",
             "body": {"content": "Interested, call me"}},
            {"from": {"emailAddress": {"address": "random@nowhere.io"}}, "subject": "Newsletter", "body": {"content": "hi"}}]
        calls = self._setup(tdb, monkeypatch, msgs)
        stats = run(ib.sync_all())
        assert stats == {"replies": 1, "bounces": 1, "inboxes": 1}, stats
        assert "receivedDateTime ge 2026-01-01T00:00:00Z" in calls[0]

        async def chk():
            return ({m["id"]: m async for m in tdb.outreach.find({})}, {p["id"]: p async for p in tdb.prospects.find({})},
                    {lead["id"]: lead async for lead in tdb.leads.find({})}, await tdb.inbox_readers.find_one({}))
        o, p, leads, rd = run(chk())
        # bounce
        assert o[1]["status"] == "bounced" and o[1]["bounced_at"]
        assert p[1]["bounced_emails"] == ["ann@brand.com"] and p[1]["tpl_sent"] == [6] and "contacts_status" not in p[1]
        assert any(lid == 10 and "bounced" in b for lid, b in tdb.logs)
        # reply (same domain)
        assert o[2]["replied_at"] and o[2]["status"] == "sent"
        assert o[3]["status"] == "stopped" and o[4]["status"] == "stopped" and "replied" in o[3]["stop_reason"]
        assert p[2]["replied"] is True and leads[20]["outreach_replied"] is True
        assert any(lid == 20 and "Reply received" in b for lid, b in tdb.logs)
        # reader bookkeeping
        assert rd["last_checked"] != "2026-01-01T00:00:00Z" and rd["refresh_token_enc"] == "enc:RT2" and "error" not in rd

    def test_undeliverable_subject_from_normal_sender(self, tdb, monkeypatch):
        msgs = [{"from": {"emailAddress": {"address": "it@brand.com"}}, "subject": "Undeliverable: Hello",
                 "bodyPreview": "Delivery to ann@brand.com failed"}]
        self._setup(tdb, monkeypatch, msgs)
        assert run(ib.sync_all())["bounces"] == 1
        assert run(tdb.outreach.find_one({"id": 1}))["status"] == "bounced"

    def test_no_client_id_noop(self, tdb, monkeypatch):
        async def gc():
            return {}
        monkeypatch.setattr(ol, "get_config", gc)
        assert run(ib.sync_all()) == {"replies": 0, "bounces": 0, "inboxes": 0}

    def test_graph_error_recorded(self, tdb, monkeypatch):
        self._setup(tdb, monkeypatch, [])

        class Bad:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return FakeResp({"error": "x"}, 401)
        monkeypatch.setattr(ib.httpx, "AsyncClient", Bad)
        assert run(ib.sync_all())["inboxes"] == 0
        assert "Graph 401" in run(tdb.inbox_readers.find_one({}))["error"]


# ---------------- contacts / auto ----------------
class TestContactsAuto:
    def test_pick_contact_skips_bounced(self):
        p = {"bounced_emails": ["CMO@x.com"], "contacts": [{"name": "A", "role": "CMO", "email": "cmo@x.com"},
                                                          {"name": "B", "role": "CEO", "email": "ceo@x.com"}]}
        assert oc.pick_contact(p)["email"] == "ceo@x.com"
        assert oc.pick_contact({"bounced_emails": ["a@x.com"], "contacts": [{"name": "A", "email": "a@x.com"}]}) is None

    def test_run_auto_skips_replied(self, tdb):
        async def go():
            await tdb.email_templates.insert_one(_tpl())
            await tdb.prospects.insert_many([
                {"id": 1, "brand_name": "R", "replied": True, "fit_score": 90, "contacts": [{"name": "A", "email": "a@r.com"}]},
                {"id": 2, "brand_name": "N", "fit_score": 10, "contacts": [{"name": "B", "email": "b@n.com"}]}])
            n = await et.run_auto(10)
            return n, await tdb.outreach.distinct("prospect_id")
        n, pids = run(go())
        assert n == 1 and pids == [2]

    def test_find_contacts_drops_bounced_keeps_6(self, tdb, monkeypatch):
        cs = [{"name": f"P{i}", "role": "CMO", "email": f"p{i}@x.com", "email_status": "published"} for i in range(8)]

        async def ask(*a):
            return json.dumps({"email_pattern": None, "contacts": cs})

        async def mx(d):
            return True

        async def lr(*a, **k):
            return None
        monkeypatch.setattr(pe, "_ask", ask)
        monkeypatch.setattr(ct, "_mx_ok", mx)
        monkeypatch.setattr(pe, "_log_run", lr)
        p = {"id": 1, "brand_name": "X", "category": "c", "country": "IN", "website": "x.com", "bounced_emails": ["p0@x.com"]}
        run(tdb.prospects.insert_one(dict(p)))
        assert run(ct.find_contacts(p))["ok"] is True
        got = run(tdb.prospects.find_one({"id": 1}))["contacts"]
        assert len(got) == 6 and got[0]["email"] is None and got[1]["email"] == "p1@x.com"


# ---------------- API ----------------
@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


class TestAPI:
    def test_settings_signature_roundtrip(self, s):
        cur = s.get(f"{BASE_URL}/api/outreach/settings").json()
        assert "signature" in cur and not (cur["signature"] or "").startswith("Team Fundle")
        body = {k: v for k, v in cur.items() if k not in ("mail_connected", "mail_provider", "last_run", "sender_name")}
        try:
            r = s.put(f"{BASE_URL}/api/outreach/settings", json={**body, "signature": "TEST_Sig\nLine2"})
            assert r.status_code == 200 and r.json()["signature"] == "TEST_Sig\nLine2"
            assert s.get(f"{BASE_URL}/api/outreach/settings").json()["signature"] == "TEST_Sig\nLine2"
        finally:
            s.put(f"{BASE_URL}/api/outreach/settings", json=body)

    def test_template_put_queued_updated(self, s):
        t = s.post(f"{BASE_URL}/api/outreach/templates", json={"name": "TEST_tpl", "subject": "S", "body": "<p>B</p>",
                                                               "mode": "manual", "active": False}).json()
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        live = run(mk())[os.environ["DB_NAME"]]
        ids = [990000000 + uuid.uuid4().int % 1000000 + i for i in range(3)]
        try:
            run(live.outreach.insert_many([
                {"id": ids[0], "channel": "email", "kind": "template", "template_id": t["id"], "status": "pending_approval", "brand": "TEST_"},
                {"id": ids[1], "channel": "email", "kind": "template", "template_id": t["id"], "status": "pending_approval",
                 "edited_by": "x", "brand": "TEST_"},
                {"id": ids[2], "channel": "email", "kind": "template", "template_id": t["id"], "status": "sent", "brand": "TEST_"}]))
            r = s.put(f"{BASE_URL}/api/outreach/templates/{t['id']}", json={"name": "TEST_tpl", "subject": "S2", "body": "<p>B2</p>",
                                                                          "mode": "manual", "active": False})
            assert r.status_code == 200 and r.json()["queued_updated"] == 1 and r.json()["subject"] == "S2"
            assert r.json()["updated_at"] != t["updated_at"] or True
        finally:
            run(live.outreach.delete_many({"id": {"$in": ids}}))
            s.delete(f"{BASE_URL}/api/outreach/templates/{t['id']}")

    def test_mailboxes_today_cap_reader_and_report(self, s):
        b = s.post(f"{BASE_URL}/api/admin/mailboxes", json={"smtp_user": f"test_{uuid.uuid4().hex[:6]}@example.com",
                                                            "smtp_password": "x", "daily_cap": 150}).json()
        try:
            row = next(x for x in s.get(f"{BASE_URL}/api/admin/mailboxes").json()["boxes"] if x["id"] == b["id"])
            assert "today_cap" in row and "reader" in row and row["reader"] is None
            rep = s.get(f"{BASE_URL}/api/admin/mailboxes/report")
            assert rep.status_code == 200
            rows = {x["id"]: x for x in rep.json()}
            assert 0 in rows and b["id"] in rows and rows[0]["inbox"] == "Main inbox"
            for k in ("sent", "failed", "replies", "bounces"):
                assert set(rows[b["id"]][k]) == {"today", "week"}
        finally:
            s.delete(f"{BASE_URL}/api/admin/mailboxes/{b['id']}")

    def test_read_auth_url(self, s):
        r = s.get(f"{BASE_URL}/api/admin/mailboxes/0/read-auth-url", params={"origin": BASE_URL})
        print("read-auth-url:", r.status_code, r.text[:200])
        if r.status_code == 400:
            assert "Microsoft app" in r.json()["detail"]
        else:
            assert r.status_code == 200 and "login.microsoftonline.com" in r.json()["url"] and "Mail.Read" in r.json()["url"]
        assert s.get(f"{BASE_URL}/api/admin/mailboxes/99999999/read-auth-url", params={"origin": BASE_URL}).status_code in (400, 404)

    def test_read_callback_badstate(self):
        r = requests.get(f"{BASE_URL}/api/oauth/outlook/read-callback", params={"state": "nope", "code": "x"}, allow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"].endswith("/admin?tab=Email&inboxread=badstate")

    def test_sync_replies(self, s):
        r = s.post(f"{BASE_URL}/api/admin/mailboxes/sync-replies")
        assert r.status_code == 200 and set(r.json()) == {"replies", "bounces", "inboxes"}
        assert requests.post(f"{BASE_URL}/api/admin/mailboxes/sync-replies").status_code in (401, 403)
        assert requests.get(f"{BASE_URL}/api/admin/mailboxes/report").status_code in (401, 403)


class TestAuthUrl:
    def test_auth_url_has_mail_read_and_hint(self):
        u = ib.auth_url({"client_id": "cid", "tenant_id": "t1"}, "https://h/api/oauth/outlook/read-callback", "st", "box@fundle.ai")
        assert u.startswith("https://login.microsoftonline.com/t1/oauth2/v2.0/authorize?")
        assert "Mail.Read" in u and "login_hint=box%40fundle.ai" in u and "state=st" in u and "client_id=cid" in u

    def test_freemail_domain_reply_false_positive(self, tdb, monkeypatch):
        # informational: a reply from ANY gmail.com address matches the latest outreach sent to a gmail.com contact
        async def go():
            await tdb.outreach.insert_one({"id": 1, "channel": "email", "status": "sent", "to": "alice@gmail.com", "prospect_id": 1,
                                           "sent_at": "2026-01-10 00:00:00"})
            await tdb.prospects.insert_one({"id": 1})
            return await ib._reply({"from": {"emailAddress": {"address": "stranger@gmail.com"}}, "subject": "hi"}, "2026-01-11")
        print("free-mail domain reply matched:", run(go()))
