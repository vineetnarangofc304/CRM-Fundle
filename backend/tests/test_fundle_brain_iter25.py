# Fundle Brain iter25: reason guard (_reason_ok), server GATES regex, upload chat title, tool labels (no LLM)
import os
import re
import sys

import pytest
import requests

sys.path.insert(0, "/app/backend")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
EMAIL, PWD = "vineetnarangofc@gmail.com", "Fundle@cf928c"

from core.brain import _reason_ok, GATES, SYSTEM  # noqa: E402


# Reason guard: rejects invented / meta reasons
@pytest.mark.parametrize("reason", ["User explicitly asked to start emailing", "user requested", "The user wants it",
                                    "as requested", "per the user", "Brain decided explicitly asked", "abc"])
def test_reason_rejects_meta(reason):
    assert _reason_ok(reason, "add these brands reason: trade show list") is False


# Reason guard: words must appear in user's messages
def test_reason_rejects_unrelated_words():
    assert _reason_ok("quarterly growth initiative", "add these brands reason: trade show list") is False


def test_reason_accepts_user_words():
    assert _reason_ok("trade show list", "add these brands — reason: trade show list") is True
    assert _reason_ok("got them from the brands directly", "these are verified emails, reason: got them from the brands directly") is True


# GATES: turn-2 message (with attached note) must not allow send/run_job
def test_gates_block_import_turn():
    t2 = ("[Attached file #9: emails.csv — 2 rows, columns: Company, Person, Title, Email]\n"
          "These are verified emails for these brands, reason: got them from the brands directly").lower()
    assert not re.search(GATES["send_email_campaign"], t2)
    assert not re.search(GATES["run_job"], t2)
    assert not re.search(GATES["send_single_email"], t2)


def test_gates_allow_approval():
    assert re.search(GATES["send_email_campaign"], "yes send it")
    assert re.search(GATES["run_job"], "run the outreach job now")


def test_system_rule4_mentions_import():
    assert "NEVER call send_email_campaign or run_job after an import" in SYSTEM


# Session title for upload chats
@pytest.fixture(scope="module")
def H():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PWD})
    if r.status_code != 200:
        pytest.skip("login failed")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_title_regex_logic():
    for text, want in [("[Attached file #3: brands.csv — 2 rows, columns: Brand]\nAdd these brands", "Add these brands"),
                       ("[Attached file #3: brands.csv — 2 rows, columns: Brand]", "File: brands.csv"),
                       ("hello there", "hello there")]:
        att = re.match(r"\[Attached file #\d+: (.+?) —.*?\]\s*(.*)", text, re.S)
        title = (att.group(2).strip() or f"File: {att.group(1)}") if att else text
        assert title == want


# Frontend labels present
def test_frontend_tool_labels():
    src = open("/app/frontend/src/pages/FundleBrain.jsx").read()
    for k in ("read_uploaded_file", "import_brands_from_file", "import_contact_emails_from_file", "manage_contact_sources"):
        assert re.search(rf"{k}: \"[A-Z]", src), k
