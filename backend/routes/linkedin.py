"""LinkedIn one-click queue: today's connects/messages, mark sent/accepted/replied, settings."""
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.db import db
from core.security import require_roles
from core.utils import log_message, now_utc_str
from core import linkedin as li
from routes.prospecting import _spawn

router = APIRouter(prefix="/linkedin", tags=["linkedin"])
STAFF = require_roles("admin", "manager")


@router.get("/queue")
async def queue(view: str = "today", user: dict = Depends(STAFF)):
    st = await li.get_settings()
    left = {k: max(0, st[f"daily_{k}s"] - await li.sent_today(k)) for k in ("connect", "message")}
    if view == "today":
        items = []
        for k in ("message", "connect"):
            items += await db.linkedin_tasks.find({"kind": k, "status": "queued"}, {"_id": 0}
                                                  ).sort([("created_at", 1)]).limit(left[k]).to_list(left[k])
    else:
        f = {"sent": {"status": "sent"}, "accepted": {"status": "accepted"}, "replied": {"status": "replied"},
             "skipped": {"status": "skipped"}}.get(view)
        if f is None:
            raise HTTPException(status_code=400, detail="Unknown view")
        items = await db.linkedin_tasks.find(f, {"_id": 0}).sort([("sent_at", -1), ("id", -1)]).limit(200).to_list(200)
    rows = await db.linkedin_tasks.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]).to_list(20)
    last = await db.settings.find_one({"key": "linkedin_last"}, {"_id": 0, "key": 0}) or {}
    return {"items": items, "left": left, "counts": {r["_id"]: r["n"] for r in rows}, "settings": st, "last_run": last}


async def _get(tid: int, statuses: list) -> dict:
    t = await db.linkedin_tasks.find_one({"id": tid, "status": {"$in": statuses}}, {"_id": 0})
    if not t:
        raise HTTPException(status_code=409, detail="This LinkedIn task has already moved on")
    return t


async def _log(t: dict, text: str, user: dict):
    if t.get("lead_id"):
        await log_message(t["lead_id"], text, author=user)


class Body(BaseModel):
    body: str


@router.patch("/tasks/{tid}")
async def edit(tid: int, b: Body, user: dict = Depends(STAFF)):
    await _get(tid, ["queued"])
    await db.linkedin_tasks.update_one({"id": tid}, {"$set": {"body": b.body.strip()[:1500], "edited_by": user["name"]}})
    return {"ok": True}


@router.post("/tasks/{tid}/sent")
async def mark_sent(tid: int, user: dict = Depends(STAFF)):
    t = await _get(tid, ["queued"])
    left = (await li.get_settings())[f"daily_{t['kind']}s"] - await li.sent_today(t["kind"])
    if left <= 0:
        raise HTTPException(status_code=429, detail=f"Today's LinkedIn {t['kind']} limit is reached — keeps your account safe")
    await db.linkedin_tasks.update_one({"id": tid}, {"$set": {"status": "sent", "sent_at": now_utc_str(), "sent_by": user["name"]}})
    if t["kind"] == "connect":
        await db.prospects.update_one({"id": t["prospect_id"]}, {"$set": {"li_task": "sent"}})
    what = "connection request" if t["kind"] == "connect" else "message"
    await _log(t, f"🔗 LinkedIn {what} sent to {t['contact_name']} by {user['name']}: <i>{escape(t['body'])}</i>", user)
    return {"ok": True}


@router.post("/tasks/{tid}/accepted")
async def accepted(tid: int, user: dict = Depends(STAFF)):
    t = await _get(tid, ["sent"])
    if t["kind"] != "connect":
        raise HTTPException(status_code=400, detail="Only connection requests can be accepted")
    await db.linkedin_tasks.update_one({"id": tid}, {"$set": {"status": "accepted", "accepted_at": now_utc_str()}})
    await db.prospects.update_one({"id": t["prospect_id"]}, {"$set": {"li_task": "accepted"}})
    await _log(t, f"🤝 {t['contact_name']} accepted the LinkedIn connection", user)
    return await li.new_message(t, await li.draft_message(t))


class Reply(BaseModel):
    text: str


@router.post("/tasks/{tid}/reply")
async def reply(tid: int, b: Reply, user: dict = Depends(STAFF)):
    text = b.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste the reply text first")
    t = await _get(tid, ["sent", "accepted"])
    await db.linkedin_tasks.update_one({"id": tid}, {"$set": {"status": "replied", "replied_at": now_utc_str(), "reply_text": text[:4000]}})
    await db.prospects.update_one({"id": t["prospect_id"]}, {"$set": {"li_task": "replied"}})
    await db.linkedin_tasks.update_many({"prospect_id": t["prospect_id"], "status": "queued", "kind": "message"},
                                        {"$set": {"status": "skipped", "skip_reason": "They replied"}})
    await _log(t, f"💬 LinkedIn reply from {t['contact_name']}: <i>{escape(text[:4000])}</i>", user)
    return await li.new_message(t, await li.draft_message(t, text), reply_to=text[:4000])


@router.post("/tasks/{tid}/skip")
async def skip(tid: int, user: dict = Depends(STAFF)):
    t = await _get(tid, ["queued"])
    await db.linkedin_tasks.update_one({"id": tid}, {"$set": {"status": "skipped", "skip_reason": f"Skipped by {user['name']}"}})
    return {"ok": True, "id": t["id"]}


class Settings(BaseModel):
    enabled: bool = True
    daily_connects: int = 20
    daily_messages: int = 50
    drafts_per_run: Optional[int] = None


@router.put("/settings")
async def save_settings(b: Settings, user: dict = Depends(require_roles("admin"))):
    data = {"enabled": b.enabled, "daily_connects": max(0, min(b.daily_connects, 40)),
            "daily_messages": max(0, min(b.daily_messages, 100))}
    if b.drafts_per_run is not None:
        data["drafts_per_run"] = max(0, min(b.drafts_per_run, 40))
    await db.settings.update_one({"key": "linkedin"}, {"$set": data}, upsert=True)
    return await li.get_settings()


@router.get("/lead/{lead_id}/contacts")
async def lead_contacts(lead_id: int, user: dict = Depends(STAFF)):
    p = await db.prospects.find_one({"lead_id": lead_id}, {"_id": 0, "contacts": 1, "li_lookup_at": 1, "brand_name": 1})
    cs = [{k: c.get(k) for k in ("name", "role", "linkedin", "email", "uploaded", "li_verified", "li_degree")} for c in (p or {}).get("contacts") or []]
    return {"contacts": cs, "searched": bool((p or {}).get("li_lookup_at")), "brand": (p or {}).get("brand_name")}


DEFAULT_LI_MSG = ("Hi {first_name}, came across {brand} and loved how you've grown your stores. At Fundle we help retail brands "
                  "drive repeat purchases with loyalty across stores and online. Open to a quick chat?")
REPORT_PROJ = {"_id": 0, "id": 1, "brand_name": 1, "store_count": 1, "category": 1, "lead_id": 1, "country": 1, "contacts": 1}


@router.get("/report")
async def report(q: Optional[str] = None, status: str = "all", source: str = "all", filter: Optional[str] = None, page: int = 1, limit: int = 50,
                 user: dict = Depends(STAFF)):
    import re
    m = {"contacts.name": {"$nin": [None, ""]}}
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        m = {**m, "$or": [{"brand_name": rx}, {"contacts.name": rx}, {"contacts.role": rx}]}
    cm = {"contacts.name": {"$nin": [None, ""]}}
    if q:
        cm["$or"] = [{"brand_name": rx}, {"contacts.name": rx}, {"contacts.role": rx}]
    if status == "sent":
        cm["contacts.li_sent_at"] = {"$nin": [None, ""]}
    elif status == "todo":
        cm["contacts.li_sent_at"] = {"$in": [None, ""]}
    if source == "sheet":
        cm["contacts.uploaded"] = True
    elif source in ("li_server", "li_ext", "li_search"):
        cm["contacts.li_source"] = source
    elif source == "any_li":
        cm["contacts.li_source"] = {"$exists": True}
    stat = {"checked": {"contacts.li_checked_at": {"$exists": True}}, "found": {"contacts.li_verified": True},
            "first_degree": {"contacts.li_degree": 1}, "invited": {"contacts.li_invited_at": {"$exists": True}},
            "accepted": {"contacts.li_accepted_at": {"$exists": True}}, "messaged": {"contacts.li_msg_at": {"$exists": True}}}.get(filter or "")
    if stat:
        cm.update(stat)
    limit = max(1, min(limit, 200))
    pipe = [{"$match": m}, {"$project": REPORT_PROJ}, {"$unwind": "$contacts"}, {"$match": cm},
            {"$sort": {"contacts.uploaded": -1, "store_count": -1, "id": 1}},
            {"$facet": {"items": [{"$skip": (max(page, 1) - 1) * limit}, {"$limit": limit}], "total": [{"$count": "n"}]}}]
    r = (await db.prospects.aggregate(pipe, allowDiskUse=True).to_list(1))[0]
    items = [{"prospect_id": x["id"], "brand": x["brand_name"], "store_count": x.get("store_count"), "category": x.get("category"),
              "lead_id": x.get("lead_id"), "country": x.get("country"),
              **{k: x["contacts"].get(k) for k in ("name", "role", "email", "linkedin", "uploaded", "li_verified", "li_degree", "li_invited_at", "li_sent_at", "li_sent_by", "li_source", "li_source_by", "li_msg_at")}}
             for x in r["items"]]
    tpl = await db.settings.find_one({"key": "li_report_msg"}, {"_id": 0})
    return {"items": items, "total": (r["total"] or [{"n": 0}])[0]["n"], "page": page, "limit": limit,
            "template": (tpl or {}).get("text") or DEFAULT_LI_MSG}


class SentMark(BaseModel):
    prospect_id: int
    name: str
    sent: bool = True


@router.post("/report/sent")
async def report_mark_sent(b: SentMark, user: dict = Depends(STAFF)):
    val = {"contacts.$[c].li_sent_at": now_utc_str(), "contacts.$[c].li_sent_by": user["name"]} if b.sent \
        else {"contacts.$[c].li_sent_at": None, "contacts.$[c].li_sent_by": None}
    r = await db.prospects.update_one({"id": b.prospect_id, "contacts.name": b.name}, {"$set": val}, array_filters=[{"c.name": b.name}])
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Contact not found")
    p = await db.prospects.find_one({"id": b.prospect_id}, {"_id": 0, "lead_id": 1})
    if p and p.get("lead_id"):
        await log_message(p["lead_id"], f"💬 LinkedIn message {'sent to' if b.sent else 'un-marked for'} {escape(b.name)} by {escape(user['name'])}", author=user)
    return {"ok": True}


class LiTemplate(BaseModel):
    text: str


@router.put("/report/template")
async def report_save_template(b: LiTemplate, user: dict = Depends(STAFF)):
    await db.settings.update_one({"key": "li_report_msg"}, {"$set": {"text": b.text.strip()[:1500]}}, upsert=True)
    return {"ok": True}


@router.post("/run")
async def run_now(user: dict = Depends(STAFF)):
    _spawn(li.build_queue(f"manual:{user['name']}"))
    return {"ok": True}
