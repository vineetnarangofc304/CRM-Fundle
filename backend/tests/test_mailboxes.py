# Multiple outreach inboxes: admin CRUD API (/api/admin/mailboxes), core.mailboxes senders()/pick(),
# outreach _send_one/_run_outreach rotation (sum of caps, sent_via/sent_from, no-room → approved), _migrate_v4,
# outlook_send.send_with_config error dict.
# SAFETY: unit tests use a throw-away DB (<DB_NAME>_t1_<uuid>) dropped at the end; all sending is monkeypatched.
# API tests create TEST mailboxes with fake creds and soft-delete them by id.
import asyncio
import os
import sys
import uuid

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import outreach as oc, mailer, outlook_send as ol, mailboxes as mb, utils as cu, email_templates as et  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


# ---------------- API ----------------
@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


@pytest.fixture(scope="module")
def created(s):
    ids = []
    yield ids
    for i in ids:
        s.delete(f"{BASE_URL}/api/admin/mailboxes/{i}")


def _box(**k):
    return {"smtp_user": f"test_{uuid.uuid4().hex[:6]}@example.com", "smtp_password": "fakepass", "sender_name": "TEST",
            "smtp_host": "smtp.office365.com", "smtp_port": 587, "daily_cap": 200, "active": True, **k}


class TestMailboxAPI:
    def test_list_shape(self, s):
        r = s.get(f"{BASE_URL}/api/admin/mailboxes")
        assert r.status_code == 200
        d = r.json()
        # hourly model (iteration 21): per-inbox hourly_cap, each box has sent_hour/sent_today/hourly_cap
        assert set(d) >= {"primary", "boxes", "hourly_cap"}
        assert isinstance(d["boxes"], list)
        assert isinstance(d["hourly_cap"], int) and 1 <= d["hourly_cap"] <= 100
        for b in d["boxes"]:
            assert b["hourly_cap"] == d["hourly_cap"] and isinstance(b["sent_hour"], int) and isinstance(b["sent_today"], int)
        if d["primary"]:
            assert {"email", "hourly_cap", "sent_hour", "sent_today"} <= set(d["primary"])

    def test_unauth(self):
        assert requests.get(f"{BASE_URL}/api/admin/mailboxes").status_code in (401, 403)

    @pytest.mark.parametrize("bad,code", [({"smtp_user": "notanemail"}, 400), ({"smtp_port": 2525}, 400),
                                          ({"smtp_password": ""}, 400), ({"smtp_password": None}, 400),
                                          ({"daily_cap": 0}, 400), ({"daily_cap": 1001}, 400)])
    def test_validation(self, s, bad, code):
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json=_box(**bad))
        assert r.status_code == code, r.text

    def test_create_dup_edit_test_delete(self, s, created):
        body = _box()
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json=body)
        assert r.status_code == 200, r.text
        b = r.json()
        created.append(b["id"])
        assert b["has_password"] is True and b["verified"] is False and b["daily_cap"] == 200
        assert "smtp_password_enc" not in b and "smtp_password" not in b and "_id" not in b
        # dup (case-insensitive)
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json={**body, "smtp_user": body["smtp_user"].upper()})
        assert r.status_code == 409
        lst = s.get(f"{BASE_URL}/api/admin/mailboxes").json()
        row = next(x for x in lst["boxes"] if x["id"] == b["id"])
        assert row["sent_today"] == 0 and "smtp_password_enc" not in row
        # test send with fake creds → 400, verified False, last_error set
        r = s.post(f"{BASE_URL}/api/admin/mailboxes/{b['id']}/test", json={"to": "test@example.com"}, timeout=60)
        assert r.status_code == 400, r.text
        assert r.json().get("detail")
        row = next(x for x in s.get(f"{BASE_URL}/api/admin/mailboxes").json()["boxes"] if x["id"] == b["id"])
        assert row["verified"] is False and row.get("last_error")
        # edit cap only (no pw)
        r = s.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json={**body, "smtp_password": "", "daily_cap": 150})
        assert r.status_code == 200 and r.json()["daily_cap"] == 150 and r.json()["has_password"] is True
        # 404s
        assert s.put(f"{BASE_URL}/api/admin/mailboxes/99999999", json=body).status_code == 404
        assert s.post(f"{BASE_URL}/api/admin/mailboxes/99999999/test", json={"to": "a@b.co"}).status_code == 404
        # delete → gone from list, second delete 404
        assert s.delete(f"{BASE_URL}/api/admin/mailboxes/{b['id']}").status_code == 200
        assert all(x["id"] != b["id"] for x in s.get(f"{BASE_URL}/api/admin/mailboxes").json()["boxes"])
        assert s.delete(f"{BASE_URL}/api/admin/mailboxes/{b['id']}").status_code == 404
        # re-adding the same email after soft delete is allowed
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json=body)
        assert r.status_code == 200
        created.append(r.json()["id"])

    def test_edit_duplicate_email(self, s, created):
        a = s.post(f"{BASE_URL}/api/admin/mailboxes", json=_box()).json(); created.append(a["id"])
        b = s.post(f"{BASE_URL}/api/admin/mailboxes", json=_box()).json(); created.append(b["id"])
        r = s.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json=_box(smtp_user=a["smtp_user"], smtp_password=""))
        print("PUT to another box's email:", r.status_code)
        assert r.status_code == 409, "PUT allows renaming to an email already used by another inbox"


# ---------------- unit (isolated DB) ----------------
@pytest.fixture
def tdb(monkeypatch):
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])
    cli = run(mk())
    name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
    d = cli[name]
    for mod in (oc, mb, cu, et):
        monkeypatch.setattr(mod, "db", d)
    yield d
    run(cli.drop_database(name))


def _conn(monkeypatch, on=True):
    async def ic():
        return on

    async def gc():
        return {"email": "main@fundle.ai", "method": "graph"}
    monkeypatch.setattr(mailer, "is_connected", ic)
    monkeypatch.setattr(ol, "get_config", gc)


async def _mk_box(d, bid, cap=200, active=True, verified=True, deleted=False, verified_at="2025-01-01 00:00:00"):
    # verified_at well in the past → warm-up finished, full daily_cap applies
    await d.mailboxes.insert_one({"id": bid, "smtp_user": f"box{bid}@fundle.ai", "smtp_password_enc": "x", "daily_cap": cap,
                                  "active": active, "verified": verified, "is_deleted": deleted, "smtp_host": "h", "smtp_port": 587,
                                  **({"verified_at": verified_at} if verified_at else {})})


DAY = "2026-01-15"


def _ago(minutes):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


class TestSendersPick:
    def test_senders(self, tdb, monkeypatch):
        # hourly model: remaining = hourly_cap - sent by that inbox in the last rolling 60 min; daily_cap ignored
        _conn(monkeypatch, True)
        recent, old = _ago(10), _ago(61)

        async def go():
            await _mk_box(tdb, 1, cap=5)  # tiny daily_cap must NOT reduce remaining any more
            await _mk_box(tdb, 2, verified=False)
            await _mk_box(tdb, 3, active=False)
            await _mk_box(tdb, 4, deleted=True)
            await tdb.outreach.insert_many([
                {"id": 1, "status": "sent", "channel": "email", "sent_at": recent},  # legacy, no sent_via -> primary
                {"id": 2, "status": "sent", "channel": "email", "sent_at": recent, "sent_via": 0},
                {"id": 3, "status": "bounced", "channel": "email", "sent_at": recent, "sent_via": 1},
                {"id": 4, "status": "sent", "channel": "email", "sent_at": old, "sent_via": 1},  # >60 min ago
                {"id": 5, "status": "approved", "channel": "email", "sent_at": recent, "sent_via": 1}])
            return await mb.senders({"hourly_cap": 25, "daily_cap": 1}, DAY)
        pool = run(go())
        by = {x["id"]: x for x in pool}
        assert set(by) == {0, 1}
        assert by[0]["remaining"] == 23 and by[0]["cfg"] is None and by[0]["email"] == "main@fundle.ai"
        assert by[1]["remaining"] == 24 and by[1]["cfg"]["method"] == "smtp" and by[1]["cfg"]["from_email"] == "box1@fundle.ai"

    def test_senders_zero_sent_and_full_inbox_only(self, tdb, monkeypatch):
        _conn(monkeypatch, True)

        async def go():
            await _mk_box(tdb, 1)
            await _mk_box(tdb, 2, verified_at=_ago(30))  # brand new (warm-up day 0) -> no longer limited to 30/day
            fresh = await mb.senders({"hourly_cap": 25}, DAY)
            await tdb.outreach.insert_many([{"id": i, "status": "sent", "channel": "email", "sent_at": _ago(i % 59 + 1),
                                             "sent_via": 1} for i in range(1, 26)])
            await tdb.outreach.insert_many([{"id": 100 + i, "status": "sent", "channel": "email", "sent_at": _ago(70 + i),
                                             "sent_via": 2} for i in range(200)])  # 200 earlier today -> no daily limit
            return fresh, await mb.senders({"hourly_cap": 25}, DAY), await mb.senders({}, DAY)
        fresh, after, dflt = run(go())
        assert {x["id"]: x["remaining"] for x in fresh} == {0: 25, 1: 25, 2: 25}
        assert {x["id"]: x["remaining"] for x in after} == {0: 25, 1: 0, 2: 25}
        assert {x["id"]: x["remaining"] for x in dflt} == {0: 25, 1: 0, 2: 25}  # default hourly_cap 25

    def test_senders_primary_disconnected(self, tdb, monkeypatch):
        _conn(monkeypatch, False)

        async def go():
            await _mk_box(tdb, 1)
            return await mb.senders({"daily_cap": 200}, DAY)
        assert [x["id"] for x in run(go())] == [1]

    def test_pick_sticky_and_most_room(self, tdb):
        async def go():
            pool = [{"id": 0, "remaining": 10}, {"id": 1, "remaining": 50}, {"id": 2, "remaining": 0}]
            await tdb.prospects.insert_many([{"id": 1}, {"id": 2, "sender_mailbox": 0}, {"id": 3, "sender_mailbox": 2},
                                             {"id": 4, "sender_mailbox": 9}])
            await tdb.mailboxes.insert_many([{"id": 1, "is_deleted": False}, {"id": 2, "is_deleted": False}])
            r = {}
            r["new"] = (await mb.pick(1, pool))["id"]
            r["new_saved"] = (await tdb.prospects.find_one({"id": 1}))["sender_mailbox"]
            r["sticky"] = (await mb.pick(2, pool))["id"]
            r["full_sticky"] = (await mb.pick(3, pool))["id"]
            r["full_sticky_saved"] = (await tdb.prospects.find_one({"id": 3}))["sender_mailbox"]
            r["gone_sticky"] = (await mb.pick(4, pool))["id"]
            r["gone_saved"] = (await tdb.prospects.find_one({"id": 4}))["sender_mailbox"]
            r["force_full_sticky"] = (await mb.pick(3, pool, force=True))["id"]
            r["none"] = await mb.pick(1, [{"id": 0, "remaining": 0}])
            r["force_none"] = (await mb.pick(1, [{"id": 0, "remaining": 0}], force=True))["id"]
            r["noprospect"] = (await mb.pick(None, pool))["id"]
            return r
        r = run(go())
        assert r == {"new": 1, "new_saved": 1, "sticky": 0, "full_sticky": 1, "full_sticky_saved": 2, "gone_sticky": 1,
                     "gone_saved": 1, "force_full_sticky": 2, "none": None, "force_none": 0, "noprospect": 1}


class TestRotation:
    def _setup(self, tdb, monkeypatch, n_msgs, fail_ids=()):
        _conn(monkeypatch, True)
        sent = []

        async def fake_send(s, to, subject, body, cc=None, attachments=None):
            if s["id"] in fail_ids:
                return {"ok": False, "error": "boom"}
            sent.append((s["id"], to))
            return {"ok": True}
        monkeypatch.setattr(mb, "send", fake_send)

        async def no_auto(n):
            return 0
        monkeypatch.setattr(et, "run_auto", no_auto)
        from core import inbox_sync, linkedin  # never touch the live DB / Microsoft from unit runs

        async def noop(*a, **k):
            return {}
        monkeypatch.setattr(inbox_sync, "sync_all", noop)
        monkeypatch.setattr(linkedin, "build_queue", noop)

        async def go():
            await _mk_box(tdb, 1, cap=3)
            await _mk_box(tdb, 2, cap=2)
            docs = [{"id": i, "seq_id": 1000 + i, "prospect_id": 100 + i, "lead_id": 500 + i, "channel": "email", "step": 1,
                     "status": "approved", "to": f"p{i}@example.com", "contact_name": "TEST", "subject": "S", "body": "<p>b</p>",
                     "scheduled_at": "2026-01-01 00:00:00", "kind": "template", "template_id": 0} for i in range(1, n_msgs + 1)]
            await tdb.outreach.insert_many(docs)
            await tdb.prospects.insert_many([{"id": 100 + i} for i in range(1, n_msgs + 1)])
        run(go())
        return sent

    def test_per_run_cap_is_ceil_hourly_over_4(self, tdb, monkeypatch):
        # hourly_cap 25 -> each 15-min run sends at most ceil(25/4)=7 per inbox; daily caps (3/2 on boxes) ignored
        sent = self._setup(tdb, monkeypatch, 30)
        st = {**oc.DEFAULTS, "hourly_cap": 25, "daily_cap": 4, "auto_send": False, "ai_sequence": False}
        stats = run(oc._run_outreach(st, {"drafted": 0, "sent": 0, "failed": 0}, "test"))
        assert stats["sent"] == 21, stats
        from collections import Counter
        c = Counter(i for i, _ in sent)
        assert c == {0: 7, 1: 7, 2: 7}

        async def chk():
            sentdocs = await tdb.outreach.find({"status": "sent"}).to_list(100)
            left = await tdb.outreach.count_documents({"status": "approved"})
            msg = await tdb.messages.find_one({"body": {"$regex": "from box1@fundle.ai"}})
            return sentdocs, left, msg
        sentdocs, left, msg = run(chk())
        assert left == 9
        assert all("sent_via" in d and d["sent_from"] for d in sentdocs)
        assert {d["sent_from"] for d in sentdocs} == {"main@fundle.ai", "box1@fundle.ai", "box2@fundle.ai"}
        assert msg is not None
        p = run(tdb.prospects.find_one({"id": sentdocs[0]["prospect_id"]}))
        assert p["sender_mailbox"] == sentdocs[0]["sent_via"]
        # 2nd run in the same hour: each inbox has 18 left this hour, per-run 7 -> the 9 remaining all go
        stats2 = run(oc._run_outreach(st, {"drafted": 0, "sent": 0, "failed": 0}, "test"))
        assert stats2["sent"] == 9, stats2

    def test_run_respects_hour_remaining(self, tdb, monkeypatch):
        # inbox 1 already sent 22 this hour -> only 3 more; inbox 2 at 25 -> 0; primary 7
        sent = self._setup(tdb, monkeypatch, 30)

        async def pre():
            await tdb.outreach.insert_many([{"id": 1000 + i, "status": "sent", "channel": "email", "sent_at": _ago(5),
                                             "sent_via": 1} for i in range(22)]
                                           + [{"id": 2000 + i, "status": "sent", "channel": "email", "sent_at": _ago(5),
                                               "sent_via": 2} for i in range(25)])
        run(pre())
        st = {**oc.DEFAULTS, "hourly_cap": 25, "auto_send": False, "ai_sequence": False}
        stats = run(oc._run_outreach(st, {"drafted": 0, "sent": 0, "failed": 0}, "test"))
        from collections import Counter
        assert Counter(i for i, _ in sent) == {0: 7, 1: 3}, stats
        assert stats["sent"] == 10

    def test_per_run_small_cap(self, tdb, monkeypatch):
        sent = self._setup(tdb, monkeypatch, 30)
        st = {**oc.DEFAULTS, "hourly_cap": 5, "auto_send": False, "ai_sequence": False}  # ceil(5/4)=2
        stats = run(oc._run_outreach(st, {"drafted": 0, "sent": 0, "failed": 0}, "test"))
        from collections import Counter
        assert Counter(i for i, _ in sent) == {0: 2, 1: 2, 2: 2} and stats["sent"] == 6

    def test_no_room_goes_back_to_approved(self, tdb, monkeypatch):
        self._setup(tdb, monkeypatch, 1)

        async def go():
            m = await tdb.outreach.find_one({"id": 1}, {"_id": 0})
            await tdb.outreach.update_one({"id": 1}, {"$set": {"status": "sending"}})
            ok = await oc._send_one(m, oc.DEFAULTS, pool=[{"id": 0, "email": "x", "remaining": 0, "cfg": None}])
            return ok, await tdb.outreach.find_one({"id": 1})
        ok, d = run(go())
        assert ok is False and d["status"] == "approved" and not d.get("send_attempts")

    def test_force_sends_when_full(self, tdb, monkeypatch):
        sent = self._setup(tdb, monkeypatch, 1)

        async def go():
            m = await tdb.outreach.find_one({"id": 1}, {"_id": 0})
            return await oc._send_one(m, oc.DEFAULTS, pool=[{"id": 0, "email": "x", "remaining": 0, "cfg": None}], force=True)
        assert run(go()) is True and sent == [(0, "p1@example.com")]

    def test_failing_inbox_behaviour(self, tdb, monkeypatch):
        # Observational: one broken extra inbox — how many messages get burned in a single run?
        self._setup(tdb, monkeypatch, 6, fail_ids=(1,))
        st = {**oc.DEFAULTS, "daily_cap": 1, "auto_send": False, "ai_sequence": False}
        stats = run(oc._run_outreach(st, {"drafted": 0, "sent": 0, "failed": 0}, "test"))
        print("stats with failing box1:", stats)
        assert stats["sent"] >= 1


class TestMigrationAndSendWithConfig:
    def test_migrate_v4_once(self, tdb, monkeypatch):
        async def go():
            await tdb.settings.insert_one({"key": "outreach", "daily_cap": 100})
            await oc._migrate_v4()
            a = (await tdb.settings.find_one({"key": "outreach"}))["daily_cap"]
            await tdb.settings.update_one({"key": "outreach"}, {"$set": {"daily_cap": 50}})
            await oc._migrate_v4()
            b = (await tdb.settings.find_one({"key": "outreach"}))["daily_cap"]
            return a, b, await tdb.settings.count_documents({"key": "outreach_v4"})
        assert run(go()) == (200, 50, 1)

    def test_send_with_config_returns_error(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("535 auth failed")
        monkeypatch.setattr(ol, "_smtp_send", boom)
        r = run(ol.send_with_config({"smtp_user": "a@b.co"}, "x@y.co", "s", "b"))
        assert r["ok"] is False and "rejected" in r["error"]

    def test_send_email_smtp_and_graph(self, monkeypatch):
        calls = []

        async def smtp(cfg, *a):
            calls.append("smtp"); return {"ok": True}

        async def graph(cfg, *a):
            calls.append("graph"); raise RuntimeError("graph down")
        monkeypatch.setattr(ol, "_smtp_send", smtp)
        monkeypatch.setattr(ol, "_graph_send", graph)

        async def cfg_smtp():
            return {"method": "smtp", "smtp_user": "a@b.co"}

        async def cfg_graph():
            return {"method": "graph"}
        monkeypatch.setattr(ol, "get_config", cfg_smtp)
        assert run(ol.send_email("x@y.co", "s", "b"))["ok"] is True
        monkeypatch.setattr(ol, "get_config", cfg_graph)
        r = run(ol.send_email("x@y.co", "s", "b"))
        assert r == {"ok": False, "error": "graph down"} and calls == ["smtp", "graph"]
