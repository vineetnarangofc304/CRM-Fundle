# Iteration 4: 50,000-row chunked xlsx import (/root/big.xlsx from /root/mkbig.py) — timing <60s, per-chunk progress,
# skip/update/create duplicate-mode counts and won_date from 'Won Date' column. Run with -n 0.
import json
import os
import time

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
BIG = "/root/big.xlsx"
N, DUPS = 50000, 999  # i%50==0 rows (except i=0) reuse previous phone


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@fundle.ai", "password": "Fundle@cf928c"})
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


def big_import(s, mode):
    raw = open(BIG, "rb").read()
    t0 = time.time()
    p = s.post(f"{BASE_URL}/api/import/preview", files={"file": ("big.xlsx", raw)}, timeout=120)
    assert p.status_code == 200, p.text
    m = p.json()["mapping"]
    assert m.get("Won Date") == "_won"
    r = s.post(f"{BASE_URL}/api/import/leads", files={"file": ("big.xlsx", raw)},
               data={"mapping": json.dumps(m), "options": json.dumps({"duplicate_mode": mode})}, timeout=120)
    assert r.status_code == 200, r.text
    jid, seen = r.json()["job_id"], []
    while time.time() - t0 < 180:
        j = s.get(f"{BASE_URL}/api/import/jobs/{jid}").json()
        seen.append(j["processed"])
        if j["status"] == "done":
            break
        time.sleep(0.5)
    el = time.time() - t0
    print(f"{mode}: {el:.1f}s created={j['created']} updated={j['updated']} skipped={j['skipped']} failed={j['failed']} "
          f"progress={sorted(set(seen))[:8]}... fatal={j.get('fatal')}")
    return j, el, seen


def test_skip_mode_50k(admin):
    j, el, seen = big_import(admin, "skip")
    assert el < 60, el
    assert not j.get("fatal")
    assert (j["created"], j["skipped"], j["failed"]) == (N - DUPS, DUPS, 0), j
    assert len(set(seen)) > 3
    assert DB.leads.count_documents({"bulk_import": True}) == N - DUPS
    won = DB.leads.find_one({"contact_name": "BulkTest 10"})
    assert won["lead_stage"] == "Converted" and won["won_date_ist"].startswith("2026-10-20")
    assert won["create_date_ist"].startswith("2026-10-05")


def test_update_mode_50k(admin):
    j, el, _ = big_import(admin, "update")
    assert el < 60, el
    assert (j["created"], j["updated"]) == (0, N), j
    assert DB.leads.count_documents({"bulk_import": True}) == N - DUPS


def test_create_mode_50k(admin):
    j, el, _ = big_import(admin, "create")
    assert el < 60, el
    assert j["created"] == N, j
    assert DB.leads.count_documents({"bulk_import": True, "is_duplicate": True}) == N
    mids = DB.messages.distinct("id")
    assert len(mids) == DB.messages.count_documents({})
