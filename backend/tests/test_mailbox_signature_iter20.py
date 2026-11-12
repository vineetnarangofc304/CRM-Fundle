# Per-inbox signatures (iter20): mailbox signature CRUD API, PUT /api/outreach/signature + primary.signature,
# inbox test email & main-inbox test email include signature (mocked send), senders() signature fallback,
# _send_one appends sender signature (mocked send).
# SAFETY: no real email sent. Unit tests use a throw-away DB; API tests soft-delete created inboxes by id and
# restore settings.outreach.signature afterwards.
import asyncio
import os
import sys
import uuid

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import outreach as oc, mailer, outlook_send as ol, mailboxes as mb, utils as cu, email_templates as et  # noqa: E402
from routes import mailboxes as rmb, outlook as rol  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
SIG = "Regards,\nTEST Box Person\nSales | Fundle.AI"


def run(coro):
    asyncio.set_event_loop(LOOP)
    return LOOP.run_until_complete(coro)


@pytest.fixture(scope="module")
def s():
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return ss


@pytest.fixture(scope="module")
def created(s):
    ids = []
    yield ids
    for i in ids:
        s.delete(f"{BASE_URL}/api/admin/mailboxes/{i}")


def _box(**k):
    return {"smtp_user": f"test_{uuid.uuid4().hex[:6]}@example.com", "smtp_password": "fakepass", "sender_name": "TEST",
            "smtp_host": "smtp.office365.com", "smtp_port": 587, "daily_cap": 200, "active": True, **k}


class TestSignatureAPI:
    def test_box_signature_create_list_update(self, s, created):
        r = s.post(f"{BASE_URL}/api/admin/mailboxes", json=_box(signature="  " + SIG + "  "))
        assert r.status_code == 200, r.text
        b = r.json(); created.append(b["id"])
        assert b["signature"] == SIG
        row = next(x for x in s.get(f"{BASE_URL}/api/admin/mailboxes").json()["boxes"] if x["id"] == b["id"])
        assert row["signature"] == SIG
        # update other field, keep signature
        r = s.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json=_box(smtp_user=b["smtp_user"], smtp_password="",
                                                                          signature=SIG, daily_cap=120))
        assert r.status_code == 200 and r.json()["signature"] == SIG and r.json()["daily_cap"] == 120
        # update signature
        r = s.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json=_box(smtp_user=b["smtp_user"], smtp_password="",
                                                                          signature="New sig"))
        assert r.json()["signature"] == "New sig"
        row = next(x for x in s.get(f"{BASE_URL}/api/admin/mailboxes").json()["boxes"] if x["id"] == b["id"])
        assert row["signature"] == "New sig"

    def test_put_without_signature_field(self, s, created):
        """Observational: a PUT that omits 'signature' (older client) wipes the saved signature."""
        b = s.post(f"{BASE_URL}/api/admin/mailboxes", json=_box(signature=SIG)).json(); created.append(b["id"])
        body = _box(smtp_user=b["smtp_user"], smtp_password="")
        body.pop("signature", None)
        r = s.put(f"{BASE_URL}/api/admin/mailboxes/{b['id']}", json=body)
        print("signature after PUT w/o field:", repr(r.json().get("signature")))

    def test_main_signature_save_and_list(self, s):
        orig = s.get(f"{BASE_URL}/api/admin/mailboxes").json()
        cur = run(_get_main_sig())
        try:
            r = s.put(f"{BASE_URL}/api/outreach/signature", json={"signature": "TEST main sig"})
            assert r.status_code == 200 and r.json()["signature"] == "TEST main sig"
            assert run(_get_main_sig()) == "TEST main sig"
            d = s.get(f"{BASE_URL}/api/admin/mailboxes").json()
            if d["primary"]:
                assert d["primary"]["signature"] == "TEST main sig"
            else:
                print("primary is None in preview (no main inbox) — primary.signature not checkable via API")
        finally:
            r = s.put(f"{BASE_URL}/api/outreach/signature", json={"signature": cur})
            assert r.status_code == 200
            assert run(_get_main_sig()) == cur
        assert orig is not None


async def _get_main_sig():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = await cli[os.environ["DB_NAME"]].settings.find_one({"key": "outreach"}) or {}
    return d.get("signature") or ""


# ---------------- unit (isolated DB) ----------------
@pytest.fixture
def tdb(monkeypatch):
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])
    cli = run(mk())
    name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
    d = cli[name]
    for mod in (oc, mb, cu, et, rmb, rol):
        monkeypatch.setattr(mod, "db", d)
    yield d
    run(cli.drop_database(name))


USER = {"id": 1, "name": "TEST", "role": "admin"}


class TestTestEmails:
    def _capture(self, monkeypatch):
        cap = []

        async def fake(cfg, to, subject, body, html=False, cc=None, attachments=None):
            cap.append({"cfg": cfg, "to": to, "body": body}); return {"ok": True}
        monkeypatch.setattr(ol, "send_with_config", fake)
        return cap

    def test_box_test_uses_box_signature(self, tdb, monkeypatch):
        cap = self._capture(monkeypatch)

        async def go():
            await tdb.settings.insert_one({"key": "outreach", "signature": "Main Person"})
            await tdb.mailboxes.insert_one({"id": 7, "smtp_user": "b7@fundle.ai", "smtp_password_enc": "x", "smtp_host": "h",
                                            "smtp_port": 587, "signature": "Box Person\nCBO", "is_deleted": False})
            await rmb.test_box(7, rmb.TestTo(to="t@example.com"), USER)
            return await tdb.mailboxes.find_one({"id": 7})
        b = run(go())
        assert "Box Person<br>CBO" in cap[0]["body"] and "Main Person" not in cap[0]["body"]
        assert b["verified"] is True

    def test_box_test_falls_back_to_main(self, tdb, monkeypatch):
        cap = self._capture(monkeypatch)

        async def go():
            await tdb.settings.insert_one({"key": "outreach", "signature": "Main Person\nCBO"})
            await tdb.mailboxes.insert_one({"id": 8, "smtp_user": "b8@fundle.ai", "smtp_password_enc": "x", "smtp_host": "h",
                                            "smtp_port": 587, "signature": "", "is_deleted": False})
            await rmb.test_box(8, rmb.TestTo(to="t@example.com"), USER)
        run(go())
        assert "Main Person<br>CBO" in cap[0]["body"]

    def test_main_send_test_appends_main_signature(self, tdb, monkeypatch):
        cap = []

        async def ic():
            return True

        async def fake(to, subject, body, html=False, cc=None, attachments=None):
            cap.append(body); return {"ok": True}

        async def gc():
            return {"method": "graph"}
        monkeypatch.setattr(ol, "is_connected", ic)
        monkeypatch.setattr(ol, "send_email", fake)
        monkeypatch.setattr(ol, "get_config", gc)

        async def go():
            await tdb.settings.insert_one({"key": "outreach", "signature": "Abhinav TEST\nCBO | Fundle.AI"})
            return await rol.outlook_send_test(rol.TestEmail(to="t@example.com"), USER)
        run(go())
        assert len(cap) == 1 and "Abhinav TEST<br>CBO | Fundle.AI" in cap[0]


def _conn(monkeypatch):
    async def ic():
        return True

    async def gc():
        return {"email": "main@fundle.ai", "method": "graph"}
    monkeypatch.setattr(mailer, "is_connected", ic)
    monkeypatch.setattr(ol, "get_config", gc)


class TestSendersSignature:
    def test_senders_signature_fallback(self, tdb, monkeypatch):
        _conn(monkeypatch)

        async def go():
            for bid, sig in ((1, "Box1 sig"), (2, "")):
                await tdb.mailboxes.insert_one({"id": bid, "smtp_user": f"b{bid}@fundle.ai", "smtp_password_enc": "x",
                                                "daily_cap": 200, "active": True, "verified": True, "is_deleted": False,
                                                "smtp_host": "h", "smtp_port": 587, "signature": sig})
            return await mb.senders({"daily_cap": 200, "signature": "Main sig"}, "2026-01-15")
        by = {x["id"]: x for x in run(go())}
        assert by[0]["signature"] == "Main sig" and by[1]["signature"] == "Box1 sig" and by[2]["signature"] == "Main sig"

    def test_send_one_appends_sender_signature(self, tdb, monkeypatch):
        sent = []

        async def fake_send(s, to, subject, body, cc=None, attachments=None):
            sent.append((s["id"], body)); return {"ok": True}
        monkeypatch.setattr(mb, "send", fake_send)

        async def go():
            await tdb.prospects.insert_many([{"id": 1, "sender_mailbox": 5}, {"id": 2, "sender_mailbox": 0}])
            await tdb.mailboxes.insert_one({"id": 5, "is_deleted": False})
            base = {"seq_id": 1, "channel": "email", "step": 3, "status": "sending", "contact_name": "TEST", "subject": "S",
                    "body": "<p>hi</p>", "to": "x@example.com"}
            await tdb.outreach.insert_many([{**base, "id": 1, "prospect_id": 1}, {**base, "id": 2, "prospect_id": 2}])
            pool = [{"id": 0, "email": "main@fundle.ai", "remaining": 5, "cfg": None, "signature": "Main sig"},
                    {"id": 5, "email": "b5@fundle.ai", "remaining": 5, "cfg": {}, "signature": "Box5 sig"}]
            for i in (1, 2):
                m = await tdb.outreach.find_one({"id": i}, {"_id": 0})
                assert await oc._send_one(m, oc.DEFAULTS, pool) is True
        run(go())
        d = dict(sent)
        assert "Box5 sig" in d[5] and "Main sig" not in d[5]
        assert "Main sig" in d[0]
