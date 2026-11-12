# Night-only prospecting window (night_seconds_left, run_worker gating, watchdog spawn rules, overview night_window)
# + Outreach auto-send (v2 migration, auto_approve, min_score 0 candidates, CC plumbing in mailer/outlook/gmail,
# daily cap, PUT /outreach/settings cc validation).
# SAFETY: DB-mutating unit tests run against a throw-away database (<DB_NAME>_t1_<uuid>) dropped at the end;
# mail providers are monkeypatched — no real email, no LLM calls, no real prospecting runs.
import asyncio
import base64
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

from core import prospector as pe, outreach as oc, mailer, outlook_send, gmail_send, email_templates as et  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    asyncio.set_event_loop(LOOP)  # other suites (asyncio.run) may clear the current loop
    return LOOP.run_until_complete(coro)
IST = timedelta(hours=5, minutes=30)


def _fake_dt(ist_h, ist_m=0):
    utc = datetime(2026, 1, 15, ist_h, ist_m, tzinfo=timezone.utc) - IST

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc
    return FakeDT


# ---------------- night window ----------------
class TestNightWindow:
    @pytest.mark.parametrize("h,m,expected", [
        (9, 0, 0), (12, 0, 0), (21, 59, 0), (8, 59, 60), (22, 0, 11 * 3600), (23, 30, 9.5 * 3600),
        (0, 0, 9 * 3600), (1, 0, 8 * 3600), (5, 15, 3.75 * 3600)])
    def test_night_seconds_left(self, monkeypatch, h, m, expected):
        monkeypatch.setattr(pe, "datetime", _fake_dt(h, m))
        assert pe.night_seconds_left() == int(expected)

    # 24h engine: daytime cron/watchdog runs no longer return skipped (see test_prospecting_24h_tailor.py)

    def _fake_db(self, captured):
        class Col:
            async def find_one_and_update(self, f, u, **k):
                captured["until"] = u["$set"]["until"]
                return {"key": "prospect_lock"}

            async def update_one(self, *a, **k):
                return None

            async def find_one(self, *a, **k):
                return None

        class DB:
            settings = Col()
        return DB()

    def test_run_worker_caps_and_manual_unrestricted(self, monkeypatch):
        cap = {}
        monkeypatch.setattr(pe, "db", self._fake_db(cap))

        async def disabled():
            return {"enabled": False, "countries": ["India"], "phase": "auto", "concurrency": 1,
                    "create_leads": False, "daily_cap": 0}
        monkeypatch.setattr(pe, "get_settings", disabled)
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 300)
        t0 = datetime.now(timezone.utc)
        assert run(pe.run_worker(840, "cron")) == {"skipped": "disabled"}
        secs = (datetime.fromisoformat(cap["until"]) - t0).total_seconds()
        assert 415 <= secs <= 425, f"max_seconds not capped to time left: until-now={secs}"
        # manual trigger in daytime is not window-restricted
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 0)
        cap.clear()
        assert run(pe.run_worker(840, "manual:Tester")) == {"skipped": "disabled"}
        secs = (datetime.fromisoformat(cap["until"]) - t0).total_seconds()
        assert 955 <= secs <= 965

    @pytest.mark.parametrize("enabled,left,spawn", [(True, 3600, True), (True, 0, True), (False, 3600, False)])
    def test_watchdog_spawn_rules(self, monkeypatch, enabled, left, spawn):
        from routes import prospecting as rp
        spawned = []

        async def gs():
            return {"enabled": enabled}

        async def wd(en=True):
            return {"stuck": False, "restart": True}
        monkeypatch.setattr(rp.pe, "get_settings", gs)
        monkeypatch.setattr(rp.pe, "watchdog", wd)
        monkeypatch.setattr(rp.pe, "night_seconds_left", lambda: left)
        monkeypatch.setattr(rp, "_spawn", lambda c: (spawned.append(c), c.close()))
        monkeypatch.setattr(rp, "_cron_auth", lambda r: None)
        res = run(rp.cron_watchdog(None))
        assert res["ok"] is True
        assert bool(spawned) == spawn


# ---------------- outreach auto-send (isolated DB) ----------------
class TestOutreachAutoSend:
    @pytest.fixture(autouse=True)
    def tdb(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        monkeypatch.setattr(oc, "db", d)
        monkeypatch.setattr(et, "db", d)
        self.db = d
        yield d
        run(cli.drop_database(name))

    def _seq(self, seq, pid):
        base = {"seq_id": seq, "prospect_id": pid, "brand": "TEST_Brand", "category": "x", "contact_name": "T",
                "to": "t1@example.com", "status": "pending_approval", "created_at": "2026-01-01 00:00:00"}
        docs = [{**base, "id": seq * 10 + i, "channel": "email", "step": i, "subject": f"S{i}", "body": "<p>b</p>",
                 "scheduled_at": None} for i in (1, 2, 3)]
        docs += [{**base, "id": seq * 10 + 5, "channel": "linkedin", "step": 1, "subject": "Connection note", "body": "x"}]
        return docs

    def test_migration_v2_one_time(self):
        run(self.db.settings.insert_one({"key": "outreach", "min_score": 60, "daily_cap": 40, "drafts_per_run": 10}))
        run(oc._migrate_v2())
        st = run(oc.get_settings())
        assert (st["auto_send"], st["min_score"], st["daily_cap"], st["drafts_per_run"], st["cc"]) == \
            (True, 0, 100, 30, "vineet.narang@fundle.ai")
        assert run(self.db.settings.find_one({"key": "outreach_v2"}))
        run(self.db.settings.update_one({"key": "outreach"}, {"$set": {"daily_cap": 55, "cc": ""}}))
        run(oc._migrate_v2())
        st = run(oc.get_settings())
        assert st["daily_cap"] == 55 and st["cc"] == "", "migration must only run once"

    def test_auto_approve(self):
        run(self.db.outreach.insert_many(self._seq(1, 1)))
        run(self.db.prospects.insert_one({"id": 1, "brand_name": "TEST_Brand", "outreach_seq": 1,
                                          "outreach_status": "pending_approval"}))
        assert run(oc.auto_approve()) == 1
        m = {d["id"]: d for d in run(self.db.outreach.find({}, {"_id": 0}).to_list(10))}
        assert m[11]["status"] == "approved" and m[11]["scheduled_at"]
        assert m[12]["status"] == "approved" and m[12]["scheduled_at"] is None
        assert m[13]["status"] == "approved" and m[13]["scheduled_at"] is None
        assert m[15]["status"] == "pending_approval"
        assert run(self.db.prospects.find_one({"id": 1}))["outreach_status"] == "approved"

    def _run(self, monkeypatch, connected=False, sends=None):
        drafted = []

        async def fake_draft(p, c, user=None):
            drafted.append(p["id"])
            await self.db.prospects.update_one({"id": p["id"]}, {"$set": {"outreach_seq": p["id"]}})
            return p["id"]

        async def is_conn():
            return connected

        async def send(to, subject, body, html=False, cc=None, attachments=None):
            sends.append({"to": to, "cc": cc})
            return {"ok": True}
        # v3 (template library) migration already done; keep the legacy AI sequence on for these legacy tests
        run(self.db.settings.update_one({"key": "outreach_v3"}, {"$set": {"done": True}}, upsert=True))
        run(self.db.settings.update_one({"key": "outreach"}, {"$set": {"ai_sequence": True}}, upsert=True))
        monkeypatch.setattr(oc, "draft_sequence", fake_draft)
        monkeypatch.setattr(mailer, "is_connected", is_conn)
        monkeypatch.setattr(mailer, "send_email", send)
        monkeypatch.setattr(oc, "_stop_engaged", lambda: asyncio.sleep(0))
        monkeypatch.setattr(oc, "log_message", lambda *a, **k: asyncio.sleep(0))
        return run(oc.run_outreach("test")), drafted

    def test_candidates_min_score_zero(self, monkeypatch):
        run(self.db.prospects.insert_many([
            {"id": 1, "brand_name": "TEST_noscore", "category": "x", "country": "India",
             "contacts": [{"name": "A", "email": "a@example.com"}]},
            {"id": 2, "brand_name": "TEST_noemail", "category": "x", "country": "India", "fit_score": 90,
             "contacts": [{"name": "B", "email": None}]},
            {"id": 3, "brand_name": "TEST_scored", "category": "x", "country": "India", "fit_score": 10,
             "contacts": [{"name": "C", "email": "c@example.com"}]}]))
        stats, drafted = self._run(monkeypatch)
        assert sorted(drafted) == [1, 3]

    def test_candidates_mixed_contacts(self, monkeypatch):
        # A prospect whose first contact has no email but second does should still be a candidate
        run(self.db.prospects.insert_one({"id": 4, "brand_name": "TEST_mixed", "category": "x", "country": "India",
                                          "contacts": [{"name": "N", "email": None}, {"name": "Y", "email": "y@example.com"}]}))
        stats, drafted = self._run(monkeypatch)
        assert drafted == [4], "prospect with a mix of email/no-email contacts excluded by contacts.email $nin filter"

    def test_send_with_cc(self, monkeypatch):
        run(self.db.outreach.insert_many(self._seq(2, 2)))
        sends = []
        stats, _ = self._run(monkeypatch, connected=True, sends=sends)
        assert stats["auto_approved"] == 1 and stats["sent"] == 1
        assert sends == [{"to": "t1@example.com", "cc": "vineet.narang@fundle.ai"}]
        s2 = run(self.db.outreach.find_one({"id": 22}))
        assert s2["scheduled_at"] and s2["status"] == "approved"

    def test_daily_cap_reached_sends_nothing(self, monkeypatch):
        run(oc._migrate_v2())
        run(self.db.settings.update_one({"key": "outreach"}, {"$set": {"daily_cap": 2}}))
        now = oc._now()
        run(self.db.outreach.insert_many([{"id": 900 + i, "seq_id": 900 + i, "status": "sent", "channel": "email",
                                           "step": 1, "sent_at": now} for i in range(2)]))
        for s in (3, 4, 5):
            run(self.db.outreach.insert_many(self._seq(s, s)))
        sends = []
        stats, _ = self._run(monkeypatch, connected=True, sends=sends)
        assert len(sends) == 0, f"daily cap already reached but {len(sends)} emails sent (limit(0) = no limit)"

    # --- iteration_12 retest: partial cap, lock, atomic claim, stale 'sending', failed-send retry ---
    def _due(self, n, start=100):
        now = oc._now()
        docs = []
        for i in range(n):
            docs += [{"id": start + i, "seq_id": start + i, "prospect_id": start + i, "brand": "TEST_B", "contact_name": "T",
                      "to": f"t{i}@example.com", "status": "approved", "channel": "email", "step": 3,
                      "subject": "S", "body": "<p>b</p>", "scheduled_at": now}]
        run(self.db.outreach.insert_many(docs))

    def test_daily_cap_partial_sends_exactly_remaining(self, monkeypatch):
        run(oc._migrate_v2())
        run(self.db.settings.update_one({"key": "outreach"}, {"$set": {"daily_cap": 3}}))
        run(self.db.outreach.insert_one({"id": 999, "seq_id": 999, "status": "sent", "channel": "email", "step": 1,
                                         "sent_at": oc._now()}))
        self._due(5)
        sends = []
        stats, _ = self._run(monkeypatch, connected=True, sends=sends)
        assert len(sends) == 2 and stats["sent"] == 2
        assert run(self.db.outreach.count_documents({"status": "approved", "channel": "email"})) == 3
        # cap now fully reached -> another run sends nothing
        sends2 = []
        self._run(monkeypatch, connected=True, sends=sends2)
        assert sends2 == []

    def _slow_patch(self, monkeypatch, sends, delay=0.2, ok=True):
        async def fake_draft(p, c, user=None):
            return p["id"]

        async def is_conn():
            return True

        async def send(to, subject, body, html=False, cc=None, attachments=None):
            sends.append(to)
            await asyncio.sleep(delay)
            return {"ok": ok, **({} if ok else {"error": "TEST_boom"})}
        # v3 (template library) migration already done; keep the legacy AI sequence on for these legacy tests
        run(self.db.settings.update_one({"key": "outreach_v3"}, {"$set": {"done": True}}, upsert=True))
        run(self.db.settings.update_one({"key": "outreach"}, {"$set": {"ai_sequence": True}}, upsert=True))
        monkeypatch.setattr(oc, "draft_sequence", fake_draft)
        monkeypatch.setattr(mailer, "is_connected", is_conn)
        monkeypatch.setattr(mailer, "send_email", send)
        monkeypatch.setattr(oc, "_stop_engaged", lambda: asyncio.sleep(0))
        monkeypatch.setattr(oc, "log_message", lambda *a, **k: asyncio.sleep(0))

    def test_concurrent_runs_lock_no_double_send(self, monkeypatch):
        run(oc._migrate_v2())
        self._due(4)
        sends = []
        self._slow_patch(monkeypatch, sends)

        async def both():
            return await asyncio.gather(oc.run_outreach("a"), oc.run_outreach("b"))
        r = run(both())
        assert sum(1 for x in r if x == {"skipped": "already running"}) == 1, r
        assert sorted(sends) == sorted(set(sends)) and len(sends) == 4
        assert run(self.db.outreach.count_documents({"status": "sent"})) == 4
        locks = run(self.db.settings.find({"key": "outreach_lock"}, {"_id": 0}).to_list(5))
        assert locks == [{"key": "outreach_lock", "until": ""}], locks

    def test_concurrent_runs_fresh_db_lock_doc_race(self, monkeypatch):
        # no pre-existing lock doc and no v2 marker: both runs race the $setOnInsert upsert
        self._due(3)
        sends = []
        self._slow_patch(monkeypatch, sends)

        async def both():
            return await asyncio.gather(oc.run_outreach("a"), oc.run_outreach("b"))
        r = run(both())
        n_locks = run(self.db.settings.count_documents({"key": "outreach_lock"}))
        print("results", r, "lock docs", n_locks, "sends", sends)
        assert len(sends) == len(set(sends)) == 3
        assert n_locks == 1, f"{n_locks} outreach_lock docs created (no unique index on settings.key)"

    def test_lock_held_skips_and_expired_lock_taken(self, monkeypatch):
        run(oc._migrate_v2())
        fut = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        run(self.db.settings.insert_one({"key": "outreach_lock", "until": fut}))
        self._due(1)
        sends = []
        self._slow_patch(monkeypatch, sends, delay=0)
        assert run(oc.run_outreach("t")) == {"skipped": "already running"} and sends == []
        assert run(self.db.settings.find_one({"key": "outreach_lock"}))["until"] == fut
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        run(self.db.settings.update_one({"key": "outreach_lock"}, {"$set": {"until": past}}))
        res = run(oc.run_outreach("t"))
        assert res["sent"] == 1 and len(sends) == 1
        assert run(self.db.settings.find_one({"key": "outreach_lock"}))["until"] == ""

    def test_stale_sending_reset_and_sent(self, monkeypatch):
        run(oc._migrate_v2())
        self._due(2)
        run(self.db.outreach.update_one({"id": 100}, {"$set": {"status": "sending"}}))
        sends = []
        self._slow_patch(monkeypatch, sends, delay=0)
        res = run(oc.run_outreach("t"))
        assert res["sent"] == 2 and run(self.db.outreach.count_documents({"status": "sending"})) == 0

    def test_failed_send_retry_then_failed(self, monkeypatch):
        run(oc._migrate_v2())
        self._due(1)
        sends = []
        self._slow_patch(monkeypatch, sends, delay=0, ok=False)
        t0 = datetime.now(timezone.utc)
        res = run(oc.run_outreach("t"))
        assert res["failed"] == 1
        m = run(self.db.outreach.find_one({"id": 100}))
        assert m["status"] == "approved" and m["send_attempts"] == 1 and m["error"] == "TEST_boom"
        sched = datetime.strptime(m["scheduled_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        assert 3500 <= (sched - t0).total_seconds() <= 3700
        # not due again within the hour
        run(oc.run_outreach("t"))
        assert len(sends) == 1
        for expected_attempts, expected_status in ((2, "approved"), (3, "failed")):
            run(self.db.outreach.update_one({"id": 100}, {"$set": {"scheduled_at": oc._now()}}))
            run(oc.run_outreach("t"))
            m = run(self.db.outreach.find_one({"id": 100}))
            assert (m["send_attempts"], m["status"]) == (expected_attempts, expected_status)
        run(oc.run_outreach("t"))
        assert len(sends) == 3, "failed message must not be retried after 3 attempts"

# ---------------- CC plumbing in providers ----------------
class TestMailerCC:
    def test_mailer_passes_cc(self, monkeypatch):
        got = {}

        async def ap():
            return "outlook"

        async def se(to, subject, body, html=False, cc=None, attachments=None):
            got.update(to=to, cc=cc)
            return {"ok": True}
        monkeypatch.setattr(mailer, "active_provider", ap)
        monkeypatch.setattr(outlook_send, "send_email", se)
        run(mailer.send_email("a@example.com", "s", "b", html=True, cc="c@example.com"))
        assert got == {"to": "a@example.com", "cc": "c@example.com"}

    def test_graph_payload_cc(self, monkeypatch):
        sent = {}

        async def tok(cfg, **d):
            return {"access_token": "x"}

        class Resp:
            status_code = 202

        class Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                sent.update(json)
                return Resp()
        monkeypatch.setattr(outlook_send, "_token", tok)
        monkeypatch.setattr(outlook_send.httpx, "AsyncClient", Client)
        assert run(outlook_send._graph_send({}, "a@example.com", "s", "b", True, "c@example.com")) == {"ok": True}
        assert sent["message"]["ccRecipients"] == [{"emailAddress": {"address": "c@example.com"}}]
        sent.clear()
        run(outlook_send._graph_send({}, "a@example.com", "s", "b", True))
        assert "ccRecipients" not in sent["message"]

    def test_smtp_cc_header(self, monkeypatch):
        msgs = []

        async def fake_send(msg, **k):
            msgs.append(msg)
        monkeypatch.setattr(outlook_send.aiosmtplib, "send", fake_send)
        run(outlook_send._smtp_send({"smtp_user": "u@example.com"}, "a@example.com", "s", "b", True, "c@example.com"))
        assert msgs[0]["Cc"] == "c@example.com" and msgs[0]["To"] == "a@example.com"

    def test_gmail_cc_header(self, monkeypatch):
        raw = {}

        async def creds():
            return None

        async def cfg():
            return {"email": "me@example.com"}

        class Svc:
            def users(self): return self
            def messages(self): return self
            def send(self, userId, body):
                raw.update(body)
                return self
            def execute(self): return {"id": "1"}
        monkeypatch.setattr(gmail_send, "_creds", creds)
        monkeypatch.setattr(gmail_send, "get_config", cfg)
        monkeypatch.setattr(gmail_send, "build", lambda *a, **k: Svc())
        assert run(gmail_send.send_email("a@example.com", "s", "b", html=True, cc="c@example.com"))["ok"]
        assert "cc: c@example.com" in base64.urlsafe_b64decode(raw["raw"]).decode().lower()


# ---------------- API ----------------
FINAL = {"enabled": True, "min_score": 0, "daily_cap": 100, "drafts_per_run": 30, "auto_send": True,
         "cc": "vineet.narang@fundle.ai", "gaps": [3, 7]}


class TestApi:
    @pytest.fixture(scope="class")
    def admin(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
        if r.status_code != 200:
            pytest.skip("login failed")
        s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        return s

    def test_overview_night_window(self, admin):
        d = admin.get(f"{BASE_URL}/api/prospecting/overview").json()
        assert isinstance(d["night_window"], bool)
        assert d["night_window"] == (pe.night_seconds_left() > 0)

    def test_settings_auto_send_cc(self, admin):
        cur = admin.get(f"{BASE_URL}/api/outreach/settings").json()
        assert "auto_send" in cur and "cc" in cur and cur["mail_connected"] is False
        body = {**FINAL, "pitch": cur["pitch"], "signature": cur["signature"]}
        r = admin.put(f"{BASE_URL}/api/outreach/settings", json={**body, "cc": "  TEST_cc@example.com ", "auto_send": False})
        assert r.status_code == 200 and r.json()["cc"] == "TEST_cc@example.com" and r.json()["auto_send"] is False
        got = admin.get(f"{BASE_URL}/api/outreach/settings").json()
        assert got["cc"] == "TEST_cc@example.com" and got["auto_send"] is False
        r = admin.put(f"{BASE_URL}/api/outreach/settings", json={**body, "cc": "not-an-email"})
        assert r.status_code == 400 and "CC" in r.json()["detail"]
        r = admin.put(f"{BASE_URL}/api/outreach/settings", json=body)  # restore migration values
        assert r.status_code == 200 and r.json()["cc"] == "vineet.narang@fundle.ai" and r.json()["auto_send"] is True

    def test_watchdog_disabled_no_spawn(self, admin):
        assert admin.get(f"{BASE_URL}/api/prospecting/overview").json()["settings"]["enabled"] is False
        r = requests.post(f"{BASE_URL}/api/cron/prospecting-watchdog")
        assert r.status_code == 401
        r = requests.post(f"{BASE_URL}/api/cron/prospecting-watchdog",
                          headers={"Authorization": f"Bearer {os.environ['WEBHOOK_CRON_SECRET']}"})
        assert r.status_code == 200 and r.json()["ok"] is True
        lock = admin.get(f"{BASE_URL}/api/prospecting/overview").json()["lock"]
        assert lock["watchdog_action"] == "ok"
        assert lock["trigger"] != "watchdog" or admin.get(f"{BASE_URL}/api/prospecting/overview").json()["running"] is False
