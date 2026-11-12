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
    await oc._migrate_v4()
    st = await oc.get_settings()
    day = oc._now()[:10]
    sent = await mb._sent_today(day)
    boxes = [_public(b) | {"sent_today": sent.get(b["id"], 0), "today_cap": mb.warm_cap(b, day)}
             async for b in db.mailboxes.find({"is_deleted": {"$ne": True}}).sort("id", 1)]
    primary = next((s for s in await mb.senders(st, day) if s["id"] == mb.PRIMARY), None)
    readers = {r["mailbox_id"]: {"email": r.get("email"), "last_sync": r.get("last_sync"), "error": r.get("error")}
               async for r in db.inbox_readers.find({}, {"_id": 0, "refresh_token_enc": 0})}
    for b in boxes:
        b["reader"] = readers.get(b["id"])
    return {"primary": primary and {"email": primary["email"], "daily_cap": st["daily_cap"], "sent_today": sent.get(mb.PRIMARY, 0),
                                    "reader": readers.get(mb.PRIMARY)},
            "boxes": boxes, "per_inbox_cap": st["daily_cap"]}


@router.get("/admin/mailboxes/report")
async def report(user: dict = Depends(require_roles("admin", "manager"))):
    """Per inbox: sent / failed / replies / bounces, today and last 7 days."""
    from datetime import datetime, timedelta, timezone
    today = oc._now()[:10]
    week = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")

    async def count(field_via, field_at, extra):
        rows = await db.outreach.aggregate([
            {"$match": {"channel": "email", field_at: {"$gte": week}, **extra}},
            {"$group": {"_id": {"via": {"$ifNull": [f"${field_via}", mb.PRIMARY]}, "today": {"$gte": [f"${field_at}", today]}},
                        "n": {"$sum": 1}}}]).to_list(200)
        out = {}
        for r in rows:
            d = out.setdefault(r["_id"]["via"], {"today": 0, "week": 0})
            d["week"] += r["n"]
            if r["_id"]["today"]:
                d["today"] += r["n"]
        return out
    sent = await count("sent_via", "sent_at", {"sent_at": {"$gte": week}})
    failed = await count("failed_via", "failed_at", {"failed_at": {"$gte": week}})
    replies = await count("sent_via", "replied_at", {"replied_at": {"$gte": week}})
    bounces = await count("sent_via", "bounced_at", {"bounced_at": {"$gte": week}})
    names = {mb.PRIMARY: "Main inbox"} | {b["id"]: b["smtp_user"] async for b in db.mailboxes.find({}, {"id": 1, "smtp_user": 1})}
    ids = sorted(set(sent) | set(failed) | set(replies) | set(bounces) | {mb.PRIMARY}
                 | {b["id"] async for b in db.mailboxes.find({"is_deleted": {"$ne": True}}, {"id": 1})})
    z = {"today": 0, "week": 0}
    return [{"id": i, "inbox": names.get(i, f"Inbox {i}"), "sent": sent.get(i, z), "failed": failed.get(i, z),
             "replies": replies.get(i, z), "bounces": bounces.get(i, z)} for i in ids]


@router.get("/admin/mailboxes/{bid}/read-auth-url")
async def read_auth_url(bid: int, origin: str, user: dict = Depends(ADMIN)):
    """Microsoft sign-in so the CRM can read replies and bounces in this inbox (bid 0 = main inbox)."""
    import secrets
    from core import inbox_sync
    cfg = await ol.get_config()
    if not cfg.get("client_id") or not cfg.get("client_secret_enc"):
        raise HTTPException(status_code=400, detail="First save the Microsoft app (client) ID and secret in the Outlook card above, "
                                                    "with the Mail.Read permission added")
    hint = ""
    if bid:
        b = await db.mailboxes.find_one({"id": bid, "is_deleted": {"$ne": True}})
        if not b:
            raise HTTPException(status_code=404, detail="Inbox not found")
        hint = b["smtp_user"]
    redirect = f"{origin.rstrip('/')}/api/oauth/outlook/read-callback"
    state = secrets.token_urlsafe(24)
    await db.oauth_states.insert_one({"provider": "outlook_read", "state": state, "mailbox_id": bid, "redirect": redirect,
                                      "user_id": user["id"], "created_at": now_utc_str()})
    return {"url": inbox_sync.auth_url(cfg, redirect, state, hint)}


@router.get("/oauth/outlook/read-callback")
async def read_callback(code: str = None, state: str = None, error_description: str = None):
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    from core import inbox_sync
    st = await db.oauth_states.find_one({"provider": "outlook_read", "state": state}) if state else None
    if not st:
        return RedirectResponse("/admin?tab=Email&inboxread=badstate")
    await db.oauth_states.delete_one({"_id": st["_id"]})
    base = st["redirect"].split("/api/")[0]
    if not code:
        return RedirectResponse(f"{base}/admin?tab=Email&inboxread=error&reason={quote((error_description or 'cancelled')[:160], safe='')}")
    try:
        email = await inbox_sync.connect(code, st["redirect"], st["mailbox_id"])
    except Exception as e:
        return RedirectResponse(f"{base}/admin?tab=Email&inboxread=error&reason={quote(str(e)[:160], safe='')}")
    return RedirectResponse(f"{base}/admin?tab=Email&inboxread=connected&reason={quote(email, safe='')}")


@router.post("/admin/mailboxes/sync-replies")
async def sync_replies(user: dict = Depends(ADMIN)):
    from core import inbox_sync
    return await inbox_sync.sync_all()


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
    if upd["smtp_user"] != cur["smtp_user"] and await db.mailboxes.find_one(
            {"smtp_user": upd["smtp_user"], "is_deleted": {"$ne": True}, "id": {"$ne": bid}}):
        raise HTTPException(status_code=409, detail="This inbox is already added")
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
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", body.to.strip()):
        raise HTTPException(status_code=400, detail="Enter a valid email to send the test to")
    cfg = {**b, "method": "smtp", "from_email": b["smtp_user"]}
    res = await ol.send_with_config(cfg, body.to.strip(), "Fundle.ai CRM — inbox test",
                                    f"<p>Test email from <b>{b['smtp_user']}</b> via Fundle.ai CRM. This inbox is ready for outreach.</p>",
                                    html=True)
    upd = {"verified": bool(res.get("ok")), "last_error": res.get("error")}
    if res.get("ok") and not b.get("verified_at"):
        upd["verified_at"] = now_utc_str()
    await db.mailboxes.update_one({"id": bid}, {"$set": upd})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return {"ok": True}
