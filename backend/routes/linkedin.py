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


@router.post("/run")
async def run_now(user: dict = Depends(STAFF)):
    _spawn(li.build_queue(f"manual:{user['name']}"))
    return {"ok": True}
