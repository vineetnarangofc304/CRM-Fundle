"""Outreach approval queue, settings, manual triggers, cron + prospect contact/draft actions."""
import hmac
import os
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.db import db
from core.security import require_roles
from core import contacts as ct, outreach as oc
from routes.prospecting import _spawn

router = APIRouter(tags=["outreach"])
STAFF = require_roles("admin", "manager")


@router.post("/cron/outreach")
async def cron_outreach(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    auth = request.headers.get("authorization", "")
    if not secret or not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    _spawn(oc.run_outreach("cron"))
    return {"ok": True, "queued": True}


class OutreachSettings(BaseModel):
    enabled: bool = True
    min_score: int = 0
    daily_cap: int = 100
    drafts_per_run: int = 30
    auto_send: bool = True
    ai_sequence: bool = False
    cc: str = ""
    gaps: List[int] = [3, 7]
    pitch: str = oc.DEFAULT_PITCH
    signature: Optional[str] = None


@router.get("/outreach/settings")
async def get_settings(user: dict = Depends(STAFF)):
    from core import mailer
    await oc._migrate_v5()
    last = await db.settings.find_one({"key": "outreach_last"}, {"_id": 0, "key": 0}) or {}
    return {**await oc.get_settings(), "mail_connected": await mailer.is_connected(),
            "mail_provider": await mailer.active_provider(), "last_run": last}


@router.put("/outreach/settings")
async def save_settings(body: OutreachSettings, user: dict = Depends(require_roles("admin"))):
    if len(body.gaps) != 2 or any(g < 1 or g > 30 for g in body.gaps):
        raise HTTPException(status_code=400, detail="Follow-up gaps must be 1-30 days")
    data = body.model_dump()
    if data["signature"] is None:
        data.pop("signature")
    data["cc"] = body.cc.strip()
    if data["cc"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", data["cc"]):
        raise HTTPException(status_code=400, detail="Enter a valid CC email address")
    data.update(min_score=max(0, min(body.min_score, 100)), daily_cap=max(1, min(body.daily_cap, 300)),
                drafts_per_run=max(0, min(body.drafts_per_run, 50)))
    await db.settings.update_one({"key": "outreach"}, {"$set": data}, upsert=True)
    return await oc.get_settings()


class Signature(BaseModel):
    signature: str = ""


@router.put("/outreach/signature")
async def save_signature(body: Signature, user: dict = Depends(require_roles("admin"))):
    await db.settings.update_one({"key": "outreach"}, {"$set": {"signature": body.signature.strip()[:1000]}}, upsert=True)
    return {"signature": body.signature.strip()[:1000]}


@router.get("/outreach/stats")
async def stats(user: dict = Depends(STAFF)):
    rows = await db.outreach.aggregate([{"$group": {"_id": {"s": "$status", "c": "$channel"}, "n": {"$sum": 1}}}]).to_list(50)
    out = {}
    for r in rows:
        out.setdefault(r["_id"]["c"], {})[r["_id"]["s"]] = r["n"]
    return out


@router.get("/outreach/messages")
async def messages(status: Optional[str] = None, channel: Optional[str] = None, page: int = 1, limit: int = 50,
                   user: dict = Depends(STAFF)):
    f = {k: v for k, v in (("status", status), ("channel", channel)) if v}
    limit = max(1, min(limit, 200))
    items = await db.outreach.find(f, {"_id": 0}).sort([("kind", -1), ("fit_score", -1), ("seq_id", 1), ("step", 1)]) \
        .skip((max(page, 1) - 1) * limit).limit(limit).to_list(limit)
    return {"items": items, "total": await db.outreach.count_documents(f)}


class Edit(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    to: Optional[str] = None


@router.patch("/outreach/messages/{mid}")
async def edit(mid: int, body: Edit, user: dict = Depends(STAFF)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "to" in upd and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", upd["to"].strip()):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if "body" in upd:
        upd["body"] = oc.clean_html(upd["body"])
    r = await db.outreach.update_one({"id": mid, "status": {"$in": ["pending_approval", "approved", "failed"]}},
                                     {"$set": {**upd, "edited_by": user["name"]}})
    if not r.matched_count:
        raise HTTPException(status_code=409, detail="Message already sent or stopped")
    return {"ok": True}


async def _approve(f: dict, user):
    msgs = await db.outreach.find({**f, "status": {"$in": ["pending_approval", "failed"]}}, {"_id": 0}).to_list(500)
    for m in msgs:
        upd = {"status": "approved", "approved_by": user["name"], "approved_at": oc._now()}
        if m["channel"] == "email" and m["step"] == 1:
            upd["scheduled_at"] = oc._now()
        elif m["channel"] == "email":
            prev = await db.outreach.find_one({"seq_id": m["seq_id"], "channel": "email", "step": m["step"] - 1})
            if prev and prev.get("status") == "sent" and not m.get("scheduled_at"):
                upd["scheduled_at"] = oc._now()
        await db.outreach.update_one({"id": m["id"]}, {"$set": upd})
    if msgs:
        await db.prospects.update_many({"outreach_seq": {"$in": list({m["seq_id"] for m in msgs})}}, {"$set": {"outreach_status": "approved"}})
    return {"approved": len(msgs)}


@router.post("/outreach/messages/{mid}/approve")
async def approve(mid: int, user: dict = Depends(STAFF)):
    return await _approve({"id": mid}, user)


@router.post("/outreach/sequences/{seq}/approve")
async def approve_seq(seq: int, user: dict = Depends(STAFF)):
    return await _approve({"seq_id": seq}, user)


class Ids(BaseModel):
    seq_ids: List[int]


@router.post("/outreach/bulk-approve")
async def bulk_approve(body: Ids, user: dict = Depends(STAFF)):
    return await _approve({"seq_id": {"$in": body.seq_ids[:200]}}, user)


@router.post("/outreach/messages/{mid}/reject")
async def reject(mid: int, user: dict = Depends(STAFF)):
    m = await db.outreach.find_one_and_update({"id": mid, "status": {"$in": ["pending_approval", "approved", "failed"]}},
                                              {"$set": {"status": "rejected", "rejected_by": user["name"]}})
    if not m:
        raise HTTPException(status_code=404, detail="Message not found or already sent/stopped")
    if m.get("template_id"):
        await db.prospects.update_one({"id": m["prospect_id"]}, {"$pull": {"tpl_sent": m["template_id"]}})
    return {"ok": True}


@router.post("/outreach/sequences/{seq}/stop")
async def stop_seq(seq: int, user: dict = Depends(STAFF)):
    r = await db.outreach.update_many({"seq_id": seq, "status": {"$in": ["pending_approval", "approved", "failed"]}},
                                      {"$set": {"status": "stopped", "stop_reason": f"Stopped by {user['name']}"}})
    await db.prospects.update_many({"outreach_seq": seq}, {"$set": {"outreach_status": "stopped"}})
    return {"stopped": r.modified_count}


@router.post("/outreach/messages/{mid}/mark-sent")
async def mark_sent(mid: int, user: dict = Depends(STAFF)):
    m = await db.outreach.find_one({"id": mid, "channel": "linkedin", "status": {"$in": ["pending_approval", "approved", "failed"]}}, {"_id": 0})
    if not m:
        raise HTTPException(status_code=404, detail="LinkedIn draft not found or already closed")
    await db.outreach.update_one({"id": mid}, {"$set": {"status": "sent", "sent_at": oc._now(), "sent_by": user["name"]}})
    if m.get("lead_id"):
        await oc.log_message(m["lead_id"], f"LinkedIn {m['subject'].lower()} sent to {m['contact_name']} by {user['name']}", author=user)
    return {"ok": True}


@router.post("/outreach/run")
async def run_now(user: dict = Depends(STAFF)):
    _spawn(oc.run_outreach(f"manual:{user['name']}"))
    return {"ok": True}


@router.post("/prospecting/prospects/{pid}/contacts")
async def find_contacts_now(pid: int, user: dict = Depends(STAFF)):
    p = await db.prospects.find_one_and_update({"id": pid, "contacts_status": {"$ne": "running"}},
                                               {"$set": {"contacts_status": "running", "contacts_started_at": oc._now()}},
                                               projection={"_id": 0})
    if not p:
        raise HTTPException(status_code=409, detail="Email search already running or prospect not found")
    _spawn(ct.find_contacts(p))
    return {"ok": True}


class DraftReq(BaseModel):
    contact_index: Optional[int] = None


@router.post("/prospecting/prospects/{pid}/draft")
async def draft_now(pid: int, body: DraftReq, user: dict = Depends(STAFF)):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")
    cs = p.get("contacts") or []
    c = cs[body.contact_index] if body.contact_index is not None and 0 <= body.contact_index < len(cs) else oc.pick_contact(p)
    if not c or not c.get("email"):
        raise HTTPException(status_code=400, detail="This contact has no email yet — run 'Find emails' first")
    old = p.get("outreach_seq")
    try:
        seq = await oc.draft_sequence(p, c, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if old:
        await db.outreach.update_many({"seq_id": old, "status": {"$in": ["pending_approval", "approved", "failed"]}},
                                      {"$set": {"status": "stopped", "stop_reason": "Replaced by new draft"}})
    return {"seq_id": seq}
