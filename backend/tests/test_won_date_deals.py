# Iteration 4: Won Date tracking (import column, stamp_won via PATCH/bulk/disposition), won-value reports,
# leads list Company/Deal Value sort + min_deal_value filter (+export), chunked import duplicate modes.
import io
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from openpyxl import load_workbook
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "admin@fundle.ai", "password": "Fundle@cf928c"}
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(IST)
THIS_MONTH = NOW.strftime("%Y-%m")
PREV = (NOW.replace(day=1) - timedelta(days=1))
PREV_MONTH = PREV.strftime("%Y-%m")
S = {}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(**ADMIN)


@pytest.fixture(scope="module")
def caller(admin):
    em = f"test_caller_won_{uuid.uuid4().hex[:6]}@x.in"
    r = admin.post(f"{BASE_URL}/api/users", json={"name": "TEST Caller Won", "email": em, "password": "Test@12345", "role": "caller"})
    assert r.status_code == 200, r.text
    S["caller_id"] = r.json()["id"]
    yield _login(em, "Test@12345")
    DB.users.delete_many({"id": S["caller_id"]})
    DB.call_events.delete_many({"id": {"$gte": 900000}})


def run_import(s, lines, mapping, opts, timeout=120):
    content = ("\n".join(lines) + "\n").encode()
    r = s.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.csv", content)},
               data={"mapping": json.dumps(mapping), "options": json.dumps(opts)})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = s.get(f"{BASE_URL}/api/import/jobs/{jid}").json()
        if j["status"] == "done":
            return j
        time.sleep(0.5)
    raise AssertionError("import did not finish")


def lead_by_phone(phone):
    return list(DB.leads.find({"phone_digits": phone[-10:]}, {"_id": 0}).sort("id", 1))


MAP = {"Name": "contact_name", "Phone": "phone", "Company": "company_name", "Deal Value": "deal_value",
       "Tags": "_tags", "Created Date": "_created", "Won Date": "_won"}


# --- Won Date import column + alias auto-mapping
def test_preview_maps_won_aliases(admin):
    for h in ["Won Date", "won_date", "Closed Date", "close_date"]:
        r = admin.post(f"{BASE_URL}/api/import/preview", files={"file": ("t.csv", f"Phone,{h}\n9811100001,2026-10-01\n".encode())})
        assert r.status_code == 200
        assert r.json()["mapping"].get(h) == "_won", (h, r.json()["mapping"])


def test_import_won_dates(admin, caller):
    cm = f"{PREV_MONTH}-05"
    job = run_import(admin, [
        "Name,Phone,Company,Deal Value,Tags,Created Date,Won Date",
        f"TEST W1,9811100011,Zeta Co,500000,Won,{cm},{PREV_MONTH}-20",   # won in prev month
        f"TEST W2,9811100012,Alpha Co,70000,Won,{cm},",                  # won = created date
        "TEST W3,9811100013,Mid Co,120000,Won,,",                         # won = now
        f"TEST W4,9811100014,Beta Co,300000,Ringing,{cm},{PREV_MONTH}-21",  # not won -> no won_date
        f"TEST W5,9811100015,Gamma Co,90000,,{cm},",                       # plain, to convert later
        f"TEST W6,9811100016,Delta Co,40000,,{cm},",                       # bulk convert later
        f"TEST W7,9811100017,Eps Co,25000,,{cm},",                         # disposition later
        "TEST W8,9811100018,Bad Co,1000,Won,,31-31-2026",                  # bad won date
    ], MAP, {"duplicate_mode": "skip", "assign_mode": "user", "user_id": S["caller_id"]})
    assert job["created"] == 7 and job["failed"] == 1, job
    assert any("Won date" in e["error"] for e in job["errors"]), job["errors"]
    w1, w2, w3, w4 = (lead_by_phone(p)[0] for p in ["9811100011", "9811100012", "9811100013", "9811100014"])
    assert w1["lead_stage"] == "Converted" and w1["won_date_ist"].startswith(f"{PREV_MONTH}-20"), w1.get("won_date_ist")
    assert w2["won_date_ist"].startswith(cm), w2.get("won_date_ist")
    assert w3["won_date_ist"].startswith(NOW.strftime("%Y-%m-%d")), w3.get("won_date_ist")
    assert "won_date" not in w4
    for p in ["9811100015", "9811100016", "9811100017"]:
        S[p] = lead_by_phone(p)[0]["id"]


# --- stamp_won via PATCH, move away/back keeps first won date
def test_patch_converted_stamps_once(admin):
    lid = S["9811100015"]
    r = admin.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"lead_stage": "Converted"}})
    assert r.status_code == 200, r.text
    d = admin.get(f"{BASE_URL}/api/leads/{lid}").json()
    assert d["lead_stage"] == "Converted" and d.get("won_date_ist", "").startswith(NOW.strftime("%Y-%m-%d")), d.get("won_date_ist")
    first = d["won_date"]
    DB.leads.update_one({"id": lid}, {"$set": {"won_date": first, "won_date_ist": d["won_date_ist"]}})
    admin.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"lead_stage": "Contacted"}})
    time.sleep(1.2)
    admin.patch(f"{BASE_URL}/api/leads/{lid}", json={"updates": {"lead_stage": "Converted"}})
    d2 = admin.get(f"{BASE_URL}/api/leads/{lid}").json()
    assert d2["won_date"] == first


def test_bulk_set_lead_stage_stamps(admin):
    lid = S["9811100016"]
    r = admin.post(f"{BASE_URL}/api/leads/bulk", json={"ids": [lid], "action": "set_lead_stage", "payload": {"lead_stage": "Converted"}})
    assert r.status_code == 200, r.text
    d = DB.leads.find_one({"id": lid})
    assert d["lead_stage"] == "Converted" and d.get("won_date_ist", "").startswith(NOW.strftime("%Y-%m-%d"))


def test_disposition_converted_stamps(admin, caller):
    lid = S["9811100017"]
    DB.call_events.insert_one({"id": 900001, "lead_id": lid, "direction": "outbound", "status": "completed",
                               "user_id": S["caller_id"], "created_at": "2026-01-01 00:00:00"})
    r = caller.post(f"{BASE_URL}/api/calls/900001/disposition", json={"disposition": "Converted"})
    assert r.status_code == 200, r.text
    d = DB.leads.find_one({"id": lid})
    assert d["lead_stage"] == "Converted" and d.get("won_date_ist", "").startswith(NOW.strftime("%Y-%m-%d")), d.get("won_date_ist")


# --- Won value reports use won date (caller-scoped => fresh cache key)
def test_kpi_overview_won_mtd(admin):
    r = admin.get(f"{BASE_URL}/api/reports/kpi-overview", params={"month": THIS_MONTH})
    assert r.status_code == 200, r.text
    deals = r.json()["deals"]
    # this month: W3 120000 (import won now) + W5 90000 + W6 40000 + W7 25000; W1/W2 won prev month
    assert deals["won_mtd"] == 275000, deals
    r = admin.get(f"{BASE_URL}/api/reports/kpi-overview", params={"month": PREV_MONTH})
    assert r.json()["deals"]["won_mtd"] == 570000, r.json()["deals"]


def test_dashboard_won_value_mtd(caller):
    r = caller.get(f"{BASE_URL}/api/reports/dashboard", params={"section": "kpis"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["won_value_mtd"] == 275000, d
    assert d["pipeline_value"] == 300000, d  # W4 only open lead with value


# --- Leads list sort + min_deal_value filter + export
def _items(r):
    d = r.json()
    return d.get("items") or d.get("leads") or d.get("records") or []


def test_sort_deal_value_and_company(admin):
    r = admin.get(f"{BASE_URL}/api/leads", params={"sort": "deal_value", "order": "desc", "search": "TEST W", "limit": 50})
    assert r.status_code == 200
    vals = [l.get("deal_value") or 0 for l in _items(r)]
    assert vals and vals == sorted(vals, reverse=True), vals
    assert "company_name" in _items(r)[0] and "deal_value" in _items(r)[0]
    r = admin.get(f"{BASE_URL}/api/leads", params={"sort": "company_name", "order": "asc", "search": "TEST W", "limit": 50})
    names = [l.get("company_name") for l in _items(r)]
    assert names == sorted(names), names


def test_min_deal_value_filter(admin):
    r = admin.get(f"{BASE_URL}/api/leads", params={"min_deal_value": "1,00,000", "search": "TEST W", "limit": 50})
    vals = sorted(l["deal_value"] for l in _items(r))
    assert vals == [120000, 300000, 500000], vals
    r = admin.get(f"{BASE_URL}/api/leads", params={"min_deal_value": "abc", "search": "TEST W", "limit": 50})
    assert r.status_code == 200 and len(_items(r)) == 7


def test_export_won_date_and_min_filter(admin):
    r = admin.get(f"{BASE_URL}/api/export/leads.xlsx", params={"min_deal_value": "100000", "search": "TEST W"})
    assert r.status_code == 200, r.text[:300]
    ws = load_workbook(io.BytesIO(r.content)).active
    hdr = [c.value for c in ws[1]]
    assert "Won Date" in hdr
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 3, len(rows)
    wi = hdr.index("Won Date")
    assert any(str(x[wi] or "").startswith(f"{PREV_MONTH}-20") for x in rows)


# --- Chunked import duplicate modes (re-upload same small file)
DUP_FILE = ["Name,Phone,Company,Tags", "TEST D1,9822200001,C1,", "TEST D2,9822200002,C2,", "TEST D1b,9822200001,C1b,Won"]
DMAP = {"Name": "contact_name", "Phone": "phone", "Company": "company_name", "Tags": "_tags"}


def test_dup_modes(admin):
    j = run_import(admin, DUP_FILE, DMAP, {"duplicate_mode": "skip"})
    assert (j["created"], j["skipped"]) == (2, 1), j
    j = run_import(admin, DUP_FILE, DMAP, {"duplicate_mode": "skip"})
    assert (j["created"], j["skipped"]) == (0, 3), j
    j = run_import(admin, DUP_FILE, DMAP, {"duplicate_mode": "update"})
    assert (j["created"], j["updated"]) == (0, 3), j
    l1 = lead_by_phone("9822200001")
    assert len(l1) == 1 and l1[0]["company_name"] == "C1b" and l1[0]["lead_stage"] == "Converted"
    assert l1[0].get("won_date_ist"), "update-mode Won row did not stamp won_date"
    j = run_import(admin, DUP_FILE, DMAP, {"duplicate_mode": "create"})
    assert j["created"] == 3, j
    l1 = lead_by_phone("9822200001")
    assert len(l1) == 3 and all(x["is_duplicate"] and x["duplicate_of"] == l1[0]["id"] for x in l1[1:])


def test_in_file_dup_update_mode_new_leads(admin):
    # both rows new; second (Won) row merges into first in-chunk doc
    j = run_import(admin, ["Name,Phone,Tags", "TEST U1,9833300001,", "TEST U1b,9833300001,Won"],
                   {"Name": "contact_name", "Phone": "phone", "Tags": "_tags"}, {"duplicate_mode": "update"})
    assert (j["created"], j["updated"]) == (1, 1), j
    l = lead_by_phone("9833300001")
    assert len(l) == 1 and l[0]["contact_name"] == "TEST U1b"
    assert l[0].get("lead_stage") == "Converted"
    assert l[0].get("won_date_ist"), "in-file update merged Won stage but no won_date set"
    assert l[0].get("name") == "TEST U1b", f"name not refreshed: {l[0].get('name')}"


def test_messages_unique(admin):
    ids = DB.leads.distinct("id", {"bulk_import": True})
    msgs = list(DB.messages.find({"lead_id": {"$in": ids}}, {"_id": 0, "id": 1, "body": 1, "lead_id": 1}))
    mids = [m["id"] for m in msgs]
    assert len(mids) == len(set(mids))
    created = {m["lead_id"] for m in msgs if "Lead created from bulk upload by" in m["body"]}
    assert created == set(ids)
