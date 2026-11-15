import hmac
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core import li_agent as la
from core.db import db
from core.security import require_roles
from core.utils import now_utc_str
from routes.prospecting import _spawn

router = APIRouter(tags=["linkedin-agent"])
ADMIN = require_roles("admin")
STAFF = require_roles("admin", "manager")
SAFE = {"_id": 0, "li_at_enc": 0, "jsession_enc": 0, "password_enc": 0, "token_hash": 0}


@router.post("/cron/linkedin")
async def cron_linkedin(request: Request):
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    auth = request.headers.get("authorization", "")
    if not secret or not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    _spawn(la.run("cron"))
    return {"ok": True, "queued": True}


class Account(BaseModel):
    name: Optional[str] = None
    li_at: Optional[str] = None
    jsessionid: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None


@router.get("/linkedin-agent/overview")
async def overview(user: dict = Depends(STAFF)):
    accs = await db.li_accounts.find({"is_deleted": {"$ne": True}}, SAFE).sort("id", 1).to_list(50)
    for a in accs:
        a["today"] = {k: await la.used_today(a["id"], k) for k in ("lookup", "invite", "message", "search")}
        a["import"] = await db.settings.find_one({"key": f"li_import_{a['id']}"}, {"_id": 0, "key": 0})
    cnt = lambda m: db.prospects.aggregate([{"$unwind": "$contacts"}, {"$match": m}, {"$count": "n"}]).to_list(1)  # noqa: E731
    totals = {}
    for k, m in {"contacts": {"contacts.name": {"$nin": [None, ""]}}, "checked": {"contacts.li_checked_at": {"$exists": True}},
                 "found": {"contacts.li_verified": True}, "first_degree": {"contacts.li_degree": 1},
                 "invited": {"contacts.li_invited_at": {"$exists": True}}, "accepted": {"contacts.li_accepted_at": {"$exists": True}},
                 "messaged": {"contacts.li_msg_at": {"$exists": True}}}.items():
        r = await cnt(m)
        totals[k] = r[0]["n"] if r else 0
    return {"accounts": accs, "settings": await la.get_settings(), "totals": totals, "regions": list(la.REGIONS),
            "last": (await db.settings.find_one({"key": "li_agent_last"}, {"_id": 0})) or {}}


@router.post("/linkedin-agent/accounts")
async def add_account(b: Account, user: dict = Depends(ADMIN)):
    try:
        doc = await la.add_account(b.model_dump(), user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**{k: v for k, v in doc.items() if k not in SAFE}, "test": await la.test(doc)}


@router.post("/linkedin-agent/accounts/{aid}/import-connections")
async def import_conns(aid: int, user: dict = Depends(STAFF)):
    a = await db.li_accounts.find_one({"id": aid, "is_deleted": {"$ne": True}, "type": {"$ne": "extension"}}, {"_id": 0})
    if not a or not a.get("verified"):
        raise HTTPException(status_code=400, detail="This account isn't connected — reconnect it (or use the Chrome extension's Import button)")
    _spawn(la.import_connections(a))
    return {"ok": True, "queued": True}


@router.post("/linkedin-agent/accounts/{aid}/test")
async def test_account(aid: int, user: dict = Depends(ADMIN)):
    a = await db.li_accounts.find_one({"id": aid, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Account not found")
    return await la.test(a)


class Toggle(BaseModel):
    active: bool


@router.put("/linkedin-agent/accounts/{aid}")
async def toggle_account(aid: int, b: Toggle, user: dict = Depends(ADMIN)):
    await db.li_accounts.update_one({"id": aid}, {"$set": {"active": b.active, "updated_at": now_utc_str()}})
    return {"ok": True}


@router.delete("/linkedin-agent/accounts/{aid}")
async def delete_account(aid: int, user: dict = Depends(ADMIN)):
    await db.li_accounts.update_one({"id": aid}, {"$set": {"is_deleted": True, "active": False, "li_at_enc": None, "jsession_enc": None,
                                                           "password_enc": None, "deleted_by": user["name"]}})
    return {"ok": True}


class Settings(BaseModel):
    lookup_on: Optional[bool] = None
    invite_on: Optional[bool] = None
    message_on: Optional[bool] = None
    daily_lookups: Optional[int] = None
    daily_invites: Optional[int] = None
    daily_messages: Optional[int] = None
    invite_note: Optional[str] = None
    message: Optional[str] = None


@router.put("/linkedin-agent/settings")
async def save_settings(b: Settings, user: dict = Depends(ADMIN)):
    d = {k: v for k, v in b.model_dump().items() if v is not None}
    for k, hi in (("daily_lookups", 300), ("daily_invites", 100), ("daily_messages", 150)):
        if k in d:
            d[k] = max(0, min(int(d[k]), hi))
    if len(d.get("invite_note") or "") > 300:
        raise HTTPException(status_code=400, detail="LinkedIn invite notes are limited to 300 characters")
    await db.settings.update_one({"key": "li_agent"}, {"$set": d}, upsert=True)
    return await la.get_settings()


class Search(BaseModel):
    keywords: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    regions: List[str] = []
    network: List[str] = []
    page: int = 1


@router.post("/linkedin-agent/search")
async def search(b: Search, user: dict = Depends(STAFF)):
    accs = await la.accounts()
    if not accs:
        raise HTTPException(status_code=400, detail="Connect a LinkedIn account first (LinkedIn Agent → Accounts)")
    if not (b.keywords or b.title or b.company):
        raise HTTPException(status_code=400, detail="Enter keywords, a title or a company")
    try:
        rs = await la.search(accs[0], b.keywords, b.title, b.company, b.regions, [n for n in b.network if n in ("F", "S", "O")],
                             limit=25, offset=(max(b.page, 1) - 1) * 25)
    except Exception as e:
        await la._fail(accs[0], e)
        raise HTTPException(status_code=502, detail=f"LinkedIn search failed: {str(e)[:200]}")
    known = {x["li_urn"] async for x in db.prospects.aggregate([{"$unwind": "$contacts"}, {"$match": {"contacts.li_urn": {"$in": [r["urn_id"] for r in rs]}}},
                                                                {"$project": {"_id": 0, "li_urn": "$contacts.li_urn"}}])}
    return {"items": [{**r, "in_crm": r["urn_id"] in known} for r in rs], "page": b.page, "account": accs[0]["name"]}


class Person(BaseModel):
    name: str
    urn_id: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    degree: Optional[int] = None
    linkedin: Optional[str] = None
    company: Optional[str] = None
    country: Optional[str] = None


class Save(BaseModel):
    people: List[Person]


@router.post("/linkedin-agent/save")
async def save(b: Save, user: dict = Depends(STAFF)):
    return await la.save_people([p.model_dump() for p in b.people], user)


@router.post("/linkedin-agent/run")
async def run_now(user: dict = Depends(ADMIN)):
    _spawn(la.run("manual"))
    return {"ok": True, "queued": True}


# ---------- Chrome extension ----------
EXT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "linkedin-extension")


class ExtKey(BaseModel):
    name: Optional[str] = None


@router.post("/linkedin-agent/ext/token")
async def ext_token(b: ExtKey, user: dict = Depends(STAFF)):
    from core import li_ext
    return await li_ext.new_token(b.name or user["name"], user)


@router.get("/linkedin-agent/extension.zip")
async def ext_zip(user: dict = Depends(STAFF)):
    import io
    import zipfile
    from fastapi.responses import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(EXT_DIR):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join("fundle-linkedin", os.path.relpath(full, EXT_DIR)))
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="fundle-linkedin-extension.zip"'})


async def ext_acc(request: Request):
    from core import li_ext
    acc = await li_ext.account_for(request.headers.get("x-ext-token", ""))
    if not acc:
        raise HTTPException(status_code=401, detail="Invalid or revoked extension key — create a new one in LinkedIn Prospecting → Chrome extension")
    return acc


@router.get("/ext/ping")
async def ext_ping(acc: dict = Depends(ext_acc)):
    st = await la.get_settings()
    await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"last_seen": now_utc_str()}})
    return {"name": acc["name"], "today": {k: await la.used_today(acc["id"], k) for k in ("lookup", "invite", "message")},
            "limits": {"lookup": st["daily_lookups"] if st["lookup_on"] else 0, "invite": st["daily_invites"] if st["invite_on"] else 0,
                       "message": st["daily_messages"] if st["message_on"] else 0}}


@router.get("/ext/task")
async def ext_task(acc: dict = Depends(ext_acc)):
    from core import li_ext
    await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"last_seen": now_utc_str()}})
    return {"task": await li_ext.next_task(acc)}


class ExtResult(BaseModel):
    kind: str
    prospect_id: int
    name: str
    ok: bool
    error: Optional[str] = None
    text: Optional[str] = None
    results: List[dict] = []


@router.post("/ext/result")
async def ext_result(b: ExtResult, acc: dict = Depends(ext_acc)):
    from core import li_ext
    if b.kind not in ("lookup", "invite", "message"):
        raise HTTPException(status_code=400, detail="Unknown task kind")
    return await li_ext.result(acc, b.model_dump())


@router.post("/ext/save")
async def ext_save(b: Save, acc: dict = Depends(ext_acc)):
    return await la.save_people([p.model_dump() for p in b.people], {"name": acc["name"]})
