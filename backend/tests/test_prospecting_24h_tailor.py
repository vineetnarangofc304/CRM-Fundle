# 24h prospecting (day_mode in run_worker, _step day/night mix, _lane AiCreditsOut backoff, contacts queue order,
# watchdog any-hour restart, concurrency clamp 10) + template ai_tailor (tailor() with/without AI, PUT/POST persist).
# SAFETY: unit tests use fakes / an isolated throw-away DB; no LLM calls, no real prospecting runs, no mail sent.
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import prospector as pe, contacts as ct, email_templates as et  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    asyncio.set_event_loop(LOOP)  # other suites (asyncio.run) may clear the current loop
    return LOOP.run_until_complete(coro)


def _fake_db(cap):
    class Col:
        async def find_one_and_update(self, f, u, **k):
            cap["until"] = u["$set"]["until"]
            return {"key": "prospect_lock"}

        async def update_one(self, *a, **k):
            return None

        async def update_many(self, *a, **k):
            return None

        async def find_one(self, *a, **k):
            return {"done": True}

        async def insert_one(self, *a, **k):
            return None

    class DB:
        settings = Col()
        prospects = Col()
        prospect_cats = Col()
    return DB()


# ---------------- run_worker day / night ----------------
class TestRunWorkerDayMode:
    def _setup(self, monkeypatch, cap, seen):
        monkeypatch.setattr(pe, "db", _fake_db(cap))

        async def gs():
            return {"enabled": True, "countries": ["India"], "phase": "auto", "concurrency": 2,
                    "create_leads": False, "daily_cap": 0}

        async def noop(*a, **k):
            return None

        async def lane(deadline, settings, stats):
            seen.append((settings.get("day_mode"), deadline - time.time()))
        monkeypatch.setattr(pe, "get_settings", gs)
        monkeypatch.setattr(pe, "requeue_failed", noop)
        monkeypatch.setattr(pe, "ensure_catalog", noop)
        monkeypatch.setattr(pe, "_lane", lane)
        from routes import leads as lr
        monkeypatch.setattr(lr, "_count_cache", {})

    def test_cron_daytime_runs_in_day_mode(self, monkeypatch):
        cap, seen = {}, []
        self._setup(monkeypatch, cap, seen)
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 0)
        res = run(pe.run_worker(840, "cron"))
        assert "skipped" not in res, res
        assert len(seen) == 2 and all(d is True for d, _ in seen)
        assert all(830 <= r <= 841 for _, r in seen)

    def test_watchdog_daytime_runs_in_day_mode(self, monkeypatch):
        cap, seen = {}, []
        self._setup(monkeypatch, cap, seen)
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 59)
        res = run(pe.run_worker(840, "watchdog"))
        assert "skipped" not in res and all(d is True for d, _ in seen)

    def test_night_caps_at_9am_and_not_day(self, monkeypatch):
        cap, seen = {}, []
        self._setup(monkeypatch, cap, seen)
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 300)
        t0 = datetime.now(timezone.utc)
        run(pe.run_worker(840, "cron"))
        assert all(d is False for d, _ in seen)
        assert all(r <= 301 for _, r in seen)
        secs = (datetime.fromisoformat(cap["until"]) - t0).total_seconds()
        assert 415 <= secs <= 425

    def test_concurrency_lanes_clamped_10(self, monkeypatch):
        cap, seen = {}, []
        self._setup(monkeypatch, cap, seen)

        async def gs():
            return {"enabled": True, "countries": ["India"], "phase": "auto", "concurrency": 50,
                    "create_leads": False, "daily_cap": 0}
        monkeypatch.setattr(pe, "get_settings", gs)
        monkeypatch.setattr(pe, "night_seconds_left", lambda: 0)
        run(pe.run_worker(840, "cron"))
        assert len(seen) == 10


# ---------------- _step ----------------
class TestStep:
    def _patch(self, monkeypatch, calls, contacts_job=True, enrich_job=True, disc=("India", "Fashion")):
        async def gs():
            return {"enabled": True}

        async def under_cap(s):
            return True

        async def nd(s):
            calls.append("next_discovery")
            return disc

        async def discover(c, cat, s):
            calls.append("discover")
            return {"added": 1}

        async def ncj():
            calls.append("next_contacts")
            return {"id": 1} if contacts_job else None

        async def fc(p):
            calls.append("find_contacts")
            return {"ok": True}

        async def ne():
            calls.append("next_enrich")
            return {"id": 2} if enrich_job else None

        async def enrich(p):
            calls.append("enrich")
            return {"ok": True}
        monkeypatch.setattr(pe, "get_settings", gs)
        monkeypatch.setattr(pe, "under_cap", under_cap)
        monkeypatch.setattr(pe, "_next_discovery", nd)
        monkeypatch.setattr(pe, "discover", discover)
        monkeypatch.setattr(ct, "next_contacts_job", ncj)
        monkeypatch.setattr(ct, "find_contacts", fc)
        monkeypatch.setattr(pe, "_next_enrich", ne)
        monkeypatch.setattr(pe, "enrich", enrich)

    @pytest.mark.parametrize("phase", ["auto", "discovery", "enrichment"])
    def test_day_never_discovers_contacts_first(self, monkeypatch, phase):
        for _ in range(30):
            calls = []
            self._patch(monkeypatch, calls)
            st = {"added": 0, "enriched": 0, "discover_runs": 0}
            assert run(pe._step({"phase": phase, "day_mode": True}, st)) is True
            assert "discover" not in calls and "next_discovery" not in calls
            assert calls[:2] == ["next_contacts", "find_contacts"]

    def test_day_enrich_when_no_contacts_job(self, monkeypatch):
        calls = []
        self._patch(monkeypatch, calls, contacts_job=False)
        st = {"added": 0, "enriched": 0, "discover_runs": 0}
        assert run(pe._step({"phase": "auto", "day_mode": True}, st)) is True
        assert calls == ["next_contacts", "next_enrich", "enrich"] and st["enriched"] == 1

    def test_day_stops_when_nothing_to_do(self, monkeypatch):
        calls = []
        self._patch(monkeypatch, calls, contacts_job=False, enrich_job=False)
        st = {"added": 0, "enriched": 0, "discover_runs": 0}
        assert run(pe._step({"phase": "auto", "day_mode": True}, st)) is False
        assert "next_discovery" not in calls

    def test_night_mix_mostly_discovery(self, monkeypatch):
        n_disc = 0
        for _ in range(300):
            calls = []
            self._patch(monkeypatch, calls)
            st = {"added": 0, "enriched": 0, "discover_runs": 0}
            run(pe._step({"phase": "auto", "day_mode": False}, st))
            n_disc += "discover" in calls
        assert 0.55 <= n_disc / 300 <= 0.85, n_disc


# ---------------- _lane AiCreditsOut ----------------
class TestLane:
    def test_credits_out_sleeps_then_continues(self, monkeypatch):
        n, sleeps = {"c": 0}, []

        async def step(s, st):
            n["c"] += 1
            if n["c"] == 1:
                raise pe.AiCreditsOut("x")
            return False

        async def fsleep(t):
            sleeps.append(t)
        monkeypatch.setattr(pe, "_step", step)
        monkeypatch.setattr(pe.asyncio, "sleep", fsleep)
        run(pe._lane(time.time() + 600, {}, {}))
        assert sleeps == [60] and n["c"] == 2

    def test_credits_out_near_deadline_returns(self, monkeypatch):
        n, sleeps = {"c": 0}, []

        async def step(s, st):
            n["c"] += 1
            raise pe.AiCreditsOut("x")

        async def fsleep(t):
            sleeps.append(t)
        monkeypatch.setattr(pe, "_step", step)
        monkeypatch.setattr(pe.asyncio, "sleep", fsleep)
        run(pe._lane(time.time() + 80, {}, {}))
        assert n["c"] == 1 and sleeps == []


# ---------------- watchdog route ----------------
class TestWatchdogAnyHour:
    @pytest.mark.parametrize("enabled,left,spawn", [(True, 3600, True), (True, 0, True), (False, 0, False), (False, 3600, False)])
    def test_spawn(self, monkeypatch, enabled, left, spawn):
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
        assert run(rp.cron_watchdog(None))["ok"] is True
        assert bool(spawned) == spawn


# ---------------- contacts queue order (isolated DB) ----------------
class TestContactsQueue:
    def test_order(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        monkeypatch.setattr(ct, "db", d)
        try:
            run(d.prospects.insert_many([
                {"id": 1, "status": "discovered", "store_count": 999},
                {"id": 2, "status": "enriched", "store_count": 5},
                {"id": 3, "status": "enriched", "store_count": 50, "contacts_status": "done"},
                {"id": 4, "status": "enriched", "store_count": 10},
                {"id": 5, "status": "failed", "store_count": 1000},
            ]))
            got = [run(ct.next_contacts_job()) for _ in range(4)]
            assert [g["id"] if g else None for g in got] == [4, 2, 1, None]
            assert run(d.prospects.find_one({"id": 4}))["contacts_status"] == "running"
        finally:
            run(cli.drop_database(name))


# ---------------- tailor ----------------
class TestTailor:
    P = {"brand_name": "TEST_Brand", "category": "Fashion", "country": "India"}
    C = {"name": "Asha Rao", "role": "CEO"}
    T = {"subject": "Hi {{first_name}} at {{brand}}", "body": "<p>Hello {{first_name}} from {{brand}}</p>"}

    def test_ai_tailor_false_no_ai(self, monkeypatch):
        called = []

        async def ai(*a, **k):
            called.append(1)
            return {"subject": "AI", "body": "<p>AI</p>"}
        monkeypatch.setattr(et, "ai_text", ai)
        s, b = run(et.tailor({**self.T, "ai_tailor": False}, self.P, self.C))
        assert called == [] and "Asha" in s and "TEST_Brand" in s and "TEST_Brand" in b and "{{" not in b

    def test_ai_tailor_true_uses_ai(self, monkeypatch):
        async def ai(*a, **k):
            return {"subject": "AI subj", "body": "<p>AI body</p>"}
        monkeypatch.setattr(et, "ai_text", ai)
        s, b = run(et.tailor({**self.T, "ai_tailor": True}, self.P, self.C))
        assert s == "AI subj" and "AI body" in b
        s, b = run(et.tailor(dict(self.T), self.P, self.C))  # missing key -> AI
        assert s == "AI subj"

    def test_ai_tailor_true_fallback(self, monkeypatch):
        async def ai(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr(et, "ai_text", ai)
        s, b = run(et.tailor({**self.T, "ai_tailor": True}, self.P, self.C))
        assert "Asha" in s and "TEST_Brand" in b


# ---------------- API ----------------
@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return s


class TestTemplateApi:
    def test_create_update_ai_tailor(self, admin):
        r = admin.post(f"{BASE_URL}/api/outreach/templates", json={
            "name": "TEST_tailor", "subject": "S {{brand}}", "body": "<p>B</p>", "ai_tailor": False, "active": False})
        assert r.status_code == 200, r.text
        t = r.json()
        try:
            assert t["ai_tailor"] is False
            lst = admin.get(f"{BASE_URL}/api/outreach/templates").json()
            assert next(x for x in lst if x["id"] == t["id"])["ai_tailor"] is False
            u = admin.put(f"{BASE_URL}/api/outreach/templates/{t['id']}", json={
                "name": "TEST_tailor", "subject": "S", "body": "<p>B</p>", "ai_tailor": True, "active": False})
            assert u.status_code == 200 and u.json()["ai_tailor"] is True
            u = admin.put(f"{BASE_URL}/api/outreach/templates/{t['id']}", json={
                "name": "TEST_tailor", "subject": "S", "body": "<p>B</p>", "active": False})
            assert u.json()["ai_tailor"] is True  # default
            r2 = admin.post(f"{BASE_URL}/api/outreach/templates", json={"name": "TEST_t2", "subject": "S", "body": "<p>B</p>", "active": False})
            assert r2.json()["ai_tailor"] is True
            admin.delete(f"{BASE_URL}/api/outreach/templates/{r2.json()['id']}")
        finally:
            assert admin.delete(f"{BASE_URL}/api/outreach/templates/{t['id']}").status_code == 200


class TestSettingsClamp:
    def test_concurrency_clamp(self, admin):
        cur = admin.get(f"{BASE_URL}/api/prospecting/overview").json()["settings"]
        body = {k: cur[k] for k in ("countries", "phase", "concurrency", "create_leads")}
        try:
            r = admin.put(f"{BASE_URL}/api/prospecting/settings", json={**body, "enabled": False, "concurrency": 25})
            assert r.status_code == 200 and r.json()["concurrency"] == 10
            r = admin.put(f"{BASE_URL}/api/prospecting/settings", json={**body, "enabled": False, "concurrency": 10})
            assert r.json()["concurrency"] == 10
            r = admin.put(f"{BASE_URL}/api/prospecting/settings", json={**body, "enabled": False, "concurrency": 8})
            assert r.json()["concurrency"] == 8
        finally:
            admin.put(f"{BASE_URL}/api/prospecting/settings", json={**body, "enabled": False})
        assert admin.get(f"{BASE_URL}/api/prospecting/overview").json()["settings"]["enabled"] is False
