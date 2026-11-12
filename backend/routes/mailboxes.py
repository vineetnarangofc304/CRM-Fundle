"""Extra outreach inboxes (Office 365 SMTP): add, test/verify, edit, remove; plus today's usage."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import mailboxes as mb, outlook_send as ol, outreach as oc
from core.db import db
from core.security import require_roles
from core.utils import next_id, now_utc_str

router = APIRouter(tags=["mailboxes"])
ADMIN = require_roles("admin")


class Box(BaseModel):
    smtp_user: str
    smtp_password: Optional[str] = None
    sender_name: str = "Fundle.ai"
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587
    daily_cap: int = 200
    active: bool = True


def _public(b: dict) -> dict:
    return {k: v for k, v in b.items() if k not in ("_id", "smtp_password_enc")} | {"has_password": bool(b.get("smtp_password_enc"))}


def _check(body: Box):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", body.smtp_user.strip()):
        raise HTTPException(status_code=400, detail="Enter a valid inbox email")
    if body.smtp_port not in (25, 465, 587):
        raise HTTPException(status_code=400, detail="SMTP port must be 587, 465 or 25")
    if not 1 <= body.daily_cap <= 1000:
        raise HTTPException(status_code=400, detail="Daily limit must be between 1 and 1000")


@router.get("/admin/mailboxes")
async def list_boxes(user: dict = Depends(require_roles("admin", "manager"))):
    st = await oc.get_settings()
    day = oc._now()[:10]
    sent = await mb._sent_today(day)
    boxes = [_public(b) | {"sent_today": sent.get(b["id"], 0)}
             async for b in db.mailboxes.find({"is_deleted": {"$ne": True}}).sort("id", 1)]
    primary = next((s for s in await mb.senders(st, day) if s["id"] == mb.PRIMARY), None)
    return {"primary": primary and {"email": primary["email"], "daily_cap": st["daily_cap"], "sent_today": sent.get(mb.PRIMARY, 0)},
            "boxes": boxes, "per_inbox_cap": st["daily_cap"]}


@router.post("/admin/mailboxes")
async def add_box(body: Box, user: dict = Depends(ADMIN)):
    _check(body)
    email = body.smtp_user.strip().lower()
    if not body.smtp_password:
        raise HTTPException(status_code=400, detail="Password is required")
    if await db.mailboxes.find_one({"smtp_user": email, "is_deleted": {"$ne": True}}):
        raise HTTPException(status_code=409, detail="This inbox is already added")
    doc = {"id": await next_id("mailbox"), "smtp_user": email, "smtp_password_enc": ol.enc(body.smtp_password.strip()),
           "sender_name": body.sender_name.strip() or "Fundle.ai", "smtp_host": body.smtp_host.strip(),
           "smtp_port": body.smtp_port, "daily_cap": body.daily_cap, "active": body.active, "verified": False,
           "is_deleted": False, "created_at": now_utc_str(), "created_by": user["name"]}
    await db.mailboxes.insert_one(dict(doc))
    return _public(doc)


@router.put("/admin/mailboxes/{bid}")
async def edit_box(bid: int, body: Box, user: dict = Depends(ADMIN)):
    _check(body)
    cur = await db.mailboxes.find_one({"id": bid, "is_deleted": {"$ne": True}})
    if not cur:
        raise HTTPException(status_code=404, detail="Inbox not found")
    upd = {"sender_name": body.sender_name.strip() or "Fundle.ai", "smtp_host": body.smtp_host.strip(),
           "smtp_port": body.smtp_port, "daily_cap": body.daily_cap, "active": body.active,
           "smtp_user": body.smtp_user.strip().lower(), "updated_at": now_utc_str()}
    if body.smtp_password or (upd["smtp_user"], upd["smtp_host"], upd["smtp_port"]) != (cur["smtp_user"], cur["smtp_host"], cur["smtp_port"]):
        upd["verified"] = False
    if body.smtp_password:
        upd["smtp_password_enc"] = ol.enc(body.smtp_password.strip())
    await db.mailboxes.update_one({"id": bid}, {"$set": upd})
    return _public(await db.mailboxes.find_one({"id": bid}))


@router.delete("/admin/mailboxes/{bid}")
async def remove_box(bid: int, user: dict = Depends(ADMIN)):
    r = await db.mailboxes.update_one({"id": bid, "is_deleted": {"$ne": True}},
                                      {"$set": {"is_deleted": True, "active": False, "deleted_by": user["name"]}})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Inbox not found")
    return {"ok": True}


class TestTo(BaseModel):
    to: str


@router.post("/admin/mailboxes/{bid}/test")
async def test_box(bid: int, body: TestTo, user: dict = Depends(ADMIN)):
    b = await db.mailboxes.find_one({"id": bid, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Inbox not found")
    cfg = {**b, "method": "smtp", "from_email": b["smtp_user"]}
    res = await ol.send_with_config(cfg, body.to.strip(), "Fundle.ai CRM — inbox test",
                                    f"<p>Test email from <b>{b['smtp_user']}</b> via Fundle.ai CRM. This inbox is ready for outreach.</p>",
                                    html=True)
    await db.mailboxes.update_one({"id": bid}, {"$set": {"verified": bool(res.get("ok")), "last_error": res.get("error")}})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return {"ok": True}
