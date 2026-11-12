"""Multiple outreach sending inboxes: primary (Admin → Email provider) + extra Office 365 SMTP inboxes.
Outreach emails take turns across inboxes, each with its own daily limit; a brand sticks to one inbox."""
from core.db import db
from core import mailer, outlook_send as ol

PRIMARY = 0


async def _sent_today(day: str) -> dict:
    rows = await db.outreach.aggregate([
        {"$match": {"status": "sent", "channel": "email", "sent_at": {"$gte": day}}},
        {"$group": {"_id": {"$ifNull": ["$sent_via", PRIMARY]}, "n": {"$sum": 1}}}]).to_list(100)
    return {r["_id"]: r["n"] for r in rows}


async def senders(st: dict, day: str) -> list:
    """Usable inboxes with today's remaining quota: [{id, email, remaining, cfg}]."""
    sent = await _sent_today(day)
    out = []
    if await mailer.is_connected():
        cfg = await ol.get_config()
        out.append({"id": PRIMARY, "email": cfg.get("email") or cfg.get("smtp_user") or "Primary inbox",
                    "remaining": st["daily_cap"] - sent.get(PRIMARY, 0), "cfg": None})
    async for b in db.mailboxes.find({"active": True, "verified": True, "is_deleted": {"$ne": True}}, {"_id": 0}):
        out.append({"id": b["id"], "email": b["smtp_user"], "remaining": int(b.get("daily_cap") or 200) - sent.get(b["id"], 0),
                    "cfg": {**b, "method": "smtp", "from_email": b.get("from_email") or b["smtp_user"]}})
    return out


async def pick(prospect_id, pool: list, force: bool = False):
    """Sticky per brand; otherwise the inbox with the most room left today."""
    avail = [s for s in pool if force or s["remaining"] > 0]
    if not avail:
        return None
    p = await db.prospects.find_one({"id": prospect_id}, {"sender_mailbox": 1}) if prospect_id else None
    sticky = next((s for s in avail if p and s["id"] == p.get("sender_mailbox") and (force or s["remaining"] > 0)), None)
    s = sticky or max(avail, key=lambda x: x["remaining"])
    cur = p.get("sender_mailbox") if p else None
    gone = cur is not None and cur != PRIMARY and not await db.mailboxes.find_one({"id": cur, "is_deleted": {"$ne": True}})
    if p is not None and (cur is None or gone):
        await db.prospects.update_one({"id": prospect_id}, {"$set": {"sender_mailbox": s["id"]}})
    return s


async def send(s: dict, to, subject, body, cc=None, attachments=None) -> dict:
    if s["cfg"] is None:
        return await mailer.send_email(to, subject, body, html=True, cc=cc, attachments=attachments)
    return await ol.send_with_config(s["cfg"], to, subject, body, html=True, cc=cc, attachments=attachments)
