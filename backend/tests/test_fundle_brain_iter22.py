# Fundle Brain (iter22): sessions CRUD, permissions, audit/requests endpoints, tool security guards (no LLM calls)
import os
import sys
import asyncio
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
sys.path.insert(0, "/app/backend")


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture(scope="module")
def admin():
    t = _login(**ADMIN)
    if not t:
        pytest.skip("admin login failed")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {t}"})
    return s


@pytest.fixture(scope="module")
def caller(admin):
    """TEST_ caller user without brain permission; deleted by id afterwards."""
    email = f"test_brain_{uuid.uuid4().hex[:6]}@example.com"
    r = admin.post(f"{BASE_URL}/api/users", json={"name": "TEST_brain_caller", "email": email, "password": "TestPass123!", "role": "caller"})
    assert r.status_code in (200, 201), r.text
    uid = r.json()["id"]
    t = _login(email, "TestPass123!")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {t}"})
    yield s
    admin.delete(f"{BASE_URL}/api/users/{uid}")


# Sessions CRUD
class TestSessions:
    def test_create_rename_list_messages_delete(self, admin):
        r = admin.post(f"{BASE_URL}/api/brain/sessions")
        assert r.status_code == 200
        s = r.json()
        assert isinstance(s["id"], int) and s["title"] == "New chat" and "_id" not in s
        sid = s["id"]
        r = admin.patch(f"{BASE_URL}/api/brain/sessions/{sid}", json={"title": "TEST_brain_session"})
        assert r.status_code == 200
        lst = admin.get(f"{BASE_URL}/api/brain/sessions").json()
        mine = [x for x in lst if x["id"] == sid]
        assert mine and mine[0]["title"] == "TEST_brain_session"
        r = admin.get(f"{BASE_URL}/api/brain/sessions/{sid}/messages")
        assert r.status_code == 200 and r.json() == []
        assert admin.delete(f"{BASE_URL}/api/brain/sessions/{sid}").status_code == 200
        assert all(x["id"] != sid for x in admin.get(f"{BASE_URL}/api/brain/sessions").json())
        assert admin.get(f"{BASE_URL}/api/brain/sessions/{sid}/messages").status_code == 404

    def test_empty_message_400(self, admin):
        sid = admin.post(f"{BASE_URL}/api/brain/sessions").json()["id"]
        r = admin.post(f"{BASE_URL}/api/brain/sessions/{sid}/chat", json={"message": "   "})
        assert r.status_code == 400
        admin.delete(f"{BASE_URL}/api/brain/sessions/{sid}")

    def test_sessions_only_own(self, admin):
        me = admin.get(f"{BASE_URL}/api/auth/me").json()
        for s in admin.get(f"{BASE_URL}/api/brain/sessions").json():
            assert s["user_id"] == me["id"]

    def test_audit_and_requests(self, admin):
        r = admin.get(f"{BASE_URL}/api/brain/audit")
        assert r.status_code == 200 and isinstance(r.json(), list)
        r = admin.get(f"{BASE_URL}/api/brain/requests")
        assert r.status_code == 200 and isinstance(r.json(), list)

    def test_unauthenticated_401(self):
        assert requests.get(f"{BASE_URL}/api/brain/sessions").status_code in (401, 403)


# Permission: non-admin without 'brain' perm
class TestPermission:
    def test_caller_403(self, caller):
        for method, path in [("get", "/api/brain/sessions"), ("post", "/api/brain/sessions"), ("get", "/api/brain/audit"), ("get", "/api/brain/requests")]:
            r = getattr(caller, method)(f"{BASE_URL}{path}")
            assert r.status_code == 403, (path, r.status_code)


# Tool guards (pure functions, in-process)
class TestToolGuards:
    def test_scrub_hides_secrets(self):
        from core import brain
        out = brain._scrub({"_id": 1, "smtp_password": "x", "a": [{"access_token": "t", "ok": 1}], "tokens": {"a": 1}})
        assert out["_id"] == 1 and out["smtp_password"] == "***" and out["a"][0]["access_token"] == "***" and out["tokens"] == "***"

    def test_where_rejected(self):
        from core import brain
        with pytest.raises(ValueError):
            asyncio.get_event_loop().run_until_complete(brain.t_find({}, {"collection": "prospects", "filter": {"$where": "1"}}))
        with pytest.raises(ValueError):
            brain._check([{"$match": {"$expr": {"$function": {}}}}])

    def test_update_refuses_password(self):
        from core import brain
        loop = asyncio.get_event_loop()
        with pytest.raises(ValueError):
            loop.run_until_complete(brain.t_update({}, {"collection": "mailboxes", "filter": {"id": 1}, "set": {"smtp_password": "x"}}))
        with pytest.raises(ValueError):
            loop.run_until_complete(brain.t_update({}, {"collection": "users", "filter": {"id": 1}, "set": {"name": "x"}}))
        with pytest.raises(ValueError):
            loop.run_until_complete(brain.t_update({}, {"collection": "prospects", "filter": {}, "set": {"x": 1}}))
