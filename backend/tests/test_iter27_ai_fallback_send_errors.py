# iter27: prospector._ask model fallback on rate-limit (mocked _call, no real LLM), overview lock ai_blocked_reason,
# brain._send_now clear 'No usable inbox' errors, _sender_id('main') check, send_queued 'Nothing sent'. No real email.
import asyncio
import os
import sys
import time

import pytest
import requests

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from core.db import db  # noqa: E402
from core import prospector as pe, brain, mailboxes as mb, mailer  # noqa: E402

run = LOOP.run_until_complete
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
RL = "litellm.RateLimitError: GeminiException - TEST Resource has been exhausted (429)"
CTX = {"user": {"id": 1, "name": "TEST_runner"}, "session_id": 99999927}


@pytest.fixture(scope="module", autouse=True)
def keep_lock():
    orig = run(db.settings.find_one({"key": "prospect_lock"}, {"_id": 0})) or {}
    yield
    keys = {k: orig[k] for k in ("ai_blocked_at", "ai_blocked_reason") if k in orig}
    unset = {k: 1 for k in ("ai_blocked_at", "ai_blocked_reason") if k not in orig}
    upd = {**({"$set": keys} if keys else {}), **({"$unset": unset} if unset else {})}
    if upd:
        run(db.settings.update_one({"key": "prospect_lock"}, upd))
    pe._limited.clear()
    pe._blocked["on"] = None


def _mock(monkeypatch, behaviour):
    calls = []

    async def fake(model, system, prompt):
        calls.append(model)
        b = behaviour(model)
        if isinstance(b, Exception):
            raise b
        return b
    monkeypatch.setattr(pe, "_call", fake)
    return calls


def _lock():
    return run(db.settings.find_one({"key": "prospect_lock"}, {"_id": 0})) or {}


# _ask: rate-limited FLASH -> next model, FLASH marked limited ~30 min, skipped on next call
def test_fallback_on_rate_limit(monkeypatch):
    pe._limited.clear()
    calls = _mock(monkeypatch, lambda m: Exception(RL) if m == pe.FLASH else f"ok-{m}")
    assert run(pe._ask(pe.FLASH, "s", "p")) == "ok-gemini-3-flash-preview"
    assert calls == [pe.FLASH, "gemini-3-flash-preview"]
    assert 1700 < pe._limited[pe.FLASH] - time.time() <= 1800
    calls.clear()
    assert run(pe._ask(pe.FLASH, "s", "p")) == "ok-gemini-3-flash-preview"
    assert calls == ["gemini-3-flash-preview"], "limited model should be skipped"


# PRO chain order and expiry of limit
def test_pro_chain_and_expiry(monkeypatch):
    pe._limited.clear()
    calls = _mock(monkeypatch, lambda m: Exception(RL) if m in (pe.PRO, pe.FLASH, "gemini-3-flash-preview") else "ok")
    assert run(pe._ask(pe.PRO, "s", "p")) == "ok"
    assert calls == [pe.PRO, pe.FLASH, "gemini-3-flash-preview", "gemini-2.5-flash"]
    pe._limited[pe.PRO] = time.time() - 1  # expired
    calls.clear()
    run(pe._ask(pe.PRO, "s", "p"))
    assert calls[0] == pe.PRO


# all models limited -> AiCreditsOut + lock ai_blocked_at/reason; success clears them
def test_all_limited_then_success_clears(monkeypatch):
    pe._limited.clear()
    calls = _mock(monkeypatch, lambda m: Exception(RL))
    with pytest.raises(pe.AiCreditsOut):
        run(pe._ask(pe.FLASH, "s", "p"))
    assert calls == [pe.FLASH, "gemini-3-flash-preview", "gemini-2.5-flash"]
    lk = _lock()
    assert lk.get("ai_blocked_at") and "RateLimitError" in lk.get("ai_blocked_reason", "")
    assert len(lk["ai_blocked_reason"]) <= 200
    # every model limited now -> chain falls back to the full list (retries all) rather than raising instantly
    calls.clear()
    with pytest.raises(pe.AiCreditsOut):
        run(pe._ask(pe.FLASH, "s", "p"))
    assert len(calls) >= 1
    calls2 = _mock(monkeypatch, lambda m: "fine")
    assert run(pe._ask(pe.FLASH, "s", "p")) == "fine"
    lk = _lock()
    assert "ai_blocked_at" not in lk and "ai_blocked_reason" not in lk
    assert calls2


# non-quota error: no fallback, no AiCreditsOut
def test_non_quota_error_no_fallback(monkeypatch):
    pe._limited.clear()
    run(db.settings.update_one({"key": "prospect_lock"}, {"$unset": {"ai_blocked_at": 1, "ai_blocked_reason": 1}}))
    calls = _mock(monkeypatch, lambda m: RuntimeError("TEST bad json schema"))
    with pytest.raises(RuntimeError):
        run(pe._ask(pe.FLASH, "s", "p"))
    assert calls == [pe.FLASH] and not pe._limited
    assert "ai_blocked_at" not in _lock()


# overview lock includes ai_blocked_reason (HTTP)
def test_overview_lock_reason():
    tok = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}).json()["access_token"]
    run(db.settings.update_one({"key": "prospect_lock"}, {"$set": {"ai_blocked_at": "2026-01-01 00:00:00", "ai_blocked_reason": "TEST_reason RateLimitError"}}))
    try:
        r = requests.get(f"{BASE_URL}/api/prospecting/overview", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        lk = r.json()["lock"]
        assert lk["ai_blocked_reason"] == "TEST_reason RateLimitError" and lk["ai_blocked_at"] == "2026-01-01 00:00:00"
        assert r.json()["settings"]["enabled"] is False
    finally:
        run(db.settings.update_one({"key": "prospect_lock"}, {"$unset": {"ai_blocked_at": 1, "ai_blocked_reason": 1}}))


# ---- brain send errors ----
@pytest.fixture
def created():
    ids = []
    yield ids
    run(db.outreach.delete_many({"id": {"$in": ids}}))


def _doc(oid, pin):
    return {"id": oid, "seq_id": oid, "channel": "email", "step": 1, "kind": "brain", "template_name": "TEST",
            "to": "test_iter27@example.com", "subject": "TEST_27", "subject_raw": "TEST_27", "body": "<p>x</p>",
            "force_sender": pin, "status": "approved", "attachments": [], "video_links": []}


def test_sender_id_main_not_connected(monkeypatch):
    async def no():
        return False
    monkeypatch.setattr(mailer, "is_connected", no)
    with pytest.raises(ValueError) as e:
        run(brain._sender_id("main"))
    assert "main inbox isn't connected" in str(e.value)


@pytest.mark.parametrize("pin,expect", [(0, "the main inbox isn't connected"), (987654, "inbox #987654 is inactive"),
                                        (None, "no connected inbox has room this hour")])
def test_send_now_no_inbox_message(monkeypatch, created, pin, expect):
    async def senders(st, day):
        return []
    monkeypatch.setattr(mb, "senders", senders)
    oid = run(brain.next_id("outreach"))
    created.append(oid)
    run(db.outreach.insert_one(_doc(oid, pin)))
    r = run(brain._send_now([oid]))
    assert not r["sent"] and r["failed"][0]["error"].startswith("No usable inbox: ") and expect in r["failed"][0]["error"]
    d = run(db.outreach.find_one({"id": oid}))
    assert d["error"] == r["failed"][0]["error"] and d["status"] == "approved"


def test_send_queued_all_failed_raises(monkeypatch, created):
    async def senders(st, day):
        return []
    monkeypatch.setattr(mb, "senders", senders)
    oid = run(brain.next_id("outreach"))
    created.append(oid)
    run(db.outreach.insert_one({**_doc(oid, None), "status": "pending_approval"}))
    with pytest.raises(ValueError) as e:
        run(brain.t_send_queued(CTX, {"outreach_filter": {"id": oid}}))
    assert str(e.value).startswith("Nothing sent: ") and f"#{oid} No usable inbox" in str(e.value)
