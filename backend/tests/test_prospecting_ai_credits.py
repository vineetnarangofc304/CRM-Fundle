# AI prospecting: credit-limit (spend limit) handling — fast stop, ai_blocked_at, no failed marking, worker reset
import os
import time
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
TEST_IDS = [9990001, 9990002]


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module", autouse=True)
def test_prospects():
    mdb.prospects.delete_many({"id": {"$in": TEST_IDS}})
    base = {"country": "TEST_Land", "category": "TEST_cat", "store_count": None, "brand_key": "test"}
    mdb.prospects.insert_many([
        {**base, "id": 9990001, "brand_name": "TEST_quota_failed", "brand_key": "test_quota_failed", "status": "failed",
         "enrich_attempts": 3, "last_error": "litellm.APIError: OpenAIException - Daily spend limit reached"},
        {**base, "id": 9990002, "brand_name": "TEST_quota_contacts", "brand_key": "test_quota_contacts", "status": "enriched",
         "enrich_attempts": 1, "contacts_status": "failed", "contacts_error": "Daily spend limit reached"},
    ])
    yield
    mdb.prospects.delete_many({"id": {"$in": TEST_IDS}})


class TestAiCredits:
    state = {}

    def test_cron_requires_secret(self):
        r = requests.post(f"{BASE_URL}/api/cron/prospecting")
        assert r.status_code == 401
        r = requests.post(f"{BASE_URL}/api/cron/prospecting", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_run_stops_fast_and_sets_blocked(self, client):
        mdb.settings.update_one({"key": "prospect_lock"}, {"$unset": {"ai_blocked_at": 1, "ai_blocked_reason": 1}})
        before_failed = mdb.prospects.count_documents({"status": "failed", "id": {"$nin": TEST_IDS}})
        TestAiCredits.state["before_failed"] = before_failed
        TestAiCredits.state["before_attempts"] = {d["id"]: d.get("enrich_attempts", 0) for d in mdb.prospects.find(
            {"status": "discovered", "id": {"$nin": TEST_IDS}}, {"_id": 0, "id": 1, "enrich_attempts": 1})}
        prev_finished = (mdb.settings.find_one({"key": "prospect_lock"}) or {}).get("last_finished")
        t0 = time.time()
        r = client.post(f"{BASE_URL}/api/prospecting/run")
        assert r.status_code == 200 and r.json().get("ok") is True
        finished = None
        while time.time() - t0 < 90:
            lk = mdb.settings.find_one({"key": "prospect_lock"}) or {}
            if lk.get("until") == "" and lk.get("last_finished") and lk.get("last_finished") != prev_finished:
                finished = lk
                break
            time.sleep(1)
        took = time.time() - t0
        print(f"worker finished in {took:.1f}s lock={finished}")
        assert finished is not None, "worker did not finish within 90s"
        assert took < 60
        assert finished.get("ai_blocked_at")
        assert "spend limit" in finished.get("ai_blocked_reason", "").lower()

    def test_overview_returns_blocked(self, client):
        r = client.get(f"{BASE_URL}/api/prospecting/overview")
        assert r.status_code == 200
        d = r.json()
        assert d["lock"]["ai_blocked_at"]
        assert d["settings"]["enabled"] is True
        assert d["pending_enrich"] >= 2000
        print("pending_enrich", d["pending_enrich"], "totals", d.get("totals"))

    def test_worker_reset_quota_failed(self):
        p1 = mdb.prospects.find_one({"id": 9990001})
        assert p1["status"] == "discovered"
        assert p1["enrich_attempts"] == 0
        assert "last_error" not in p1
        p2 = mdb.prospects.find_one({"id": 9990002})
        assert "contacts_status" not in p2 and "contacts_error" not in p2

    def test_no_new_failed_and_attempts_not_consumed(self):
        after_failed = mdb.prospects.count_documents({"status": "failed", "id": {"$nin": TEST_IDS}})
        assert after_failed <= TestAiCredits.state["before_failed"]
        assert mdb.prospects.count_documents({"status": "enriching"}) == 0
        changed = []
        for d in mdb.prospects.find({"id": {"$in": list(TestAiCredits.state["before_attempts"])}},
                                    {"_id": 0, "id": 1, "enrich_attempts": 1, "status": 1}):
            if d.get("enrich_attempts", 0) != TestAiCredits.state["before_attempts"][d["id"]] or d["status"] != "discovered":
                changed.append(d)
        print("changed", changed[:5])
        assert not changed

    def test_prospects_list_regression(self, client):
        r = client.get(f"{BASE_URL}/api/prospecting/prospects", params={"limit": 5})
        assert r.status_code == 200
