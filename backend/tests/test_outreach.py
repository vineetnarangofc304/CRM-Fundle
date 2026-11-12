# Outreach sequences + Email-finder regression tests (settings, approval/scheduling logic, no-mailbox safety,
# auto-stop on lead engagement, LinkedIn mark-sent, cron auth, RBAC, prospect sort by fit score).
# SAFETY: uses only TEST_ outreach docs inserted directly (seq_id >= 990000, example.com addresses) + a TEST lead.
# drafts_per_run is set to 0 while triggering runs so no LLM drafts / real sends happen; settings restored after.
import os
import time
import uuid

import pymongo
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
# v2 (auto-send) production values (daily_cap 200 since outreach_v4) — restored after the module
RESTORE = {"enabled": True, "min_score": 0, "daily_cap": 200, "drafts_per_run": 30, "gaps": [3, 7],
           "auto_send": True, "cc": "vineet.narang@fundle.ai"}


def _env(key, path="/app/backend/.env"):
    for line in open(path):
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')


DB = pymongo.MongoClient(_env("MONGO_URL"))[_env("DB_NAME")]
# Triggering a live run before the one-time v2 migration has happened would re-apply v2 settings mid-test
# (auto_send=True, drafts_per_run=30) -> real LLM drafts + auto-approval of the user's pending drafts. Skip then.
needs_migrated = pytest.mark.skipif(not DB.settings.find_one({"key": "outreach_v2"}),
                                    reason="outreach_v2 migration not yet run in this DB; live run would draft/auto-approve real data")
SEQ = 990000 + int(uuid.uuid4().int % 9000)
MID = SEQ * 10


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return s


@pytest.fixture(scope="module")
def caller(admin):
    email = f"test_caller_{uuid.uuid4().hex[:6]}@example.com"
    r = admin.post(f"{BASE_URL}/api/users", json={"name": "TEST_caller", "email": email, "password": "Test@12345", "role": "caller"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    s = requests.Session()
    lr = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "Test@12345"})
    s.headers["Authorization"] = f"Bearer {lr.json()['access_token']}"
    yield s
    admin.delete(f"{BASE_URL}/api/users/{uid}")


@pytest.fixture(scope="module")
def settings_guard(admin):
    orig = admin.get(f"{BASE_URL}/api/outreach/settings").json()
    yield orig
    body = {**{k: orig[k] for k in ("pitch", "signature")}, **RESTORE}
    r = admin.put(f"{BASE_URL}/api/outreach/settings", json=body)
    assert r.status_code == 200


@pytest.fixture(scope="module")
def test_seq(admin):
    lr = admin.post(f"{BASE_URL}/api/leads", json={"name": "TEST_outreach_lead", "phone": "9000011122", "email_from": "test_out@example.com"})
    assert lr.status_code == 200, lr.text
    lead_id = lr.json()["id"]
    base = {"seq_id": SEQ, "prospect_id": 0, "lead_id": lead_id, "brand": "TEST_Brand", "category": "TEST",
            "fit_score": 1, "contact_name": "TEST Person", "contact_role": "CMO", "to": "test_person@example.com",
            "email_status": "pattern", "linkedin": None, "status": "pending_approval", "created_at": "2026-01-01 00:00:00"}
    docs = [{**base, "id": MID + i, "channel": "email", "step": i, "subject": f"TEST subj {i}", "body": "<p>TEST</p>",
             "scheduled_at": None} for i in (1, 2, 3)]
    docs += [{**base, "id": MID + 3 + i, "channel": "linkedin", "step": i, "subject": "Connection note" if i == 1 else "Follow-up message",
              "body": "TEST li"} for i in (1, 2)]
    DB.outreach.insert_many(docs)
    yield {"lead_id": lead_id, "seq": SEQ}
    DB.outreach.delete_many({"seq_id": SEQ})
    DB.leads.delete_one({"id": lead_id, "name": "TEST_outreach_lead"})
    DB.messages.delete_many({"lead_id": lead_id}) if "messages" in DB.list_collection_names() else None


def _msg(mid):
    return DB.outreach.find_one({"id": mid}, {"_id": 0})


# ---- settings ----
def test_settings_get(admin, settings_guard):
    s = settings_guard
    for k in ("enabled", "min_score", "daily_cap", "drafts_per_run", "gaps", "pitch", "signature", "mail_connected"):
        assert k in s
    assert s["mail_connected"] is False


@pytest.mark.parametrize("gaps", [[0, 7], [3, 31], [3]])
def test_settings_bad_gaps(admin, settings_guard, gaps):
    r = admin.put(f"{BASE_URL}/api/outreach/settings", json={**RESTORE, "gaps": gaps})
    assert r.status_code == 400


def test_settings_save_persist(admin, settings_guard):
    body = {**RESTORE, "pitch": settings_guard["pitch"], "signature": settings_guard["signature"], "drafts_per_run": 0, "daily_cap": 39,
            "auto_send": False}
    r = admin.put(f"{BASE_URL}/api/outreach/settings", json=body)
    assert r.status_code == 200 and r.json()["drafts_per_run"] == 0
    g = admin.get(f"{BASE_URL}/api/outreach/settings").json()
    assert g["daily_cap"] == 39 and g["drafts_per_run"] == 0 and g["auto_send"] is False


# ---- approval logic ----
def test_approve_sequence_scheduling(admin, settings_guard, test_seq):
    r = admin.post(f"{BASE_URL}/api/outreach/sequences/{SEQ}/approve")
    assert r.status_code == 200 and r.json()["approved"] == 5
    e1, e2, e3 = _msg(MID + 1), _msg(MID + 2), _msg(MID + 3)
    assert e1["status"] == "approved" and e1["scheduled_at"]
    assert e2["status"] == "approved" and e2["scheduled_at"] is None
    assert e3["status"] == "approved" and e3["scheduled_at"] is None


@needs_migrated
def test_run_without_mailbox_sends_nothing(admin, settings_guard, test_seq):
    r = admin.post(f"{BASE_URL}/api/outreach/run")
    assert r.status_code == 200
    time.sleep(6)
    assert _msg(MID + 1)["status"] == "approved"
    assert not _msg(MID + 1).get("sent_at")
    last = admin.get(f"{BASE_URL}/api/outreach/settings").json()["last_run"]
    assert last.get("stats", {}).get("sent") == 0


def test_messages_list_filter(admin, test_seq):
    r = admin.get(f"{BASE_URL}/api/outreach/messages", params={"status": "approved", "channel": "email", "limit": 200})
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(m["status"] == "approved" and m["channel"] == "email" for m in items)
    assert any(m["id"] == MID + 1 for m in items)
    assert "_id" not in items[0]


def test_edit_message(admin, test_seq):
    r = admin.patch(f"{BASE_URL}/api/outreach/messages/{MID + 2}", json={"subject": "TEST edited"})
    assert r.status_code == 200
    assert _msg(MID + 2)["subject"] == "TEST edited"


def test_mark_sent_linkedin(admin, test_seq):
    r = admin.post(f"{BASE_URL}/api/outreach/messages/{MID + 4}/mark-sent")
    assert r.status_code == 200
    assert _msg(MID + 4)["status"] == "sent"
    assert admin.post(f"{BASE_URL}/api/outreach/messages/{MID + 1}/mark-sent").status_code == 404


def test_reject_message(admin, test_seq):
    DB.outreach.update_one({"id": MID + 5}, {"$set": {"status": "pending_approval"}})
    assert admin.post(f"{BASE_URL}/api/outreach/messages/{MID + 5}/reject").status_code == 200
    assert _msg(MID + 5)["status"] == "rejected"


@needs_migrated
def test_auto_stop_on_lead_engaged(admin, settings_guard, test_seq):
    r = admin.patch(f"{BASE_URL}/api/leads/{test_seq['lead_id']}", json={"updates": {"lead_stage": "Contacted"}})
    assert r.status_code == 200, r.text
    admin.post(f"{BASE_URL}/api/outreach/run")
    for _ in range(10):
        time.sleep(2)
        if _msg(MID + 1)["status"] == "stopped":
            break
    assert _msg(MID + 1)["status"] == "stopped"
    assert _msg(MID + 1)["stop_reason"] == "Lead engaged by team"
    assert _msg(MID + 4)["status"] == "sent"  # already-sent stays sent
    assert admin.patch(f"{BASE_URL}/api/outreach/messages/{MID + 1}", json={"subject": "x"}).status_code == 409


def test_stop_sequence_endpoint(admin, test_seq):
    DB.outreach.update_one({"id": MID + 2}, {"$set": {"status": "approved"}})
    r = admin.post(f"{BASE_URL}/api/outreach/sequences/{SEQ}/stop")
    assert r.status_code == 200 and r.json()["stopped"] >= 1
    assert _msg(MID + 2)["status"] == "stopped"


# ---- cron ----
def test_cron_auth(settings_guard):
    assert requests.post(f"{BASE_URL}/api/cron/outreach").status_code == 401
    assert requests.post(f"{BASE_URL}/api/cron/outreach", headers={"Authorization": "Bearer wrong"}).status_code == 401
    if not DB.settings.find_one({"key": "outreach_v2"}):
        pytest.skip("valid-secret cron would trigger a real run before v2 migration")
    r = requests.post(f"{BASE_URL}/api/cron/outreach", headers={"Authorization": f"Bearer {_env('WEBHOOK_CRON_SECRET')}"})
    assert r.status_code == 200 and r.json().get("queued") is True


# ---- prospects: sort + contacts ----
def test_prospect_sort_score(admin):
    r = admin.get(f"{BASE_URL}/api/prospecting/prospects", params={"sort": "score", "limit": 30})
    assert r.status_code == 200
    scores = [p.get("fit_score") or 0 for p in r.json()["items"]]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0


def test_prospect_616_contacts(admin):
    p = admin.get(f"{BASE_URL}/api/prospecting/prospects/616").json()
    assert p["brand_name"] == "Hero MotoCorp" and len(p["contacts"]) >= 1 and isinstance(p["fit_score"], int)


def test_draft_404_and_400(admin):
    assert admin.post(f"{BASE_URL}/api/prospecting/prospects/99999999/draft", json={}).status_code == 404
    assert admin.post(f"{BASE_URL}/api/prospecting/prospects/99999999/contacts").status_code == 409


# ---- RBAC ----
@pytest.mark.parametrize("method,path", [
    ("get", "/api/outreach/settings"), ("get", "/api/outreach/messages"), ("get", "/api/outreach/stats"),
    ("post", "/api/outreach/run"), ("post", "/api/outreach/sequences/1/approve"), ("post", "/api/outreach/sequences/1/stop"),
    ("post", "/api/outreach/bulk-approve"), ("patch", "/api/outreach/messages/1"), ("post", "/api/outreach/messages/1/mark-sent"),
    ("post", "/api/prospecting/prospects/616/contacts"), ("post", "/api/prospecting/prospects/616/draft"),
    ("put", "/api/outreach/settings"),
])
def test_caller_forbidden(caller, method, path):
    kw = {"json": {"seq_ids": [1]} if "bulk" in path else ({**RESTORE} if method == "put" else {})}
    assert getattr(caller, method)(f"{BASE_URL}{path}", **kw).status_code == 403


def test_unauthenticated():
    assert requests.get(f"{BASE_URL}/api/outreach/messages").status_code in (401, 403)
