# Fundle Brain (iter23): retest of fixes — secret scrubbing (_enc), aggregate group keys, main-inbox pin,
# empty-filter campaign error, contact-search skip for brands with emails, escalation note. No LLM calls.
import os
import sys
import asyncio
import pytest
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

CTX = {"user": {"id": "test", "name": "TEST_brain"}, "session_id": -1}


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# Secrets
class TestSecrets:
    def test_enc_and_substring_keys_scrubbed(self):
        from core import brain
        out = brain._scrub({"smtp_password_enc": "abc", "client_secret_x": "s", "my_token_v": "t", "name": "ok",
                            "nested": [{"refresh_token_enc": "r", "email": "a@b"}]})
        assert out["smtp_password_enc"] == "***" and out["client_secret_x"] == "***" and out["my_token_v"] == "***"
        assert out["name"] == "ok" and out["nested"][0]["refresh_token_enc"] == "***" and out["nested"][0]["email"] == "a@b"

    def test_update_refuses_enc_fields(self):
        from core import brain
        for s in ({"smtp_password_enc": "x"}, {"cfg.api_key_enc": "x"}, {"oauth_token": "x"}):
            with pytest.raises(ValueError):
                run(brain.t_update(CTX, {"collection": "mailboxes", "filter": {"id": 99999999}, "set": s}))
        with pytest.raises(ValueError):
            run(brain.t_update(CTX, {"collection": "mailboxes", "filter": {"id": 99999999}, "unset": ["smtp_password_enc"]}))

    def test_find_mailboxes_no_enc_leak(self):
        from core import brain
        r = run(brain.t_find(CTX, {"collection": "mailboxes", "filter": {}, "limit": 50}))
        for row in r["rows"]:
            for k, v in row.items():
                if k.endswith("_enc") or "password" in k:
                    assert v == "***"


# Aggregate keeps group keys
class TestAggregate:
    def test_group_keys_kept(self):
        from core import brain
        r = run(brain.t_aggregate(CTX, {"collection": "prospects", "pipeline": [
            {"$group": {"_id": "$status", "n": {"$sum": 1}}}]}))
        assert r["rows"] and all("_id" in x for x in r["rows"])
        assert any(isinstance(x["_id"], str) for x in r["rows"])

    def test_objectid_dropped(self):
        from core import brain
        r = run(brain.t_aggregate(CTX, {"collection": "prospects", "pipeline": [{"$limit": 1}]}))
        assert r["rows"] and "_id" not in r["rows"][0]


# Main inbox pin + empty filter
class TestSend:
    def test_sender_id_main(self, monkeypatch):
        from core import brain, mailer

        async def yes():
            return True
        monkeypatch.setattr(mailer, "is_connected", yes)  # iter27: 'main' now requires a connected main inbox
        for v in ("main", "0", "Main inbox", 0):
            assert run(brain._sender_id(v)) == 0
        assert run(brain._sender_id(None)) is None

    def test_sender_id_unknown_raises(self):
        from core import brain
        with pytest.raises(ValueError):
            run(brain._sender_id("nobody_TEST@example.com"))

    def test_empty_match_raises(self):
        from core import brain
        with pytest.raises(ValueError):
            run(brain.t_send(CTX, {"prospect_filter": {"brand_name": "TEST_no_such_brand_zz"}, "subject": "x", "body_html": "<p>x</p>"}))

    def test_overview_has_main_inbox(self):
        from core import brain
        o = run(brain.t_overview(CTX, {}))
        assert o["main_inbox"]["id"] == 0 and "how_to_pin" in o["main_inbox"]


# Contact search skip
class TestContacts:
    def test_skip_brands_with_emails(self):
        from core import brain
        from core.db import db
        ids = [990000001, 990000002]
        run(db.prospects.insert_many([
            {"id": ids[0], "brand_name": "TEST_brain_has_email", "country": "TEST_zz", "brand_key": "test_brain_has_email", "category": "TEST_cat_zz", "contacts": [{"name": "a", "email": "a@test.zz"}], "contacts_status": "done"},
            {"id": ids[1], "brand_name": "TEST_brain_no_email", "country": "TEST_zz", "brand_key": "test_brain_no_email", "category": "TEST_cat_zz", "contacts_status": "done"}]))
        try:
            r = run(brain.t_contacts(CTX, {"prospect_filter": {"category": "TEST_cat_zz"}, "priority": 3}))
            assert r["queued_for_contact_search"] == 1
            a = run(db.prospects.find_one({"id": ids[0]}))
            b = run(db.prospects.find_one({"id": ids[1]}))
            assert a.get("contacts_status") == "done" and not a.get("contacts_priority")
            assert "contacts_status" not in b and b["contacts_priority"] == 3
            r = run(brain.t_contacts(CTX, {"prospect_filter": {"category": "TEST_cat_zz"}, "include_brands_with_emails": True}))
            assert r["queued_for_contact_search"] >= 1
            assert "contacts_status" not in run(db.prospects.find_one({"id": ids[0]}))
        finally:
            run(db.prospects.delete_many({"id": {"$in": ids}}))


# Escalation note
class TestEscalate:
    def test_logged_note_when_email_fails(self):
        from core import brain
        from core.db import db
        r = run(brain.t_escalate(CTX, {"summary": "TEST_brain escalation", "details": "test", "reason": "testing iter23"}))
        try:
            assert r["logged"] is True and isinstance(r["request_id"], int)
            if not r.get("emailed"):
                assert "saved" in r["note"] and str(r["request_id"]) in r["note"]
        finally:
            run(db.brain_requests.delete_one({"id": r["request_id"], "summary": "TEST_brain escalation"}))
