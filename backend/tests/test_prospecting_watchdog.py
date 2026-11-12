# AI prospecting nonstop: no daily cap, 12 angles, angles_v2 migration, hourly watchdog cron endpoint
import os
import sys
import time
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
# T1 iter14: these tests start REAL engine batches on the shared DB (AI now has credits -> real discovery/contacts
# + leads). Gated so regression runs never mutate real data; set RUN_LIVE_PROSPECTING=1 to run deliberately.
pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_PROSPECTING") != "1", reason="starts real prospecting runs")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
SECRET = os.environ["WEBHOOK_CRON_SECRET"]
FMT = "%Y-%m-%d %H:%M:%S"
WD = f"{BASE_URL}/api/cron/prospecting-watchdog"
AUTH = {"Authorization": f"Bearer {SECRET}"}
TEST_CAT = {"country": "TEST_Land", "category": "TEST_wd_cat"}


def ago(m):
    return (datetime.now(timezone.utc) - timedelta(minutes=m)).strftime(FMT)


def lock():
    return mdb.settings.find_one({"key": "prospect_lock"}, {"_id": 0}) or {}


def set_lock(**kw):
    mdb.settings.update_one({"key": "prospect_lock"}, {"$set": kw}, upsert=True)


def wait_finished(prev, t=120):
    t0 = time.time()
    while time.time() - t0 < t:
        lk = lock()
        if lk.get("until") == "" and lk.get("last_finished") != prev:
            return lk
        time.sleep(1)
    return None


@pytest.fixture(scope="module", autouse=True)
def backup():
    lk = mdb.settings.find_one({"key": "prospect_lock"})
    pr = mdb.settings.find_one({"key": "prospecting"})
    flag = mdb.settings.find_one({"key": "prospect_angles_v2"})
    cats = list(mdb.prospect_cats.find({}, {"_id": 1, "saturated": 1, "empty_streak": 1}))
    yield
    if lk:
        mdb.settings.replace_one({"_id": lk["_id"]}, lk)
    if pr:
        mdb.settings.replace_one({"_id": pr["_id"]}, pr)
    mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}}, upsert=True)
    if not flag:
        mdb.settings.delete_one({"key": "prospect_angles_v2"})
    mdb.prospect_cats.delete_many(TEST_CAT)
    for c in cats:
        mdb.prospect_cats.update_one({"_id": c["_id"]}, {"$set": {k: c[k] for k in ("saturated", "empty_streak") if k in c}})


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


class TestCapAndAngles:
    def test_angles(self):
        from core import prospector as pe
        assert len(pe.ANGLES) == 12
        joined = " ".join(pe.ANGLES).lower()
        for kw in ("linkedin", "store", "funding", "newsletter", "d2c"):
            assert kw in joined
        for c in ("India", "UAE", "United Kingdom"):
            for i in range(12):
                assert pe._angle_text(c, i)

    def test_daily_cap_default_zero_via_api(self, client):
        r = client.get(f"{BASE_URL}/api/prospecting/overview")
        assert r.status_code == 200
        assert r.json()["settings"]["daily_cap"] == 0

    def test_under_cap_true_above_500(self, monkeypatch):
        from core import prospector as pe

        async def many():
            return 9999
        monkeypatch.setattr(pe, "added_today", many)
        assert asyncio.run(pe.under_cap({"daily_cap": 0})) is True
        assert asyncio.run(pe.under_cap({"daily_cap": 500})) is False


class TestWatchdog:
    def test_no_auth_401(self):
        assert requests.post(WD).status_code == 401
        assert requests.post(WD, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert requests.post(WD, headers={"Authorization": SECRET}).status_code == 401

    def test_healthy_ok(self, client):
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}})
        set_lock(until="", started=ago(5), last_finished=ago(5))
        r = requests.post(WD, headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d == {"ok": True, "stuck": False, "restart": False}
        lk = lock()
        assert lk["watchdog_action"] == "ok" and lk["watchdog_at"] >= ago(1)
        ov = client.get(f"{BASE_URL}/api/prospecting/overview").json()["lock"]
        assert ov["watchdog_action"] == "ok" and ov["watchdog_at"] == lk["watchdog_at"]

    def test_running_not_stuck(self):
        fut = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        set_lock(until=fut, started=ago(5), last_finished=ago(60))
        d = requests.post(WD, headers=AUTH).json()
        assert d["stuck"] is False and d["restart"] is False
        assert lock()["until"] == fut
        assert lock()["watchdog_action"] == "ok"

    def test_stuck_freed_disabled_no_spawn(self):
        fut = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        set_lock(until=fut, started=ago(25), last_finished=ago(40), trigger="cron")
        d = requests.post(WD, headers=AUTH).json()
        assert d["stuck"] is True and d["restart"] is True
        time.sleep(3)
        lk = lock()
        assert lk["until"] == "" and lk["trigger"] == "cron"  # disabled -> run_worker not spawned
        assert lk["watchdog_action"] == "ok"  # disabled -> nothing restarted

    def test_restart_spawns_worker_and_migration_once(self):
        mdb.settings.delete_one({"key": "prospect_angles_v2"})
        mdb.prospect_cats.update_one(TEST_CAT, {"$set": {"saturated": True, "empty_streak": 5}}, upsert=True)
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": True}})
        prev = ago(45)
        set_lock(until="", started=prev, last_finished=prev)
        d = requests.post(WD, headers=AUTH).json()
        assert d["stuck"] is False and d["restart"] is True
        assert lock()["watchdog_action"] == "restarted"
        time.sleep(6)
        assert lock().get("trigger") == "watchdog"
        c = mdb.prospect_cats.find_one(TEST_CAT)
        # stop lanes quickly (preview AI quota errors otherwise keep the batch busy for 14 min)
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}})
        done = wait_finished(prev, 150)
        assert done is not None, "watchdog-spawned worker did not finish"
        assert c["saturated"] is False and c["empty_streak"] == 0
        assert mdb.settings.count_documents({"key": "prospect_angles_v2"}) == 1
        # second run: migration must NOT run again
        mdb.prospect_cats.update_one(TEST_CAT, {"$set": {"saturated": True, "empty_streak": 5}})
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": True}})
        prev2 = ago(45)
        set_lock(until="", last_finished=prev2)
        assert requests.post(WD, headers=AUTH).json()["restart"] is True
        time.sleep(6)
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}})
        done2 = wait_finished(prev2, 150)
        assert done2 is not None
        c = mdb.prospect_cats.find_one(TEST_CAT)
        assert c["saturated"] is True and c["empty_streak"] == 5
        assert mdb.settings.count_documents({"key": "prospect_angles_v2"}) == 1

    def test_lock_contention_after_restart(self):
        # a second watchdog right after a restart must not report idle restart (last_finished fresh)
        d = requests.post(WD, headers=AUTH).json()
        assert d["restart"] is False
