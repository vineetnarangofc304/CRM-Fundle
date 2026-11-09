# AI prospecting: requeue_failed() on worker start — failed brands + failed contact lookups go back to queue
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
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
IDS = list(range(990001, 990014))
FMT = "%Y-%m-%d %H:%M:%S"


def ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).strftime(FMT)


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module", autouse=True)
def seed():
    mdb.prospects.delete_many({"id": {"$in": IDS}})
    b = {"country": "TEST_Land", "category": "TEST_cat", "store_count": None}
    q = "litellm.APIError: Daily spend limit reached"
    docs = [
        # brand failures
        {"id": 990001, "status": "failed", "enrich_attempts": 3, "last_error": q, "failed_at": ago(0.1)},
        {"id": 990002, "status": "failed", "enrich_attempts": 3, "last_error": "No JSON in model reply"},
        {"id": 990003, "status": "failed", "enrich_attempts": 3, "last_error": "timeout", "failed_at": ago(7), "retry_rounds": 2},
        {"id": 990004, "status": "failed", "enrich_attempts": 3, "last_error": "timeout", "failed_at": ago(1)},
        {"id": 990005, "status": "failed", "enrich_attempts": 3, "last_error": "timeout", "retry_rounds": 5},
        {"id": 990006, "status": "failed", "enrich_attempts": 3, "last_error": "timeout", "failed_at": ago(10), "retry_rounds": 7},
        # contacts failures
        {"id": 990007, "status": "enriched", "contacts_status": "failed", "contacts_error": "Daily spend limit reached",
         "contacts_failed_at": ago(0.1)},
        {"id": 990008, "status": "enriched", "contacts_status": "failed", "contacts_error": "bad json"},
        {"id": 990009, "status": "enriched", "contacts_status": "failed", "contacts_error": "bad json",
         "contacts_failed_at": ago(8), "contacts_retry_rounds": 1},
        {"id": 990010, "status": "enriched", "contacts_status": "failed", "contacts_error": "bad json",
         "contacts_failed_at": ago(2)},
        {"id": 990011, "status": "enriched", "contacts_status": "failed", "contacts_error": "bad json",
         "contacts_retry_rounds": 5},
        # pending discovered (should stay pending/discovered when credits blocked)
        {"id": 990012, "status": "discovered", "enrich_attempts": 0},
    ]
    mdb.prospects.insert_many([{**b, **d, "brand_name": f"TEST_rq_{d['id']}", "brand_key": f"test_rq_{d['id']}"} for d in docs])
    yield
    mdb.prospects.delete_many({"id": {"$in": IDS}})
    mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": True}})


def g(i):
    return mdb.prospects.find_one({"id": i}, {"_id": 0})


class TestRequeue:
    def test_run_worker(self, client):
        mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": True}}, upsert=True)
        prev = (mdb.settings.find_one({"key": "prospect_lock"}) or {}).get("last_finished")
        t0 = time.time()
        r = client.post(f"{BASE_URL}/api/prospecting/run")
        assert r.status_code == 200 and r.json()["ok"] is True
        done = None
        while time.time() - t0 < 90:
            lk = mdb.settings.find_one({"key": "prospect_lock"}) or {}
            if lk.get("until") == "" and lk.get("last_finished") != prev:
                done = lk
                break
            time.sleep(1)
        print("worker took", round(time.time() - t0, 1), "stats", (done or {}).get("last_stats"))
        assert done is not None

    def test_quota_failed_requeued(self):
        p = g(990001)
        assert p["status"] == "discovered" and p["enrich_attempts"] == 0 and "last_error" not in p

    def test_nonquota_no_failed_at_requeued(self):
        p = g(990002)
        assert p["status"] == "discovered" and p["enrich_attempts"] == 0 and p["retry_rounds"] == 1

    def test_nonquota_old_failed_at_requeued(self):
        p = g(990003)
        assert p["status"] == "discovered" and p["enrich_attempts"] == 0 and p["retry_rounds"] == 3

    def test_recent_failed_stays(self):
        p = g(990004)
        assert p["status"] == "failed" and p["enrich_attempts"] == 3 and "retry_rounds" not in p

    def test_max_rounds_stays(self):
        assert g(990005)["status"] == "failed" and g(990005)["retry_rounds"] == 5
        assert g(990006)["status"] == "failed" and g(990006)["retry_rounds"] == 7

    def test_contacts_quota_unset(self):
        p = g(990007)
        assert "contacts_status" not in p and "contacts_error" not in p

    def test_contacts_nonquota_requeued(self):
        p = g(990008)
        assert "contacts_status" not in p and p["contacts_retry_rounds"] == 1
        p = g(990009)
        assert "contacts_status" not in p and p["contacts_retry_rounds"] == 2

    def test_contacts_recent_and_max_stay(self):
        assert g(990010)["contacts_status"] == "failed"
        assert g(990011)["contacts_status"] == "failed" and g(990011)["contacts_retry_rounds"] == 5

    def test_pending_stays_discovered(self):
        p = g(990012)
        assert p["status"] == "discovered" and p.get("enrich_attempts", 0) == 0
        assert mdb.prospects.count_documents({"id": {"$in": IDS}, "status": "enriching"}) == 0

    def test_overview(self, client):
        r = client.get(f"{BASE_URL}/api/prospecting/overview")
        assert r.status_code == 200
        d = r.json()
        assert d["settings"]["enabled"] is True and d["lock"]["ai_blocked_at"]
        assert d["pending_enrich"] > 0


# Method-level: enrich() writes failed_at, find_contacts() writes contacts_failed_at on non-quota errors
class TestFailedTimestamps:
    def test_enrich_and_contacts_write_timestamps(self, monkeypatch):
        sys.path.insert(0, "/app/backend")
        from core import prospector as pe
        from core import contacts as ct

        async def boom(*a, **k):
            raise ValueError("TEST_boom")

        async def nolog(*a, **k):
            return None
        monkeypatch.setattr(pe, "_ask", boom)
        monkeypatch.setattr(pe, "_log_run", nolog)
        mdb.prospects.update_one({"id": 990013}, {"$set": {"brand_name": "TEST_rq_990013", "brand_key": "test_rq_990013",
                                 "country": "TEST_Land", "category": "TEST_cat", "status": "enriching", "enrich_attempts": 2}},
                                 upsert=True)
        p = g(990013)

        async def go():
            from motor.motor_asyncio import AsyncIOMotorClient
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            d = cli[os.environ["DB_NAME"]]
            monkeypatch.setattr(pe, "db", d)
            monkeypatch.setattr(ct, "db", d)
            r1 = await pe.enrich(p)
            r2 = await ct.find_contacts(p)
            return r1, r2
        r1, r2 = asyncio.run(go())
        q = g(990013)
        print(q)
        assert r1 == {"ok": False} and r2 == {"ok": False}
        assert q["status"] == "failed" and q["last_error"] == "TEST_boom"
        assert q["failed_at"] > ago(0.05) and len(q["failed_at"]) == 19
        assert q["contacts_status"] == "failed" and q["contacts_failed_at"] > ago(0.05)
