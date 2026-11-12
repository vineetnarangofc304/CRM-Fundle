# Iteration 21: hourly sending limit per inbox (settings.outreach.hourly_cap, default 25, clamped 1-100) and
# GET /api/admin/mailboxes per-hour fields. Restores hourly_cap / settings afterwards. Never sends email.
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
SKIP = ("mail_connected", "mail_provider", "last_run", "sender_name")


@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


class TestHourlyCapSettings:
    def test_get_default_25(self, s):
        r = s.get(f"{BASE_URL}/api/outreach/settings")
        assert r.status_code == 200
        assert r.json()["hourly_cap"] == 25

    @pytest.mark.parametrize("val,exp", [(30, 30), (0, 1), (-5, 1), (500, 100), (100, 100), (1, 1)])
    def test_put_clamps(self, s, val, exp):
        cur = s.get(f"{BASE_URL}/api/outreach/settings").json()
        body = {k: v for k, v in cur.items() if k not in SKIP}
        try:
            r = s.put(f"{BASE_URL}/api/outreach/settings", json={**body, "hourly_cap": val})
            assert r.status_code == 200, r.text
            assert r.json()["hourly_cap"] == exp
            assert s.get(f"{BASE_URL}/api/outreach/settings").json()["hourly_cap"] == exp
            assert s.get(f"{BASE_URL}/api/admin/mailboxes").json()["hourly_cap"] == exp
        finally:
            s.put(f"{BASE_URL}/api/outreach/settings", json={**body, "hourly_cap": 25})
        assert s.get(f"{BASE_URL}/api/outreach/settings").json()["hourly_cap"] == 25


class TestMailboxesHourly:
    def test_box_without_daily_cap_and_hour_fields(self, s):
        # UI no longer sends daily_cap; backend must accept it
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json={"smtp_user": f"test_{uuid.uuid4().hex[:6]}@example.com",
                                                            "smtp_password": "fake", "sender_name": "TEST"})
        assert r.status_code == 200, r.text
        bid = r.json()["id"]
        try:
            d = s.get(f"{BASE_URL}/api/admin/mailboxes").json()
            assert d["hourly_cap"] == 25
            row = next(x for x in d["boxes"] if x["id"] == bid)
            assert row["hourly_cap"] == 25 and row["sent_hour"] == 0 and row["sent_today"] == 0
            print("primary:", d["primary"])
        finally:
            assert s.delete(f"{BASE_URL}/api/admin/mailboxes/{bid}").status_code == 200
