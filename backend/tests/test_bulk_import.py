# Iteration 3: Bulk lead upload (/api/import/*) — template, preview/mapping, row rules, duplicates, assignment, RBAC, large file
import io
import json
import os
import time
import uuid
import pytest
import requests
from openpyxl import Workbook, load_workbook

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "admin@fundle.ai", "password": "Fundle@cf928c"}
STATE = {}


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
    em = f"test_caller_{uuid.uuid4().hex[:6]}@x.in"
    r = admin.post(f"{BASE_URL}/api/users", json={"name": "TEST Caller Imp", "email": em, "password": "Test@12345", "role": "caller"})
    assert r.status_code == 200, r.text
    STATE["caller_id"] = r.json()["id"]
    STATE["caller_email"] = em
    with open("/tmp/imp_test_users.txt", "a") as f:
        f.write(f"{r.json()['id']}\n")
    return _login(em, "Test@12345")


def csv_bytes(lines):
    return ("\n".join(lines) + "\n").encode()


def preview(s, content, name="t.csv"):
    return s.post(f"{BASE_URL}/api/import/preview", files={"file": (name, content)})


def run_import(s, content, mapping, opts, name="t.csv", timeout=120):
    r = s.post(f"{BASE_URL}/api/import/leads", files={"file": (name, content)},
               data={"mapping": json.dumps(mapping), "options": json.dumps(opts)})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    seen = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = s.get(f"{BASE_URL}/api/import/jobs/{jid}").json()
        seen.append(j["processed"])
        if j["status"] == "done":
            j["_seen"] = seen
            return j
        time.sleep(0.7)
    raise AssertionError(f"job {jid} did not finish; last={j}")


def find_lead(s, phone):
    r = s.get(f"{BASE_URL}/api/leads", params={"search": phone, "limit": 50})
    assert r.status_code == 200, r.text
    d = r.json()
    items = d.get("items") or d.get("leads") or d.get("records") or (d if isinstance(d, list) else [])
    return [l for l in items if (l.get("phone") or "").endswith(phone[-10:])]


# --- Template
def test_template_download(admin):
    r = admin.get(f"{BASE_URL}/api/import/template.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    ws = load_workbook(io.BytesIO(r.content)).active
    headers = [c.value for c in ws[1]]
    for h in ["Name", "Phone", "Email", "Company", "Designation", "Team Size", "Deal Value (INR)", "City", "State",
              "Tags", "Assigned Agent", "Follow-up Date", "Created Date"]:
        assert h in headers, f"{h} missing from {headers}"
    assert ws.cell(row=2, column=headers.index("Phone") + 1).value


def test_template_includes_custom_field(admin):
    r = admin.post(f"{BASE_URL}/api/catalogs/custom-fields/create",
                   json={"label": "TEST Imp Budget", "field_type": "char", "section": "general", "options": [], "aliases": []})
    assert r.status_code == 200, r.text
    STATE["cf"] = r.json()
    with open("/tmp/imp_test_cf.txt", "w") as f:
        f.write(str(r.json()["id"]))
    ws = load_workbook(io.BytesIO(admin.get(f"{BASE_URL}/api/import/template.xlsx").content)).active
    assert "TEST Imp Budget" in [c.value for c in ws[1]]


# --- Preview + auto mapping
def test_preview_csv_auto_mapping(admin):
    content = csv_bytes(["Full Name,Mobile,Email,Company,Job Title,Deal Amount,Random,TEST Imp Budget",
                         "A,9899900001,a@x.in,Acme,CTO,100,foo,5L"])
    r = preview(admin, content)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_rows"] == 1 and len(d["rows"]) == 1
    m = d["mapping"]
    assert m["Full Name"] == "contact_name" and m["Mobile"] == "phone" and m["Email"] == "email_from"
    assert m["Job Title"] == "designation" and m["Deal Amount"] == "deal_value" and m["Company"] == "company_name"
    assert "Random" not in m
    assert m.get("TEST Imp Budget") == STATE["cf"]["key"]
    assert any(f["group"] == "Custom" for f in d["fields"])


def test_preview_xlsx(admin):
    wb = Workbook(); ws = wb.active
    ws.append(["Name", "Phone", "Created Date"])
    for i in range(7):
        ws.append([f"X{i}", 9899900100 + i, "2025-01-01"])
    buf = io.BytesIO(); wb.save(buf)
    d = preview(admin, buf.getvalue(), "t.xlsx").json()
    assert d["total_rows"] == 7 and len(d["rows"]) == 5
    assert d["rows"][0][1] == "9899900100"  # numeric phone not as float
    assert d["mapping"]["Created Date"] == "_created"


# --- Validation
def test_reject_non_csv(admin):
    r = preview(admin, b"hello", "t.txt")
    assert r.status_code == 400
    r = admin.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.txt", b"a,b")}, data={"mapping": "{}"})
    assert r.status_code == 400


def test_mapping_without_phone_email(admin):
    content = csv_bytes(["Name,Mobile", "A,9899900001"])
    r = admin.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.csv", content)},
                   data={"mapping": json.dumps({"Name": "contact_name"}), "options": "{}"})
    assert r.status_code == 400 and "Phone or Email" in r.json()["detail"]


def test_duplicate_field_mapping_rejected(admin):
    content = csv_bytes(["A,B", "9899900001,9899900002"])
    r = admin.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.csv", content)},
                   data={"mapping": json.dumps({"A": "phone", "B": "phone"}), "options": "{}"})
    assert r.status_code == 400


# --- RBAC
def test_caller_forbidden(caller):
    assert caller.get(f"{BASE_URL}/api/import/template.xlsx").status_code == 403
    assert preview(caller, csv_bytes(["Phone", "9899900001"])).status_code == 403
    r = caller.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.csv", b"Phone\n9899900001\n")},
                    data={"mapping": json.dumps({"Phone": "phone"}), "options": "{}"})
    assert r.status_code == 403
    assert caller.get(f"{BASE_URL}/api/import/jobs").status_code == 403


# --- Row rules (skip mode)
def test_import_row_rules_skip_mode(admin):
    content = csv_bytes([
        "Full Name,Mobile,Email,Company,Job Title,Deal Amount,City,Tags,Next Follow Up,Random,TEST Imp Budget",
        'TEST Imp One,9899911001,one@x.in,Acme,CTO,"1,50,000",Pune,Ringing,2026-12-01,foo,5L',
        "TEST Imp Two,9899911002,two@x.in,Beta,CEO,abc,Delhi,,,,",
        "TEST Imp Three,9899911001,,Gamma,,5000,Mumbai,Demo Booked,,,",
        "TEST Imp Four,,,,,,,,,,",
        "TEST Imp Five,9899911005,,Delta,,,Goa,TESTNoSuchTagImp,15-12-2026,,",
        "TEST Imp Six,,six@x.in,Eps,,,,Demo Booked,20/12/2026,,",
        "TEST Imp Seven,9899911007,,,,,,,31-31-2026,,",
    ])
    d = preview(admin, content).json()
    job = run_import(admin, content, d["mapping"], {"duplicate_mode": "skip", "assign_mode": "column",
                                                    "default_source": "Bulk Upload", "run_automations": False})
    STATE["job_rules"] = job
    assert job["created"] == 3, job
    assert job["skipped"] == 1, job
    assert job["failed"] == 3, job
    errs = " | ".join(f"{e['row']}:{e['error']}" for e in job["errors"])
    assert "3:" in errs and "abc" in errs
    assert "Note: new tag 'TESTNoSuchTagImp'" in errs
    assert "Missing a valid phone" in errs

    one = find_lead(admin, "9899911001")
    assert len(one) == 1
    lead = admin.get(f"{BASE_URL}/api/leads/{one[0]['id']}").json()
    assert lead["deal_value"] == 150000
    assert lead["follow_up_date"] == "2026-12-01"
    assert lead["lead_stage"] == "Contact Attempt"
    assert lead["bulk_import"] is True and lead["source_lead"] == "Bulk Upload"
    assert lead["designation"] == "CTO" and lead["company_name"] == "Acme"
    assert lead["custom"].get(STATE["cf"]["key"]) == "5L"
    STATE["lead_one"] = lead["id"]
    msgs = admin.get(f"{BASE_URL}/api/leads/{lead['id']}/messages").json()
    items = msgs.get("items", msgs) if isinstance(msgs, dict) else msgs
    assert any("Lead created from bulk upload by" in (m.get("body") or "") for m in items), items

    five = admin.get(f"{BASE_URL}/api/leads/{find_lead(admin, '9899911005')[0]['id']}").json()
    assert five["follow_up_date"] == "2026-12-15"
    cats = admin.get(f"{BASE_URL}/api/catalogs").json()
    tag = [t for t in cats.get("tags", cats.get("tag", [])) if t["name"] == "TESTNoSuchTagImp"]
    assert tag and tag[0]["id"] in five["tags"]

    time.sleep(46)  # dashboard has a 45s in-memory cache (no invalidation on import)
    after = admin.get(f"{BASE_URL}/api/reports/dashboard", params={"section": "kpis"}).json()
    assert after["total_leads"] >= 3 and after["leads_today"] >= 3, after
    assert after["pipeline_value"] >= 150000, after


def test_dates_created_agent_assign(admin, caller):
    cem = STATE["caller_email"]
    content = csv_bytes([
        "Name,Phone,Assigned Agent,Created Date,Follow-up Date",
        f"TEST Imp A1,9899922001,TEST Caller Imp,2025-03-10 09:30,10/04/2026",
        f"TEST Imp A2,9899922002,{cem},15-02-2025,",
        "TEST Imp A3,9899922003,Nobody Person,,",
        "TEST Imp A4,9899922004,,bad-date,",
    ])
    m = preview(admin, content).json()["mapping"]
    assert m["Assigned Agent"] == "_agent" and m["Created Date"] == "_created"
    job = run_import(admin, content, m, {"duplicate_mode": "skip", "assign_mode": "column", "default_source": "Bulk Upload"})
    assert job["created"] == 2 and job["failed"] == 2, job
    a1 = admin.get(f"{BASE_URL}/api/leads/{find_lead(admin, '9899922001')[0]['id']}").json()
    assert a1["user_id"] == STATE["caller_id"]
    assert a1["create_date_ist"].startswith("2025-03-10 09:30"), a1["create_date_ist"]
    assert a1["create_date"].startswith("2025-03-10 04:00"), a1["create_date"]
    assert a1["follow_up_date"] == "2026-04-10"
    a2 = admin.get(f"{BASE_URL}/api/leads/{find_lead(admin, '9899922002')[0]['id']}").json()
    assert a2["user_id"] == STATE["caller_id"] and a2["create_date_ist"].startswith("2025-02-15")


def test_assign_modes(admin, caller):
    content = csv_bytes(["Name,Phone", "TEST Imp R1,9899933001", "TEST Imp R2,9899933002"])
    job = run_import(admin, content, {"Name": "contact_name", "Phone": "phone"},
                     {"assign_mode": "user", "user_id": str(STATE["caller_id"]), "default_source": "TEST Src Imp"})
    assert job["created"] == 2
    r1 = find_lead(admin, "9899933001")[0]
    assert r1["user_id"] == STATE["caller_id"] and r1["source_lead"] == "TEST Src Imp"
    # round robin: only one caller => all to caller
    content = csv_bytes(["Name,Phone", "TEST Imp R3,9899933003"])
    job = run_import(admin, content, {"Name": "contact_name", "Phone": "phone"}, {"assign_mode": "round_robin"})
    assert find_lead(admin, "9899933003")[0]["user_id"] == STATE["caller_id"]
    # user mode without user => 400
    r = admin.post(f"{BASE_URL}/api/import/leads", files={"file": ("t.csv", content)},
                   data={"mapping": json.dumps({"Phone": "phone"}), "options": json.dumps({"assign_mode": "user"})})
    assert r.status_code == 400


def test_update_mode(admin):
    content = csv_bytes(["Phone,Company,Deal Value,City,Tags", "9899911001,Acme Updated,300000,Nashik,Demo Booked"])
    job = run_import(admin, content, {"Phone": "phone", "Company": "company_name", "Deal Value": "deal_value",
                                      "City": "city", "Tags": "_tags"}, {"duplicate_mode": "update", "default_source": "TEST Overwrite Src"})
    assert job["updated"] == 1 and job["created"] == 0, job
    lead = admin.get(f"{BASE_URL}/api/leads/{STATE['lead_one']}").json()
    assert lead["company_name"] == "Acme Updated" and lead["deal_value"] == 300000 and lead["city"] == "Nashik"
    assert lead["contact_name"] == "TEST Imp One"
    assert lead["lead_stage"] == "Contacted"
    assert 18 in lead["tags"] and 4 in lead["tags"]
    msgs = admin.get(f"{BASE_URL}/api/leads/{STATE['lead_one']}/messages").json()
    items = msgs.get("items", msgs) if isinstance(msgs, dict) else msgs
    assert any("Lead updated from bulk upload" in (m.get("body") or "") for m in items)
    # Source column not mapped -> existing lead's source should NOT be overwritten by default_source
    assert lead.get("source_lead") == "Bulk Upload", f"update mode overwrote source_lead -> {lead.get('source_lead')}"


def test_create_mode_marks_duplicate(admin):
    content = csv_bytes(["Name,Phone", "TEST Imp Dup,9899911001"])
    job = run_import(admin, content, {"Name": "contact_name", "Phone": "phone"}, {"duplicate_mode": "create"})
    assert job["created"] == 1
    dups = [l for l in find_lead(admin, "9899911001") if l["id"] != STATE["lead_one"]]
    assert len(dups) == 1
    d = admin.get(f"{BASE_URL}/api/leads/{dups[0]['id']}").json()
    assert d["is_duplicate"] is True and d["duplicate_of"] == STATE["lead_one"]


def test_large_xlsx_2000_rows(admin):
    wb = Workbook(); ws = wb.active
    ws.append(["Name", "Phone", "Email", "Company", "Deal Value (INR)", "Tags"])
    for i in range(2000):
        ws.append([f"TEST Bulk {i}", f"97{i:08d}", f"bulk{i}@x.in", "BulkCo", 1000, "Ringing"])
    buf = io.BytesIO(); wb.save(buf)
    t0 = time.time()
    d = preview(admin, buf.getvalue(), "big.xlsx").json()
    assert d["total_rows"] == 2000
    job = run_import(admin, buf.getvalue(), d["mapping"], {"duplicate_mode": "skip"}, name="big.xlsx", timeout=280)
    print("2000 rows took", round(time.time() - t0, 1), "s; progress seen:", sorted(set(job["_seen"]))[:15])
    assert job["created"] == 2000, job
    assert len(set(job["_seen"])) > 2, "no intermediate progress observed"
    jobs = admin.get(f"{BASE_URL}/api/import/jobs").json()
    assert any(j["id"] == job["id"] for j in jobs) and "errors" not in jobs[0]
