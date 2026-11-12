# Fundle Brain iter26: send_single_email / send_queued_emails_now (mocked mb.send), inbox_activity, ACTION_TOOLS
# auto-reason in run_turn (fake LLM), SYSTEM prompt text. Never sends real email.
import asyncio
import json
import os
import sys
import types

import pytest

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from core.db import db  # noqa: E402
from core import brain, mailboxes as mb  # noqa: E402

run = LOOP.run_until_complete
TO = "test_iter26_contact@example.com"
FAKE_LEAD = 99999926
CTX = {"user": {"id": 1, "name": "TEST_runner"}, "session_id": 99999926}
SENT = []


def _pool():
    return [{"id": 0, "email": "TEST_main@fundle.ai", "remaining": 25, "cfg": None, "signature": "<p>TEST_SIG</p>"}]


@pytest.fixture(scope="module", autouse=True)
def setup_data():
    pid = 99999926
    run(db.prospects.insert_one({"id": pid, "brand_name": "TEST_Iter26 Brand", "category": "TEST", "country": "TEST", "brand_key": "test_iter26_brand", "lead_id": FAKE_LEAD,
                                 "contacts": [{"name": "Test Person", "role": "CEO", "email": TO}]}))
    created = []
    yield created
    run(db.prospects.delete_one({"id": pid}))
    run(db.outreach.delete_many({"id": {"$in": created}}))
    run(db.messages.delete_many({"lead_id": FAKE_LEAD}))
    run(db.brain_audit.delete_many({"session_id": CTX["session_id"]}))


@pytest.fixture
def mock_ok(monkeypatch):
    async def senders(st, day):
        return _pool()

    async def send(s, to, subject, body, cc=None, attachments=None):
        SENT.append({"to": to, "subject": subject, "body": body, "from": s["email"]})
        return {"ok": True}
    monkeypatch.setattr(mb, "senders", senders)
    monkeypatch.setattr(mb, "send", send)
    SENT.clear()


@pytest.fixture
def mock_fail(monkeypatch):
    async def senders(st, day):
        return _pool()

    async def send(s, to, subject, body, cc=None, attachments=None):
        return {"ok": False, "error": "TEST timeout"}
    monkeypatch.setattr(mb, "senders", senders)
    monkeypatch.setattr(mb, "send", send)


# send_single_email success: outreach doc kind brain, prospect/lead linked, sent, signature, lead log
def test_send_single_email_success(mock_ok, setup_data):
    r = run(brain.t_email(CTX, {"to": TO.upper(), "subject": "TEST_hello", "body_html": "<p>Hi</p>", "from_inbox": "main"}))
    assert len(r["sent"]) == 1 and not r["failed"]
    oid = r["sent"][0]["id"]
    setup_data.append(oid)
    d = run(db.outreach.find_one({"id": oid}, {"_id": 0}))
    assert d["status"] == "sent" and d["kind"] == "brain" and d["force_sender"] == 0
    assert d["prospect_id"] == 99999926 and d["lead_id"] == FAKE_LEAD and d["sent_from"] == "TEST_main@fundle.ai"
    assert SENT and "TEST_SIG" in SENT[0]["body"] and SENT[0]["to"] == TO
    msg = run(db.messages.find_one({"lead_id": FAKE_LEAD}, {"_id": 0}))
    assert msg and "TEST_hello" in msg["body"]


# send_single_email failure: clear error, doc stays approved for retry
def test_send_single_email_failure(mock_fail, setup_data):
    with pytest.raises(ValueError) as e:
        run(brain.t_email(CTX, {"to": TO, "subject": "TEST_fail", "body_html": "<p>Hi</p>", "from_inbox": "main"}))
    msg = str(e.value)
    oid = int(msg.split("outreach #")[1].rstrip(")."))
    setup_data.append(oid)
    assert "Send failed from" in msg and "TEST timeout" in msg and "retries automatically" in msg
    d = run(db.outreach.find_one({"id": oid}, {"_id": 0}))
    assert d["status"] == "approved" and d["send_attempts"] == 1
    run(db.outreach.update_one({"id": oid}, {"$set": {"status": "stopped"}}))


# Preview reality: no inbox in pool -> error message should be clear (not 'None')
def test_send_single_email_no_inbox(monkeypatch, setup_data):
    async def senders(st, day):
        return []
    monkeypatch.setattr(mb, "senders", senders)
    with pytest.raises(ValueError) as e:
        run(brain.t_email(CTX, {"to": TO, "subject": "TEST_noinbox", "body_html": "<p>Hi</p>", "from_inbox": "main"}))
    msg = str(e.value)
    oid = int(msg.split("outreach #")[1].rstrip(")."))
    setup_data.append(oid)
    run(db.outreach.update_one({"id": oid}, {"$set": {"status": "stopped"}}))
    print("NO-INBOX MSG:", msg)
    assert "None" not in msg, f"Unclear error when no inbox connected: {msg}"


# send_queued_emails_now: pending -> approved -> sent
def test_send_queued_now(mock_ok, setup_data):
    oid = run(brain.next_id("outreach"))
    setup_data.append(oid)
    run(db.outreach.insert_one({"id": oid, "seq_id": run(brain.next_id("outreach_seq")), "channel": "email", "step": 3,
                                "kind": "brain", "template_name": "TEST", "prospect_id": 99999926, "lead_id": FAKE_LEAD,
                                "brand": "TEST_Iter26 Brand", "contact_name": "Test Person", "to": TO, "subject": "TEST_queued",
                                "body": "<p>q</p>", "status": "pending_approval"}))
    r = run(brain.t_send_queued(CTX, {"outreach_filter": {"id": oid}, "from_inbox": "main"}))
    assert [x["id"] for x in r["sent"]] == [oid]
    assert run(db.outreach.find_one({"id": oid}))["status"] == "sent"
    with pytest.raises(ValueError):
        run(brain.t_send_queued(CTX, {"outreach_filter": {}}))
    assert run(brain.t_send_queued(CTX, {"outreach_filter": {"id": -5}}))["sent"] == []


# inbox_activity: read-only structure
def test_inbox_activity():
    r = run(brain.t_inbox(CTX, {"days": 7}))
    assert r["days"] == 7
    for k in ("per_inbox_status_counts", "recent_replies", "recent_send_errors", "reply_tracking"):
        assert k in r
    json.dumps(r, default=str)
    assert "inbox_activity" in brain.READ_ONLY and "reason" not in brain.TOOLS["inbox_activity"][1]["function"]["parameters"]["properties"]


def test_system_prompt_and_sets():
    assert "send_queued_emails_now if an intro is already queued" in brain.SYSTEM
    assert brain.ACTION_TOOLS == {"send_single_email", "send_email_campaign", "send_queued_emails_now", "stop_emails", "run_job"}


# ---- run_turn with fake LLM ----
class TextDelta:
    def __init__(self, c): self.content = c


class ToolCallReady:
    def __init__(self, tc): self.tool_call = tc


class StreamDone:
    pass


def _fake_chat(calls):
    state = {"i": 0}

    class Chat:
        def __init__(self, **kw): pass
        def with_model(self, *a): return self
        def with_tools(self, *a, **k): return self
        def add_tool_result(self, *a): pass

        async def stream_message(self, msg):
            if state["i"] == 0:
                for n, (name, args) in enumerate(calls):
                    yield ToolCallReady(types.SimpleNamespace(id=f"c{n}", name=name, arguments=args))
            state["i"] += 1
            yield StreamDone()
    return Chat


def _turn(monkeypatch, calls, text, captured):
    import emergentintegrations.llm.chat as ec
    monkeypatch.setattr(ec, "LlmChat", _fake_chat(calls))
    monkeypatch.setattr(ec, "TextDelta", TextDelta)
    monkeypatch.setattr(ec, "ToolCallReady", ToolCallReady)
    monkeypatch.setattr(ec, "StreamDone", StreamDone)
    for name, _ in calls:
        async def fake(ctx, a, _n=name):
            captured.append((_n, dict(a)))
            return {"ok": True}
        monkeypatch.setitem(brain.TOOLS, name, (fake, brain.TOOLS[name][1]))

    async def go():
        return [ev async for ev in brain.run_turn(CTX, [], text)]
    return run(go())


def test_run_turn_action_auto_reason(monkeypatch):
    cap = []
    text = "Send intro email to go colors contact from abhinav email"
    evs = _turn(monkeypatch, [("send_single_email", {"to": "x@y.com", "subject": "s", "body_html": "b"})], text, cap)
    done = [e for e in evs if e["type"] == "tool_done"][0]
    assert done["ok"] is True, done
    assert cap[0][1]["reason"] == f"Instruction: {text}"


def test_run_turn_stop_and_run_auto_reason(monkeypatch):
    cap = []
    evs = _turn(monkeypatch, [("stop_emails", {"outreach_filter": {"brand": "X"}}), ("run_job", {"job": "outreach"})],
                "stop emails to X and run the outreach now", cap)
    assert all(e["ok"] for e in evs if e["type"] == "tool_done")
    assert all(c[1]["reason"].startswith("Instruction: ") for c in cap)


def test_run_turn_settings_still_need_reason(monkeypatch):
    cap = []
    evs = _turn(monkeypatch, [("save_settings", {"section": "outreach", "changes": {"hourly_cap": 25}})],
                "set hourly cap to 25", cap)
    done = [e for e in evs if e["type"] == "tool_done"][0]
    assert done["ok"] is False and "WHY" in done["summary"] and not cap


# Bypass check: model-invented 'Instruction: ...' reason on a settings change must NOT be accepted
def test_run_turn_settings_instruction_prefix_bypass(monkeypatch):
    cap = []
    evs = _turn(monkeypatch, [("save_settings", {"section": "outreach", "changes": {"hourly_cap": 25},
                                                  "reason": "Instruction: set hourly cap"})], "set hourly cap to 25", cap)
    done = [e for e in evs if e["type"] == "tool_done"][0]
    assert done["ok"] is False, "Model can bypass the WHY check by prefixing reason with 'Instruction: '"
