# Fundle Brain iter24: file upload endpoint, contact sources endpoint, brain_data unit checks (no LLM)
import asyncio
import io
import os
import sys

import pytest
import requests

sys.path.insert(0, "/app/backend")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
EMAIL, PWD = "vineetnarangofc@gmail.com", "Fundle@cf928c"


@pytest.fixture(scope="module")
def H():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PWD})
    if r.status_code != 200:
        pytest.skip("login failed")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def sid(H):
    s = requests.post(f"{BASE_URL}/api/brain/sessions", headers=H).json()["id"]
    yield s
    requests.delete(f"{BASE_URL}/api/brain/sessions/{s}", headers=H)


# Upload: CSV
def test_upload_csv(H, sid):
    with open("/app/test_data/emails.csv", "rb") as f:
        r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", headers=H, files={"file": ("emails.csv", f, "text/csv")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert isinstance(d["id"], int) and d["filename"] == "emails.csv" and d["rows"] == 2
    assert d["columns"] == ["Company", "Person", "Title", "Email"]
    assert "_id" not in d


# Upload: XLSX
def test_upload_xlsx(H, sid):
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame([{"Brand": "TEST_X1", "Category": "Grocery"}, {"Brand": "TEST_X2", "Category": "Toys"},
                  {"Brand": None, "Category": None}]).to_excel(buf, index=False)
    r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", headers=H,
                      files={"file": ("b.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 2 and r.json()["columns"] == ["Brand", "Category"]


# Upload: rejects other types / bad xlsx / too large / unknown session / no auth
def test_upload_bad_type(H, sid):
    r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", headers=H, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400 and "Excel" in r.json()["detail"]


def test_upload_corrupt_xlsx(H, sid):
    r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", headers=H, files={"file": ("a.xlsx", b"not a zip", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_too_large(H, sid):
    r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", headers=H,
                      files={"file": ("big.csv", b"a,b\n" + b"x" * (15 * 1024 * 1024 + 10), "text/csv")}, timeout=120)
    assert r.status_code == 413


def test_upload_unknown_session(H):
    r = requests.post(f"{BASE_URL}/api/brain/sessions/99999999/upload", headers=H, files={"file": ("e.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 404


def test_upload_no_auth(sid):
    r = requests.post(f"{BASE_URL}/api/brain/sessions/{sid}/upload", files={"file": ("e.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code in (401, 403)


# Sources endpoint
def test_sources_list(H):
    r = requests.get(f"{BASE_URL}/api/brain/sources", headers=H)
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "Indian Retailer" in names and "ETRetail" in names and len(names) >= 5


# brain_data unit checks
def test_sources_prompt_unit():
    from core import brain_data as bd
    out = bd.sources_prompt(bd.DEFAULT_SOURCES + [{"name": "Retail4Growth", "url": "https://www.retail4growth.com", "notes": ""}])
    assert "https://www.retail4growth.com" in out and "Indian Retailer" in out
    assert bd.sources_prompt([]) == ""


def test_parse_file_unit():
    from core import brain_data as bd
    rows = bd.parse_file("x.csv", b"Brand;Stores\nA;3\nB;\n")
    assert rows == [{"Brand": "A", "Stores": "3"}, {"Brand": "B", "Stores": ""}]
    with pytest.raises(ValueError):
        bd.parse_file("x.json", b"{}")


def test_tools_registered():
    from core import brain
    for t in ("read_uploaded_file", "import_brands_from_file", "import_contact_emails_from_file", "manage_contact_sources"):
        assert t in brain.TOOLS
    assert "read_uploaded_file" in brain.READ_ONLY
    assert "Never switch engines" in brain.SYSTEM
