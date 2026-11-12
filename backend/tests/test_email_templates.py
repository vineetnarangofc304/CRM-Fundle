# Email template library (Outreach → Email templates): CRUD + validation, AI suggest, brochure uploads,
# click-to-send per contact (use-template), email history, send-now, send-selected.
# Unit tests (isolated throw-away DB + monkeypatched mailer/storage/LLM): _send_one attachments + video block +
# lead log, run_auto, _migrate_v3, Outlook Graph / SMTP / Gmail attachment payloads.
# SAFETY: no real email is sent (no mailbox connected in preview; providers monkeypatched in unit tests).
# Live-DB data created here is TEST_-prefixed and removed by id at the end.
import asyncio
import base64
import email
import os
import random
import sys
import uuid

import pytest
import requests

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from core import outreach as oc, email_templates as et, mailer, outlook_send, gmail_send  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    asyncio.set_event_loop(LOOP)  # other suites may switch/clear the current loop
    return LOOP.run_until_complete(coro)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _livedb():
    async def mk():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])
    return run(mk())[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module")
def created():
    """Track everything created in the live DB, cleaned up by id."""
    c = {"tpl": [], "msg": [], "file": [], "prospect": []}
    yield c
    d = _livedb()
    s = requests.Session()
    tok = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN).json()["access_token"]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    for t in c["tpl"]:
        s.delete(f"{BASE_URL}/api/outreach/templates/{t}")
    if c["msg"]:
        run(d.outreach.delete_many({"id": {"$in": c["msg"]}}))
    if c["file"]:
        run(d.outreach_files.update_many({"id": {"$in": c["file"]}}, {"$set": {"is_deleted": True}}))
    if c["prospect"]:
        # create_message now creates the lead for the prospect: delete that TEST_ lead + its log by id too
        lids = [p["lead_id"] for p in run(d.prospects.find({"id": {"$in": c["prospect"]}, "lead_id": {"$exists": True}},
                                                             {"lead_id": 1}).to_list(50))]
        if lids:
            run(d.leads.delete_many({"id": {"$in": lids}, "name": {"$regex": "^TEST_"}}))
            run(d.messages.delete_many({"lead_id": {"$in": lids}}))
        run(d.prospects.delete_many({"id": {"$in": c["prospect"]}}))


@pytest.fixture(scope="module")
def prospect(created):
    d = _livedb()
    pid = 9_000_000 + random.randint(1, 999_999)
    run(d.prospects.insert_one({"id": pid, "brand_name": "TEST_TplBrand", "brand_key": f"test_tplbrand_{pid}", "category": "Apparel", "country": "India",
                                "fit_score": 100, "status": "enriched", "description": "TEST brand",
                                "contacts": [{"name": "Asha Rao", "role": "CMO", "email": "TEST_asha@example.com",
                                              "email_status": "published"},
                                             {"name": "No Mail", "role": "CEO", "email": None}]}))
    created["prospect"].append(pid)
    return pid


def _tpl(**kw):
    return {"name": "TEST_Tpl", "subject": "Hi {{brand}}", "body": "<p>Hi {{first_name}}, {{brand}}</p>",
            "mode": "manual", "active": True, "attachments": [], "video_links": [], **kw}


# ---------------- templates CRUD ----------------
class TestTemplatesAPI:
    def test_list_has_starters(self, admin):
        r = admin.get(f"{BASE_URL}/api/outreach/templates")
        assert r.status_code == 200
        by = {t["name"]: t for t in r.json()}
        exp = {"Intro email": "manual", "Marketplace emailer": "manual", "Loyalty emailer": "manual",
               "MBO emailer": "manual", "SIS emailer": "manual"}
        for n, mode in exp.items():
            assert n in by and by[n]["mode"] == mode
            assert "{{brand}}" in by[n]["subject"] and "{{first_name}}" in by[n]["body"]
        assert all("_id" not in t for t in r.json())
        # second call must not duplicate
        r2 = admin.get(f"{BASE_URL}/api/outreach/templates").json()
        assert len([t for t in r2 if t["name"] == "Intro email"]) == 1

    def test_create_update_delete(self, admin, created):
        r = admin.post(f"{BASE_URL}/api/outreach/templates",
                       json=_tpl(video_links=["https://youtu.be/x", "  "], body="<p>x</p><script>alert(1)</script>"))
        assert r.status_code == 200, r.text
        t = r.json()
        created["tpl"].append(t["id"])
        assert t["kind"] == "custom" and t["video_links"] == ["https://youtu.be/x"] and "<script" not in t["body"]
        r = admin.put(f"{BASE_URL}/api/outreach/templates/{t['id']}", json=_tpl(name="TEST_Tpl2", mode="auto", active=False))
        assert r.status_code == 200 and r.json()["name"] == "TEST_Tpl2" and r.json()["mode"] == "auto"
        lst = {x["id"]: x for x in admin.get(f"{BASE_URL}/api/outreach/templates").json()}
        assert lst[t["id"]]["name"] == "TEST_Tpl2" and lst[t["id"]]["active"] is False
        assert admin.delete(f"{BASE_URL}/api/outreach/templates/{t['id']}").json() == {"ok": True}
        assert t["id"] not in {x["id"] for x in admin.get(f"{BASE_URL}/api/outreach/templates").json()}
        assert admin.put(f"{BASE_URL}/api/outreach/templates/{t['id']}", json=_tpl()).status_code == 404

    @pytest.mark.parametrize("bad", [
        {"mode": "sometimes"}, {"name": "  "}, {"subject": ""}, {"body": " "},
        {"video_links": ["ftp://x"]}, {"video_links": ["javascript:alert(1)"]},
        {"attachments": [{"file_id": 1, "filename": "a.pdf", "size": 2_000_000},
                         {"file_id": 2, "filename": "b.pdf", "size": 1_200_000}]}])
    def test_validation(self, admin, bad):
        r = admin.post(f"{BASE_URL}/api/outreach/templates", json=_tpl(**bad))
        assert r.status_code == 400, r.text
        assert r.json().get("detail")

    def test_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/outreach/templates").status_code in (401, 403)

    def test_ai_suggest_clean(self, admin):
        r = admin.post(f"{BASE_URL}/api/outreach/templates/ai-suggest", json={"name": "TEST loyalty", "brief": "loyalty"},
                       timeout=90)
        assert r.status_code in (200, 503, 502), r.text
        if r.status_code == 200:
            assert r.json()["subject"] and r.json()["body"]
        else:
            assert "detail" in r.json() and r.json()["detail"]


# ---------------- brochure uploads ----------------
class TestFiles:
    def test_upload_download_pdf(self, admin, created):
        r = admin.post(f"{BASE_URL}/api/outreach/files", files={"file": ("TEST_brochure.pdf", PDF, "application/pdf")})
        assert r.status_code == 200, r.text
        d = r.json()
        created["file"].append(d["file_id"])
        assert d["filename"] == "TEST_brochure.pdf" and d["size"] == len(PDF)
        g = admin.get(f"{BASE_URL}/api/outreach/files/{d['file_id']}")
        assert g.status_code == 200 and g.content == PDF and g.headers["content-type"].startswith("application/pdf")

    def test_reject_type(self, admin):
        r = admin.post(f"{BASE_URL}/api/outreach/files", files={"file": ("TEST_x.exe", b"MZ", "application/octet-stream")})
        assert r.status_code == 400

    def test_reject_too_big(self, admin):
        r = admin.post(f"{BASE_URL}/api/outreach/files",
                       files={"file": ("TEST_big.pdf", b"0" * (3 * 1024 * 1024 + 1), "application/pdf")}, timeout=60)
        assert r.status_code == 400

    def test_download_unknown_404(self, admin):
        assert admin.get(f"{BASE_URL}/api/outreach/files/99999999").status_code == 404

    def test_non_ascii_filename_download(self, admin, created):
        r = admin.post(f"{BASE_URL}/api/outreach/files", files={"file": ("TEST_broschüre–2026.pdf", PDF, "application/pdf")})
        assert r.status_code == 200, r.text
        created["file"].append(r.json()["file_id"])
        g = admin.get(f"{BASE_URL}/api/outreach/files/{r.json()['file_id']}")
        assert g.status_code == 200, f"download of non-ASCII filename returned {g.status_code}"


# ---------------- click-to-send per contact ----------------
class TestUseTemplate:
    def test_use_template_flow(self, admin, created, prospect):
        tpls = admin.get(f"{BASE_URL}/api/outreach/templates").json()
        up = admin.post(f"{BASE_URL}/api/outreach/files", files={"file": ("TEST_deck.pdf", PDF, "application/pdf")}).json()
        created["file"].append(up["file_id"])
        t = admin.post(f"{BASE_URL}/api/outreach/templates", json=_tpl(
            name="TEST_UseTpl", subject="For {{brand}}", body="<p>Hi {{first_name}}, about {{brand}}.</p>",
            attachments=[up], video_links=["https://youtu.be/demo"])).json()
        created["tpl"].append(t["id"])
        r = admin.post(f"{BASE_URL}/api/prospecting/prospects/{prospect}/use-template",
                       json={"template_id": t["id"], "contact_index": 0}, timeout=120)
        assert r.status_code == 200, r.text
        mid = r.json()["id"]
        created["msg"].append(mid)
        m = next(x for x in admin.get(f"{BASE_URL}/api/outreach/messages",
                                      params={"status": "pending_approval", "limit": 200}).json()["items"] if x["id"] == mid)
        assert m["kind"] == "template" and m["status"] == "pending_approval" and m["template_name"] == "TEST_UseTpl"
        assert m["to"] == "TEST_asha@example.com" and m["step"] == 1
        assert "{{" not in m["subject"] + m["body"]
        assert "TEST_TplBrand" in m["subject"] or "TEST_TplBrand" in m["body"]
        assert m["attachments"][0]["file_id"] == up["file_id"] and m["video_links"] == ["https://youtu.be/demo"]
        # history
        h = admin.get(f"{BASE_URL}/api/prospecting/prospects/{prospect}/emails").json()
        assert any(x["id"] == mid and x["template_name"] == "TEST_UseTpl" for x in h)
        # send-now in preview: no mailbox
        s = admin.post(f"{BASE_URL}/api/outreach/messages/{mid}/send-now")
        assert s.status_code == 400 and "No email account connected" in s.json()["detail"]
        # send-selected → approved with scheduled_at
        q = admin.post(f"{BASE_URL}/api/outreach/messages/send-selected", json={"ids": [mid]})
        assert q.status_code == 200 and q.json()["queued"] == 1
        m2 = next(x for x in admin.get(f"{BASE_URL}/api/outreach/messages",
                                       params={"status": "approved", "limit": 200}).json()["items"] if x["id"] == mid)
        assert m2["scheduled_at"] and m2["approved_at"]
        assert admin.post(f"{BASE_URL}/api/outreach/messages/send-selected", json={"ids": [mid]}).json()["queued"] == 0
        # reject so it never goes out if a mailbox gets connected before cleanup
        assert admin.post(f"{BASE_URL}/api/outreach/messages/{mid}/reject").status_code == 200

    def test_contact_without_email_400(self, admin, prospect):
        tid = admin.get(f"{BASE_URL}/api/outreach/templates").json()[0]["id"]
        r = admin.post(f"{BASE_URL}/api/prospecting/prospects/{prospect}/use-template", json={"template_id": tid, "contact_index": 1})
        assert r.status_code == 400
        r = admin.post(f"{BASE_URL}/api/prospecting/prospects/{prospect}/use-template", json={"template_id": tid, "contact_index": 9})
        assert r.status_code == 400

    def test_unknown_template_404(self, admin, prospect):
        r = admin.post(f"{BASE_URL}/api/prospecting/prospects/{prospect}/use-template",
                       json={"template_id": 99999999, "contact_index": 0})
        assert r.status_code == 404


# ---------------- core unit tests (isolated DB) ----------------
async def _yes():
    return True


class TestCore:
    @pytest.fixture(autouse=True)
    def tdb(self, monkeypatch):
        async def mk():
            return AsyncIOMotorClient(os.environ["MONGO_URL"])
        cli = run(mk())
        name = f"{os.environ['DB_NAME']}_t1_{uuid.uuid4().hex[:8]}"
        d = cli[name]
        monkeypatch.setattr(oc, "db", d)
        from core import mailboxes as _mb
        monkeypatch.setattr(_mb, "db", d)  # multi-inbox: senders()/pick() read mailboxes.db
        monkeypatch.setattr(outlook_send, "db", d)
        run(d.settings.insert_one({"key": "outreach_v4", "done": True}))  # keep the test-set daily_cap
        monkeypatch.setattr(et, "db", d)
        ctr = {"n": 0}

        async def nid(_):
            ctr["n"] += 1
            return ctr["n"]
        monkeypatch.setattr(et, "next_id", nid)
        from core import prospector as _pe, utils as _cu
        monkeypatch.setattr(_pe, "db", d)  # create_message -> upsert_lead must never touch the real DB
        monkeypatch.setattr(_cu, "db", d)
        monkeypatch.setattr(_pe, "next_id", nid)

        async def no_ai(*a, **k):
            raise RuntimeError("Budget exceeded")
        monkeypatch.setattr(et, "ai_text", no_ai)
        self.db = d
        yield d
        run(cli.drop_database(name))

    def test_send_one_attachments_video_log(self, monkeypatch):
        run(self.db.outreach_files.insert_one({"id": 7, "storage_path": "p/7.pdf", "filename": "deck.pdf",
                                               "content_type": "application/pdf", "size": 10}))

        async def go(path):
            assert path == "p/7.pdf"
            return PDF, "application/pdf"
        monkeypatch.setattr(et, "get_object", go)
        monkeypatch.setattr(mailer, "is_connected", _yes)  # multi-inbox: _send_one needs a sender in the pool
        sent, logs = [], []

        async def send(to, subject, body, html=False, cc=None, attachments=None):
            sent.append(dict(to=to, body=body, cc=cc, attachments=attachments))
            return {"ok": True}

        async def log(lead_id, body, author=None, **k):
            logs.append((lead_id, body))
        monkeypatch.setattr(mailer, "send_email", send)
        monkeypatch.setattr(oc, "log_message", log)
        m = {"id": 1, "seq_id": 1, "step": 1, "channel": "email", "kind": "template", "template_name": "Intro email",
             "template_id": 0, "to": "a@example.com", "contact_name": "A", "subject": "S", "body": "<p>B</p>", "lead_id": 42,
             "attachments": [{"file_id": 7, "filename": "deck.pdf", "size": 10}], "video_links": ["https://youtu.be/v"],
             "status": "sending"}
        run(self.db.outreach.insert_one(dict(m)))
        # signature is now user-defined (DEFAULTS signature is ''); use a custom one
        st = {**oc.DEFAULTS, "cc": "c@example.com", "signature": "TEST Sig\nFundle"}
        assert run(oc._send_one(m, st)) is True
        s = sent[0]
        assert s["attachments"] == [{"filename": "deck.pdf", "content_type": "application/pdf", "data": PDF}]
        assert "https://youtu.be/v" in s["body"] and "TEST Sig<br>Fundle" in s["body"] and s["cc"] == "c@example.com"
        assert s["body"].index("youtu.be") < s["body"].index("TEST Sig")
        assert logs[0][0] == 42 and "Intro email" in logs[0][1] and "deck.pdf" in logs[0][1] and "video" in logs[0][1]
        assert run(self.db.outreach.find_one({"id": 1}))["status"] == "sent"

    def test_send_one_storage_error_retries(self, monkeypatch):
        run(self.db.outreach_files.insert_one({"id": 8, "storage_path": "p/8", "filename": "x.pdf",
                                               "content_type": "application/pdf", "size": 1}))

        async def go(path):
            raise RuntimeError("storage down")
        monkeypatch.setattr(et, "get_object", go)
        monkeypatch.setattr(mailer, "is_connected", _yes)  # multi-inbox: _send_one needs a sender in the pool
        m = {"id": 2, "seq_id": 2, "step": 1, "channel": "email", "to": "a@example.com", "contact_name": "A",
             "subject": "S", "body": "b", "attachments": [{"file_id": 8, "filename": "x.pdf"}], "status": "sending"}
        run(self.db.outreach.insert_one(dict(m)))
        assert run(oc._send_one(m, dict(oc.DEFAULTS))) is False
        d = run(self.db.outreach.find_one({"id": 2}))
        assert d["status"] == "approved" and "Attachment unavailable" in d["error"]

    def test_run_auto(self):
        # iter18: generic intro is manual; the 4 store-count intro templates (ai_tailor False) are auto
        run(et.seed_starters())
        run(et.seed_intro_v2())
        tpls = run(self.db.email_templates.find({}, {"_id": 0}).to_list(20))
        assert len(tpls) == 9 and sorted(t["kind"] for t in tpls if t["mode"] == "auto") == ["intro_100", "intro_11_99", "intro_1_10", "intro_d2c"]
        intro = next(t for t in tpls if t["kind"] == "intro_1_10")
        run(self.db.prospects.insert_many([
            {"id": 1, "brand_name": "TEST_A", "category": "c", "country": "IN", "fit_score": 90, "contacts": [{"name": "Ann Lee", "email": "ann@a.com"}]},
            {"id": 2, "brand_name": "TEST_B", "fit_score": 50, "contacts": [{"name": "Bo", "email": None}]},
            {"id": 3, "brand_name": "TEST_C", "fit_score": 70, "contacts": [{"name": "Cy", "email": "c@c.com"}],
             "tpl_sent": [intro["id"]]},
            {"id": 4, "brand_name": "TEST_D", "category": "c", "country": "IN", "fit_score": 10, "contacts": [{"name": "N", "email": ""}, {"name": "Di", "email": "d@d.com"}]}]))
        assert run(et.run_auto(10)) == 2
        msgs = run(self.db.outreach.find({}, {"_id": 0}).to_list(10))
        assert sorted(m["prospect_id"] for m in msgs) == [1, 4]
        m1 = next(m for m in msgs if m["prospect_id"] == 1)
        assert m1["status"] == "approved" and m1["scheduled_at"] and m1["kind"] == "template"
        assert m1["template_name"] == intro["name"] and "Ann" in m1["body"] and "TEST_A" in m1["subject"]
        assert intro["id"] in run(self.db.prospects.find_one({"id": 1}))["tpl_sent"]
        assert run(et.run_auto(10)) == 0, "same template must not be sent twice"

    def test_run_auto_limit_and_inactive(self):
        run(et.seed_starters())
        run(self.db.prospects.insert_many([{"id": i, "brand_name": f"TEST_{i}", "category": "c", "country": "IN", "fit_score": i,
                                            "contacts": [{"name": "X", "email": f"x{i}@e.com"}]} for i in range(1, 6)]))
        assert run(et.run_auto(2)) == 2
        assert run(et.run_auto(0)) == 0
        run(self.db.email_templates.update_many({}, {"$set": {"active": False}}))
        assert run(et.run_auto(10)) == 0

    def test_tailor_fallback_merge(self):
        s, b = run(et.tailor({"subject": "Hi {{brand}}", "body": "<p>Hi {{first_name}} {{unknown}}</p>"},
                             {"brand_name": "Zed"}, {"name": "Mia Wong"}))
        assert s == "Hi Zed" and "Hi Mia" in b

    def test_migrate_v3(self):
        run(self.db.outreach.insert_many([
            {"id": 1, "channel": "email", "status": "pending_approval"},
            {"id": 2, "channel": "email", "status": "approved"},
            {"id": 3, "channel": "email", "status": "sent"},
            {"id": 4, "channel": "email", "status": "pending_approval", "kind": "template"}]))
        run(self.db.settings.insert_one({"key": "outreach", "ai_sequence": True}))
        run(oc._migrate_v3())
        st = {d["id"]: d["status"] for d in run(self.db.outreach.find({}).to_list(10))}
        assert st == {1: "stopped", 2: "stopped", 3: "sent", 4: "pending_approval"}
        assert run(oc.get_settings())["ai_sequence"] is False
        assert run(self.db.email_templates.count_documents({})) == 5
        run(self.db.outreach.insert_one({"id": 5, "channel": "email", "status": "pending_approval"}))
        run(oc._migrate_v3())
        assert run(self.db.outreach.find_one({"id": 5}))["status"] == "pending_approval", "migration must run once"

    def test_auto_approve_skips_templates(self):
        run(self.db.outreach.insert_many([
            {"id": 1, "channel": "email", "step": 1, "status": "pending_approval", "kind": "template"},
            {"id": 2, "channel": "email", "step": 1, "status": "pending_approval"}]))
        assert run(oc.auto_approve()) == 1
        assert run(self.db.outreach.find_one({"id": 1}))["status"] == "pending_approval"


# ---------------- provider payloads ----------------
ATT = [{"filename": "deck.pdf", "content_type": "application/pdf", "data": PDF}]


class TestProviders:
    def test_mailer_passes_attachments(self, monkeypatch):
        got = {}

        async def ap():
            return "outlook"

        async def se(to, subject, body, html=False, cc=None, attachments=None):
            got["att"] = attachments
            return {"ok": True}
        monkeypatch.setattr(mailer, "active_provider", ap)
        monkeypatch.setattr(outlook_send, "send_email", se)
        run(mailer.send_email("a@example.com", "s", "b", html=True, attachments=ATT))
        assert got["att"] == ATT

    def test_graph_attachments(self, monkeypatch):
        sent = {}

        async def tok(cfg, **d):
            return {"access_token": "x"}

        class Resp:
            status_code = 202

        class Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                sent.update(json)
                return Resp()
        monkeypatch.setattr(outlook_send, "_token", tok)
        monkeypatch.setattr(outlook_send.httpx, "AsyncClient", Client)
        assert run(outlook_send._graph_send({}, "a@example.com", "s", "b", True, None, ATT)) == {"ok": True}
        a = sent["message"]["attachments"][0]
        assert a["@odata.type"] == "#microsoft.graph.fileAttachment" and a["name"] == "deck.pdf"
        assert a["contentType"] == "application/pdf" and base64.b64decode(a["contentBytes"]) == PDF
        sent.clear()
        run(outlook_send._graph_send({}, "a@example.com", "s", "b", True))
        assert "attachments" not in sent["message"]

    def test_smtp_attachments(self, monkeypatch):
        msgs = []

        async def fake_send(msg, **k):
            msgs.append(msg)
        monkeypatch.setattr(outlook_send.aiosmtplib, "send", fake_send)
        run(outlook_send._smtp_send({"smtp_user": "u@example.com"}, "a@example.com", "s", "<p>b</p>", True, None, ATT))
        atts = list(msgs[0].iter_attachments())
        assert len(atts) == 1 and atts[0].get_filename() == "deck.pdf" and atts[0].get_content() == PDF
        assert atts[0].get_content_type() == "application/pdf"

    def test_gmail_attachments(self, monkeypatch):
        raw = {}

        async def creds():
            return None

        async def cfg():
            return {"email": "me@example.com"}

        class Svc:
            def users(self):
                return self

            def messages(self):
                return self

            def send(self, userId, body):
                raw.update(body)
                return self

            def execute(self):
                return {"id": "1"}
        monkeypatch.setattr(gmail_send, "_creds", creds)
        monkeypatch.setattr(gmail_send, "get_config", cfg)
        monkeypatch.setattr(gmail_send, "build", lambda *a, **k: Svc())
        r = run(gmail_send.send_email("a@example.com", "s", "<p>b</p>", html=True, attachments=ATT))
        assert r["ok"] is True, r
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw["raw"]))
        parts = [p for p in msg.walk() if p.get_filename()]
        assert parts and parts[0].get_filename() == "deck.pdf" and parts[0].get_payload(decode=True) == PDF
        assert parts[0].get_content_type() == "application/pdf"
