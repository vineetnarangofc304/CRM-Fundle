# AI prospecting: Gemini 429 / RateLimit / "No deployments available" detected as quota -> AiCreditsOut, PRO->FLASH fallback, requeue
import os
import sys
import asyncio
import re
from datetime import datetime, timezone, timedelta
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from core import prospector as pe  # noqa: E402

mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
IDS = list(range(991001, 991010))
FMT = "%Y-%m-%d %H:%M:%S"
E429 = ('Failed to generate chat completion: litellm.RateLimitError: RateLimitError: OpenAIException - '
        'GeminiException - {"error": {"code": 429, "message": "You exceeded your current quota, please check your plan"}}')
ENODEP = "litellm.APIError: No deployments available for selected model gemini-3.1-pro-preview"


def ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).strftime(FMT)


def g(i):
    return mdb.prospects.find_one({"id": i}, {"_id": 0})


@pytest.fixture(scope="module", autouse=True)
def seed():
    lk = mdb.settings.find_one({"key": "prospect_lock"})
    mdb.prospects.delete_many({"id": {"$in": IDS}})
    b = {"country": "TEST_Land", "category": "TEST_cat", "store_count": None}
    docs = [
        {"id": 991001, "status": "failed", "enrich_attempts": 3, "last_error": E429[:200], "failed_at": ago(0.1)},
        {"id": 991002, "status": "failed", "enrich_attempts": 3, "last_error": ENODEP, "failed_at": ago(0.1), "retry_rounds": 5},
        {"id": 991003, "status": "failed", "enrich_attempts": 3, "last_error": "RESOURCE_EXHAUSTED", "failed_at": ago(0.1)},
        {"id": 991004, "status": "failed", "enrich_attempts": 3, "last_error": "timeout", "failed_at": ago(0.1)},
        {"id": 991005, "status": "enriched", "contacts_status": "failed", "contacts_error": E429[:200], "contacts_failed_at": ago(0.1)},
        {"id": 991006, "status": "enriching", "enrich_attempts": 1},
    ]
    mdb.prospects.insert_many([{**b, **d, "brand_name": f"TEST_qm_{d['id']}", "brand_key": f"test_qm_{d['id']}"} for d in docs])
    yield
    mdb.prospects.delete_many({"id": {"$in": IDS}})
    if lk:
        mdb.settings.replace_one({"_id": lk["_id"]}, lk)
    mdb.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}}, upsert=True)


def run(coro_fn, monkeypatch):
    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        d = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        monkeypatch.setattr(pe, "db", d)
        return await coro_fn()
    return asyncio.run(go())


class TestMarkers:
    @pytest.mark.parametrize("msg", [E429, ENODEP, "RateLimitError", "rate limit hit", "RESOURCE_EXHAUSTED",
                                     "Daily spend limit reached", "insufficient_quota"])
    def test_is_quota_true(self, msg):
        assert pe._is_quota(Exception(msg))
        assert re.search(pe.QUOTA_REGEX, msg, re.I)

    @pytest.mark.parametrize("msg", ["timeout", "No JSON in model reply", "bad json", "Connection reset"])
    def test_is_quota_false(self, msg):
        assert not pe._is_quota(Exception(msg))


class TestAsk:
    def test_pro_quota_falls_back_to_flash(self, monkeypatch):
        calls = []

        async def fake(model, system, prompt):
            calls.append(model)
            if model == pe.PRO:
                raise Exception(ENODEP)
            return type("R", (), {"content": "ok-flash"})()
        monkeypatch.setattr(pe, "_call", fake)
        out = run(lambda: pe._ask(pe.PRO, "s", "p"), monkeypatch)
        assert out == "ok-flash" and calls == [pe.PRO, pe.FLASH]
        assert not (mdb.settings.find_one({"key": "prospect_lock"}) or {}).get("ai_blocked_at")

    def test_both_quota_raises_and_sets_blocked(self, monkeypatch):
        calls = []

        async def fake(model, system, prompt):
            calls.append(model)
            raise Exception(E429)
        monkeypatch.setattr(pe, "_call", fake)
        mdb.settings.update_one({"key": "prospect_lock"}, {"$unset": {"ai_blocked_at": 1, "ai_blocked_reason": 1}})
        with pytest.raises(pe.AiCreditsOut):
            run(lambda: pe._ask(pe.PRO, "s", "p"), monkeypatch)
        assert calls == [pe.PRO, pe.FLASH]
        lk = mdb.settings.find_one({"key": "prospect_lock"})
        assert lk.get("ai_blocked_at") and "429" in lk.get("ai_blocked_reason", "")

    def test_flash_quota_no_retry(self, monkeypatch):
        calls = []

        async def fake(model, system, prompt):
            calls.append(model)
            raise Exception(E429)
        monkeypatch.setattr(pe, "_call", fake)
        with pytest.raises(pe.AiCreditsOut):
            run(lambda: pe._ask(pe.FLASH, "s", "p"), monkeypatch)
        assert calls == [pe.FLASH]

    def test_pro_nonquota_no_fallback(self, monkeypatch):
        calls = []

        async def fake(model, system, prompt):
            calls.append(model)
            raise ValueError("timeout")
        monkeypatch.setattr(pe, "_call", fake)
        with pytest.raises(ValueError):
            run(lambda: pe._ask(pe.PRO, "s", "p"), monkeypatch)
        assert calls == [pe.PRO]

    def test_enrich_429_does_not_burn_attempts(self, monkeypatch):
        async def fake(model, system, prompt):
            raise Exception(E429)

        async def nolog(*a, **k):
            return None
        monkeypatch.setattr(pe, "_call", fake)
        monkeypatch.setattr(pe, "_log_run", nolog)
        p = g(991006)
        with pytest.raises(pe.AiCreditsOut):
            run(lambda: pe.enrich(p), monkeypatch)
        q = g(991006)
        assert q["status"] == "discovered" and q["enrich_attempts"] == 1 and "last_error" not in q


class TestRequeue:
    def test_requeue_quota_errors(self, monkeypatch):
        run(pe.requeue_failed, monkeypatch)
        for i in (991001, 991002, 991003):
            p = g(i)
            assert p["status"] == "discovered" and p["enrich_attempts"] == 0 and "last_error" not in p, p
        assert "retry_rounds" not in g(991001)
        assert g(991002)["retry_rounds"] == 5  # not incremented, max-rounds ignored for quota
        p = g(991004)
        assert p["status"] == "failed" and "retry_rounds" not in p  # non-quota recent stays (6h cooldown)
        p = g(991005)
        assert "contacts_status" not in p and "contacts_error" not in p and "contacts_retry_rounds" not in p
