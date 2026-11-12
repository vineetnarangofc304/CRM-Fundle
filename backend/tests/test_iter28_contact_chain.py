# iter28: contacts.find_contacts multi-model chain (mock pe._ask), _ask fallback to Claude, _call provider tools
# (mock LlmChat). One optional real call guarded by RUN_REAL_LLM=1.
import asyncio
import os
import sys
import time

import pytest

# share ONE loop across test modules (Motor binds to the loop of first use; per-module loops -> ValueError)
LOOP = asyncio.get_event_loop_policy().get_event_loop()
if LOOP.is_closed():
    LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from core.db import db  # noqa: E402
from core import prospector as pe, contacts as ct, brain_data as bd  # noqa: E402

run = LOOP.run_until_complete
RL = "litellm.RateLimitError: GeminiException - TEST Resource has been exhausted (429)"
PID = 99999928


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


@pytest.fixture
def prospect(monkeypatch):
    p = {"id": PID, "brand_name": "TEST_Brand28", "category": "Footwear", "country": "India",
         "website": "https://example.com", "status": "enriched", "contacts_status": "running"}
    run(db.prospects.delete_one({"id": PID}))
    run(db.prospects.insert_one(dict(p)))
    logs = []

    async def fake_log_run(*a, **k):
        logs.append(a)

    async def fake_upsert(pp, user=None):
        return 999999280

    async def nolog(*a, **k):
        return None

    async def mx(d):
        return True
    monkeypatch.setattr(pe, "_log_run", fake_log_run)
    monkeypatch.setattr(pe, "upsert_lead", fake_upsert)
    monkeypatch.setattr(ct, "log_message", nolog)
    monkeypatch.setattr(ct, "_mx_ok", mx)
    yield p, logs
    run(db.prospects.delete_one({"id": PID}))


def _mock_ask(monkeypatch, answers):
    calls = []

    async def fake(model, system, prompt, meta=None):
        calls.append(model)
        a = answers[len(calls) - 1]
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(pe, "_ask", fake)
    return calls


def _doc():
    return run(db.prospects.find_one({"id": PID}, {"_id": 0}))


C_EMAIL = '{"email_pattern": "first.last@example.com", "contacts": [{"name": "Asha Rao", "role": "CMO", "email": "asha.rao@example.com", "email_status": "published"}]}'
C_NOEMAIL = '{"email_pattern": null, "contacts": [{"name": "Asha Rao", "role": "CMO", "email": null}, {"name": "Ravi K", "role": "CEO"}]}'
C_NOEMAIL2 = '{"email_pattern": "' + "x" * 400 + '", "contacts": [{"name": " asha rao ", "role": "Head CRM"}, {"name": "Neha S", "role": "CRM"}]}'


# stop after first model that returns a contact with email
def test_stops_on_first_email(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [C_EMAIL, C_EMAIL, C_EMAIL])
    assert run(ct.find_contacts(prospect[0])) == {"ok": True}
    assert calls == [ct.CONTACT_CHAIN[0]]
    d = _doc()
    assert d["contacts_models"] == [ct.CONTACT_CHAIN[0]]
    assert d["contacts_status"] == "done" and d["contacts"][0]["email"] == "asha.rao@example.com"
    assert d["email_pattern"] == "first.last@example.com"


# no emails -> next model; merge without duplicate names; second finds email -> stop at 2
def test_merges_and_continues(monkeypatch, prospect):
    new = C_EMAIL.replace("Asha Rao", "Meera J").replace("asha.rao", "meera.j")
    calls = _mock_ask(monkeypatch, [C_NOEMAIL, new, C_EMAIL])
    run(ct.find_contacts(prospect[0]))
    assert calls == list(ct.CONTACT_CHAIN[:2])
    d = _doc()
    assert [c["name"] for c in d["contacts"]] == ["Asha Rao", "Ravi K", "Meera J"]
    assert d["contacts"][2]["email"] == "meera.j@example.com"
    assert d["contacts_models"] == list(ct.CONTACT_CHAIN[:2])


# FIX1: model 2 returns the SAME person with an email -> email/status filled into existing contact, stop at 2
def test_duplicate_name_email_merged(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [C_NOEMAIL, C_EMAIL, C_NOEMAIL])
    run(ct.find_contacts(prospect[0]))
    d = _doc()
    assert d["contacts"][0]["email"] == "asha.rao@example.com" and calls == list(ct.CONTACT_CHAIN[:2])
    assert d["contacts"][0]["email_status"] == "published" and d["contacts"][0]["role"] == "CMO"
    assert len(d["contacts"]) == 2


# never emails -> max 3 models; case/whitespace dedupe; email_pattern truncated to 300
def test_max_three_models(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [C_NOEMAIL, C_NOEMAIL2, C_NOEMAIL2, C_NOEMAIL2])
    run(ct.find_contacts(prospect[0]))
    assert calls == list(ct.CONTACT_CHAIN[:3])
    d = _doc()
    assert [c["name"] for c in d["contacts"]] == ["Asha Rao", "Ravi K", "Neha S"]
    assert len(d["email_pattern"]) == 300
    assert d["contacts_models"] == list(ct.CONTACT_CHAIN[:3])


# AiCreditsOut on FIRST model (nothing found) -> contacts_status unset (re-queued), raises
def test_credits_out_requeues(monkeypatch, prospect):
    _mock_ask(monkeypatch, [pe.AiCreditsOut("TEST out")])
    with pytest.raises(pe.AiCreditsOut):
        run(ct.find_contacts(prospect[0]))
    d = _doc()
    assert "contacts_status" not in d and "contacts_models" not in d


# AiCreditsOut AFTER earlier model found contacts -> partial results saved, ok True, no raise
def test_credits_out_keeps_partial(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [C_NOEMAIL, pe.AiCreditsOut("TEST out"), C_EMAIL])
    assert run(ct.find_contacts(prospect[0])) == {"ok": True}
    assert calls == list(ct.CONTACT_CHAIN[:2])
    d = _doc()
    assert d["contacts_status"] == "done" and [c["name"] for c in d["contacts"]] == ["Asha Rao", "Ravi K"]
    assert d["contacts_models"] == [ct.CONTACT_CHAIN[0]]


# non-quota error on 1st model -> recorded, loop continues to next model
def test_non_quota_error_continues(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [RuntimeError("TEST boom"), C_EMAIL])
    assert run(ct.find_contacts(prospect[0])) == {"ok": True}
    assert calls == list(ct.CONTACT_CHAIN[:2])
    d = _doc()
    assert d["contacts_status"] == "done" and d["contacts"][0]["email"] == "asha.rao@example.com"
    assert d["contacts_models"] == [ct.CONTACT_CHAIN[1]]


# every model errors -> failed with 'All models failed'
def test_all_models_error_fails(monkeypatch, prospect):
    calls = _mock_ask(monkeypatch, [RuntimeError(f"TEST boom{i}") for i in range(len(ct.CONTACT_CHAIN))])
    assert run(ct.find_contacts(prospect[0])) == {"ok": False}
    assert calls == list(ct.CONTACT_CHAIN)
    d = _doc()
    assert d["contacts_status"] == "failed" and d["contacts_error"].startswith("All models failed:")
    assert "TEST boom0" in d["contacts_error"]


# FIX4: meta['model'] = model that actually answered -> stored; same actual model not counted twice
def test_actual_model_recorded(monkeypatch, prospect):
    calls = []
    actual = {"gemini-3.1-pro-preview": "gemini-2.5-flash"}  # pro falls back to 2.5-flash

    async def fake(model, system, prompt, meta=None):
        calls.append(model)
        if meta is not None:
            meta["model"] = actual.get(model, model)
        return C_NOEMAIL
    monkeypatch.setattr(pe, "_ask", fake)
    assert run(ct.find_contacts(prospect[0])) == {"ok": True}
    d = _doc()
    print("calls:", calls, "models:", d["contacts_models"])
    assert d["contacts_models"] == [pe.CLAUDE, "gemini-2.5-flash", pe.CLAUDE_TOP]
    assert calls == [pe.CLAUDE, "gemini-3.1-pro-preview", pe.CLAUDE_TOP]  # 2.5-flash not asked twice


# FIX4: _ask writes the answering model into meta
def test_ask_meta_model(monkeypatch):
    pe._limited.clear()

    async def fake(model, system, prompt):
        if model == pe.PRO:
            raise Exception(RL)
        return "ok"
    monkeypatch.setattr(pe, "_call", fake)
    meta = {}
    assert run(pe._ask(pe.PRO, "s", "p", meta)) == "ok"
    assert meta["model"] == pe.FLASH
    assert run(pe._ask(pe.FLASH, "s", "p")) == "ok"  # meta optional
    pe._limited.clear()


# FIX5: prompt asks email_pattern as a short format
def test_prompt_short_pattern(monkeypatch, prospect):
    seen = []

    async def fake(model, system, prompt, meta=None):
        seen.append(prompt)
        return C_EMAIL
    monkeypatch.setattr(pe, "_ask", fake)
    run(ct.find_contacts(prospect[0]))
    assert "short format only, e.g. firstname.lastname@domain.com" in seen[0]


# _ask: gemini models rate-limited -> Claude spec succeeds, gemini models recorded in _limited
def test_ask_falls_to_claude(monkeypatch):
    pe._limited.clear()
    calls = []

    async def fake(model, system, prompt):
        calls.append(model)
        if not model.startswith("anthropic:"):
            raise Exception(RL)
        return "claude-ok"
    monkeypatch.setattr(pe, "_call", fake)
    assert run(pe._ask(pe.FLASH, "s", "p")) == "claude-ok"
    assert calls == [pe.FLASH, "gemini-3-flash-preview", "gemini-2.5-flash", pe.CLAUDE]
    for m in (pe.FLASH, "gemini-3-flash-preview", "gemini-2.5-flash"):
        assert pe._limited[m] > time.time() + 1700
    assert pe.CLAUDE not in pe._limited
    calls.clear()
    assert run(pe._ask(pe.PRO, "s", "p")) == "claude-ok"
    assert calls == [pe.PRO, pe.CLAUDE]  # limited gemini models skipped
    pe._limited.clear()


# _call builds provider-specific search tools
class FakeChat:
    inst = []

    def __init__(self, api_key, session_id, system_message):
        self.model = self.tools = self.params = None
        self.sent = None
        FakeChat.inst.append(self)

    def with_model(self, prov, name):
        self.model = (prov, name)
        return self

    def with_tools(self, tools):
        self.tools = tools
        return self

    def with_params(self, **kw):
        self.params = kw
        return self

    async def send_message_with_tools(self, msg):
        self.sent = "tools"
        return "r"

    async def send_message(self, msg):
        self.sent = "plain"
        return "r"


@pytest.mark.parametrize("spec,prov,name,tool", [
    ("gemini-2.5-flash", "gemini", "gemini-2.5-flash", {"googleSearch": {}}),
    ("anthropic:claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5",
     {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}),
])
def test_call_builds_tools(monkeypatch, spec, prov, name, tool):
    FakeChat.inst.clear()
    monkeypatch.setattr(pe, "LlmChat", FakeChat)
    assert run(pe._call(spec, "s", "p")) == "r"
    c = FakeChat.inst[0]
    assert c.model == (prov, name) and c.tools == [tool] and c.sent == "tools"


# one real call (costs credits) — only when RUN_REAL_LLM=1
@pytest.mark.skipif(os.environ.get("RUN_REAL_LLM") != "1", reason="real LLM call disabled")
def test_real_find_contacts():
    p = run(db.prospects.find_one({"status": "enriched", "contacts_status": {"$exists": False}}, {"_id": 0},
                                  sort=[("id", 1)]))
    if not p:
        pytest.skip("no enriched prospect without contacts")
    t = time.time()
    res = run(ct.find_contacts(p))
    d = run(db.prospects.find_one({"id": p["id"]}, {"_id": 0}))
    print("REAL:", p["brand_name"], res, round(time.time() - t), "s", d.get("contacts_models"),
          d.get("contacts_status"), d.get("contacts_error"), [(c["name"], c["role"], c.get("email")) for c in d.get("contacts") or []])
    assert res["ok"] and d["contacts_models"] and d["contacts_status"] == "done"
