"""Bounce-tested emails: send the intro to the 3 likeliest address combinations, read the bounces,
keep the one that is delivered (no bounce in 6 h), drop the rest, otherwise try the next 3. No AI."""
import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

from core.db import db
from core.utils import log_message, now_utc_str

logger = logging.getLogger(__name__)
ORDER = ["first.last", "first", "firstlast", "flast", "f.last", "first_last", "firstl", "first.l",
         "last.first", "lastfirst", "last", "lastf", "first-last"]
PER_TRY, WAIT_HOURS = 3, 6
PROVEN = ("published", "delivered")
AGENT = {"id": 0, "name": "Email Verifier"}


def proven(c: dict) -> bool:
    return bool(c.get("email")) and c.get("email_status") in PROVEN


def targets(p: dict, c: dict) -> list:
    """Next up-to-3 untried, unbounced addresses for this contact (its current email first)."""
    from core import contacts as ct
    from core import email_format as ef
    dom = ct._domain(p)
    bad = {e.lower() for e in (p.get("bounced_emails") or []) + (c.get("tried_emails") or [])}
    fmt = (p.get("email_format") or {}).get("pattern")
    order = ([fmt] if fmt in ef.FORMATS else []) + ORDER
    out = [c["email"].lower()] if c.get("email") else []
    out += [ef.build(k, c.get("name") or "", dom) for k in order] if dom else []
    seen, res = set(), []
    for e in out:
        if e and e not in bad and e not in seen:
            seen.add(e)
            res.append(e)
    return res[:PER_TRY]


def pick_target_contact(p: dict):
    """Contact to email: a proven one if any, else the best-ranked named person (to bounce-test)."""
    from core.outreach import pick_contact
    bounced = {e.lower() for e in p.get("bounced_emails") or []}
    good = [c for c in p.get("contacts") or [] if proven(c) and c["email"].lower() not in bounced]
    if good:
        return pick_contact({**p, "contacts": good}), False
    cs = [c for c in p.get("contacts") or [] if c.get("name") and not c.get("no_valid_email") and targets(p, c)]
    if not cs:
        return None, False
    mk = [c for c in cs if re.search(r"market|cmo|crm|loyal|customer|digital", c.get("role") or "", re.I)]
    top = [c for c in cs if re.search(r"ceo|founder|director|owner|president", c.get("role") or "", re.I)]
    return (mk or top or cs)[0], True


async def queue_probe(t: dict, p: dict, c: dict) -> int:
    from core.email_templates import create_message
    addrs = targets(p, c)
    for a in addrs:
        doc = await create_message(t, p, {**c, "email": a, "email_status": "testing"}, "approved", "Auto-send")
        await db.outreach.update_one({"id": doc["id"]}, {"$set": {"probe": True}})
    if addrs:
        await db.prospects.update_one({"id": p["id"]}, {"$addToSet": {"contacts.$[c].tried_emails": {"$each": addrs}},
                                                        "$set": {"contacts.$[c].email_status": "testing"}},
                                      array_filters=[{"c.name": c["name"]}])
    return len(addrs)


async def finalize() -> dict:
    """Decide each tested contact: delivered (no bounce after 6 h) → keep one; all bounced → try next 3."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WAIT_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    read = {r.get("mailbox_id") async for r in db.inbox_readers.find({"refresh_token_enc": {"$exists": True}, "error": {"$exists": False}}, {"mailbox_id": 1})}
    read |= {b["id"] async for b in db.mailboxes.find({"active": True, "smtp_password_enc": {"$exists": True}, "imap_error": {"$exists": False}}, {"id": 1})}
    st = {"verified": 0, "retry": 0, "exhausted": 0, "waiting_for_inbox_reading": 0}
    groups = await db.outreach.aggregate([
        {"$match": {"probe": True, "probe_result": {"$exists": False}}},
        {"$group": {"_id": {"p": "$prospect_id", "c": "$contact_name"}, "msgs": {"$push": {
            "id": "$id", "to": "$to", "status": "$status", "sent_at": "$sent_at", "template_id": "$template_id", "via": "$sent_via"}}}}]).to_list(5000)
    for g in groups:
        ms = g["msgs"]
        if any(m["status"] not in ("sent", "bounced") for m in ms):
            if all(m["status"] in ("stopped", "failed") for m in ms):
                await db.outreach.update_many({"id": {"$in": [m["id"] for m in ms]}}, {"$set": {"probe_result": "cancelled"}})
            continue
        live = [m for m in ms if m["status"] == "sent"]
        if live and any((m.get("sent_at") or "") > cutoff for m in live):
            continue  # still inside the 6 h bounce window
        if any(m.get("via") not in read for m in live):
            st["waiting_for_inbox_reading"] += 1  # no bounce can be seen if the sending inbox isn't read — never assume delivered
            continue
        pid, name = g["_id"]["p"], g["_id"]["c"]
        p = await db.prospects.find_one({"id": pid}, {"_id": 0})
        if not p:
            continue
        c = next((x for x in p.get("contacts") or [] if x.get("name") == name), None)
        if live:
            win = live[0]["to"].lower()
            await db.outreach.update_one({"id": live[0]["id"]}, {"$set": {"probe_result": "delivered"}})
            await db.outreach.update_many({"id": {"$in": [m["id"] for m in ms if m["id"] != live[0]["id"]]}},
                                          {"$set": {"probe_result": "dropped"}})
            await db.prospects.update_one({"id": pid}, {"$set": {"contacts.$[c].email": win, "contacts.$[c].email_status": "delivered",
                                                                 "contacts.$[c].verified_at": now_utc_str()},
                                                        "$unset": {"contacts.$[c].tried_emails": 1}},
                                          array_filters=[{"c.name": name}])
            if p.get("lead_id"):
                await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"email_from": win, "email_lc": win}, "$unset": {"search_words": 1}})
                await log_message(p["lead_id"], f"✅ Email verified for {name}: <b>{win}</b> (delivered, no bounce in {WAIT_HOURS} h). "
                                                f"Other tested addresses removed.", author=AGENT)
            st["verified"] += 1
            continue
        await db.outreach.update_many({"id": {"$in": [m["id"] for m in ms]}}, {"$set": {"probe_result": "bounced"}})
        if c and targets(p, c):
            await db.prospects.update_one({"id": pid}, {"$pull": {"tpl_sent": {"$in": list({m["template_id"] for m in ms})}}})
            st["retry"] += 1
            note = "all bounced — trying the next combinations"
        else:
            await db.prospects.update_one({"id": pid}, {"$set": {"contacts.$[c].email": None, "contacts.$[c].email_status": None,
                                                                 "contacts.$[c].no_valid_email": True},
                                                        "$pull": {"tpl_sent": {"$in": list({m["template_id"] for m in ms})}}},
                                          array_filters=[{"c.name": name}])
            st["exhausted"] += 1
            note = "every combination bounced — no working email found"
        if p.get("lead_id"):
            await log_message(p["lead_id"], f"⚠️ {name}: {', '.join(m['to'] for m in ms)} {note}.", author=AGENT)
    return st


# ---------- bounce + reply reading over IMAP with the inbox app password ----------

def _imap_fetch(host: str, user: str, pwd: str, since_uid: int) -> tuple[list, int]:
    out, top = [], since_uid
    m = imaplib.IMAP4_SSL(host, 993, timeout=30)
    try:
        m.login(user, pwd)
        m.select("INBOX", readonly=True)
        crit = f"UID {since_uid + 1}:*" if since_uid else "SINCE " + (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%d-%b-%Y")
        typ, data = m.uid("search", None, crit)
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > since_uid][-300:]
        for u in uids:
            typ, d = m.uid("fetch", str(u), "(BODY.PEEK[]<0.60000>)")
            raw = next((x[1] for x in d if isinstance(x, tuple)), b"")
            msg = email.message_from_bytes(raw)
            texts = []
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html", "message/delivery-status"):
                    pl = part.get_payload(decode=True)
                    if pl:
                        texts.append(pl.decode(part.get_content_charset() or "utf-8", "ignore"))
                    elif part.get_content_type() == "message/delivery-status":
                        texts.append(str(part.get_payload()))
            out.append({"subject": str(make_header(decode_header(msg.get("Subject", "")))),
                        "from": {"emailAddress": {"address": email.utils.parseaddr(msg.get("From", ""))[1]}},
                        "body": {"content": " ".join(texts)[:40000]}})
            top = max(top, u)
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return out, top


async def imap_sync() -> dict:
    from core import inbox_sync as ix
    from core import outlook_send as ol
    st = {"inboxes": 0, "bounces": 0, "replies": 0, "errors": 0}
    async for b in db.mailboxes.find({"active": True, "is_deleted": {"$ne": True}, "smtp_password_enc": {"$exists": True}}, {"_id": 0}):
        host = "imap.gmail.com" if "gmail" in (b.get("smtp_host") or "") else "outlook.office365.com"
        try:
            msgs, top = await asyncio.to_thread(_imap_fetch, host, b["smtp_user"], ol.dec(b["smtp_password_enc"]), int(b.get("imap_uid") or 0))
            now = now_utc_str()
            for m in msgs:
                if ix.BOUNCE_FROM.search(m["from"]["emailAddress"]["address"]) or ix.BOUNCE_SUBJ.search(m["subject"] or ""):
                    st["bounces"] += await ix._bounce(m, now)
                else:
                    st["replies"] += await ix._reply(m, now)
            await db.mailboxes.update_one({"id": b["id"]}, {"$set": {"imap_uid": top, "imap_at": now}, "$unset": {"imap_error": 1}})
            st["inboxes"] += 1
        except Exception as e:
            st["errors"] += 1
            logger.warning(f"imap sync {b.get('smtp_user')}: {e}")
            await db.mailboxes.update_one({"id": b["id"]}, {"$set": {"imap_error": str(e)[:200], "imap_at": now_utc_str()}})
    return st
