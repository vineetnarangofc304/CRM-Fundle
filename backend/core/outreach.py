"""Outreach sequences: AI-drafted Outlook emails + LinkedIn drafts for top-fit prospects, sent only after approval."""
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from emergentintegrations.llm.chat import LlmChat, UserMessage

from core.db import db
from core.utils import log_message, next_id, now_utc_str

logger = logging.getLogger(__name__)

DEFAULT_PITCH = ("Fundle.ai builds an AI-powered customer loyalty platform for retail and consumer brands: "
                 "omnichannel points & rewards across stores and online, WhatsApp-first member engagement, "
                 "AI-driven personalised offers, and analytics that lift repeat purchase and customer lifetime value. "
                 "Quick to launch across all stores, integrates with POS and e-commerce.")
DEFAULTS = {"enabled": True, "min_score": 60, "daily_cap": 40, "drafts_per_run": 10, "gaps": [3, 7],
            "pitch": DEFAULT_PITCH, "signature": "Team Fundle.ai\nhttps://fundle.ai", "sender_name": "Fundle.ai"}
DRAFT_SYS = ("You write short, specific, human B2B outreach for an Indian/GCC/UK audience. No hype, no emojis, "
             "no fake claims or invented stats. Reference the brand's real context. Reply with JSON only.")


async def get_settings() -> dict:
    doc = await db.settings.find_one({"key": "outreach"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def pick_contact(p: dict):
    cs = [c for c in (p.get("contacts") or []) if c.get("email")]
    mk = [c for c in cs if any(w in (c.get("role") or "").lower() for w in ("market", "cmo", "crm", "loyal", "customer", "digital"))]
    return (mk or cs or [None])[0]


async def draft_sequence(p: dict, contact: dict, user=None) -> int:
    from core import prospector as pe
    st = await get_settings()
    prompt = (f"Seller: {st['pitch']}\n\nProspect brand: {p['brand_name']} — {p['category']}, {p['country']}; "
              f"segment {p.get('segment')}; stores: {p.get('store_count') or 'unknown'}; "
              f"existing loyalty programme: {p.get('has_loyalty_program') or 'unknown'}; about: {p.get('description') or ''}\n"
              f"Recipient: {contact['name']} ({contact.get('role') or 'leader'}).\n"
              "Write a 3-step email sequence (email1 intro ≤120 words with one clear ask for a 20-minute call; email2 "
              "follow-up after 3 days ≤80 words with a different angle; email3 polite break-up after 7 more days ≤60 words), "
              "plus a LinkedIn connection note (≤280 characters) and a LinkedIn follow-up message (≤500 characters). "
              "Use the recipient's first name. Bodies as simple HTML paragraphs (<p>), no signature (added automatically).\n"
              'JSON: {"email1": {"subject": str, "body": str}, "email2": {"subject": str, "body": str}, '
              '"email3": {"subject": str, "body": str}, "linkedin_connect": str, "linkedin_message": str}')
    t = time.time()
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"outreach-{time.time_ns()}",
                   system_message=DRAFT_SYS).with_model("gemini", pe.FLASH)
    d = pe._json(str(await chat.send_message(UserMessage(text=prompt))), "obj")
    if not d or not isinstance(d.get("email1"), dict):
        await pe._log_run("draft", pe.FLASH, False, "No JSON in reply", time.time() - t, brand=p["brand_name"])
        raise ValueError("AI did not return a usable draft — try again")
    seq = await next_id("outreach_seq")
    base = {"seq_id": seq, "prospect_id": p["id"], "lead_id": p.get("lead_id"), "brand": p["brand_name"],
            "category": p["category"], "fit_score": p.get("fit_score"), "contact_name": contact["name"],
            "contact_role": contact.get("role"), "to": contact.get("email"), "email_status": contact.get("email_status"),
            "linkedin": contact.get("linkedin"), "status": "pending_approval", "created_at": _now()}
    docs = []
    for i in (1, 2, 3):
        e = d.get(f"email{i}") or {}
        if e.get("subject") and e.get("body"):
            docs.append({**base, "id": await next_id("outreach"), "channel": "email", "step": i,
                         "subject": e["subject"][:200], "body": clean_html(e["body"])[:6000], "scheduled_at": None})
    for i, k in ((1, "linkedin_connect"), (2, "linkedin_message")):
        if d.get(k):
            docs.append({**base, "id": await next_id("outreach"), "channel": "linkedin", "step": i,
                         "subject": "Connection note" if i == 1 else "Follow-up message", "body": str(d[k])[:1500]})
    await db.outreach.insert_many(docs)
    await db.prospects.update_one({"id": p["id"]}, {"$set": {"outreach_seq": seq, "outreach_status": "pending_approval"}})
    await pe._log_run("draft", pe.FLASH, True, f"{len(docs)} drafts for {contact['name']}", time.time() - t, brand=p["brand_name"])
    return seq


def clean_html(html: str) -> str:
    """Strip scripts, frames, event handlers and javascript: URLs from AI/user email HTML."""
    html = re.sub(r"(?is)<(script|style|iframe|object|embed|form)[^>]*>.*?</\1\s*>", "", html or "")
    html = re.sub(r"(?is)<(script|iframe|object|embed|link|meta)[^>]*/?>", "", html)
    html = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html)
    return re.sub(r"(?i)(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*\2", r"\1=\2#\2", html)


def _with_signature(body: str, st: dict) -> str:
    sig = "<br>".join(st["signature"].splitlines())
    return f"{body}<p style='color:#475569'>{sig}</p>"


async def _send_one(m: dict, st: dict) -> bool:
    from core import mailer
    res = await mailer.send_email(m["to"], m["subject"], _with_signature(m["body"], st), html=True)
    if not res.get("ok"):
        await db.outreach.update_one({"id": m["id"]}, {"$set": {"status": "failed", "error": res.get("error")}})
        return False
    now = _now()
    await db.outreach.update_one({"id": m["id"]}, {"$set": {"status": "sent", "sent_at": now}, "$unset": {"error": 1}})
    gaps = st["gaps"]
    if m["step"] < 3:
        await db.outreach.update_one({"seq_id": m["seq_id"], "channel": "email", "step": m["step"] + 1}, {"$set": {
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=gaps[m["step"] - 1])).strftime("%Y-%m-%d %H:%M:%S")}})
    if m.get("lead_id"):
        await log_message(m["lead_id"], f"📧 Outreach email {m['step']}/3 sent to {m['contact_name']} &lt;{m['to']}&gt;: <b>{m['subject']}</b>",
                          author={"id": 0, "name": "Outreach Agent"})
    return True


async def _stop_engaged():
    """Stop sequences whose lead has been worked by the team (moved beyond first contact)."""
    seqs = await db.outreach.distinct("lead_id", {"status": {"$in": ["approved", "pending_approval"]}, "channel": "email"})
    if not seqs:
        return
    engaged = await db.leads.distinct("id", {"id": {"$in": seqs}, "lead_stage": {"$in": ["Contacted", "Converted", "Closed"]}})
    if engaged:
        await db.outreach.update_many({"lead_id": {"$in": engaged}, "status": {"$in": ["approved", "pending_approval"]}},
                                      {"$set": {"status": "stopped", "stop_reason": "Lead engaged by team"}})


async def run_outreach(trigger="cron") -> dict:
    from core import mailer
    st = await get_settings()
    stats = {"drafted": 0, "sent": 0, "failed": 0}
    if not st["enabled"]:
        return {"skipped": "disabled"}
    await _stop_engaged()
    cands = await db.prospects.find({"fit_score": {"$gte": st["min_score"]}, "outreach_seq": {"$exists": False},
                                     "contacts.email": {"$nin": [None, ""]}}, {"_id": 0}
                                    ).sort("fit_score", -1).limit(st["drafts_per_run"]).to_list(st["drafts_per_run"])
    for p in cands:
        c = pick_contact(p)
        if c:
            try:
                await draft_sequence(p, c)
                stats["drafted"] += 1
            except Exception as e:
                logger.warning(f"draft failed {p['brand_name']}: {e}")
    if await mailer.is_connected():
        day = _now()[:10]
        sent_today = await db.outreach.count_documents({"status": "sent", "channel": "email", "sent_at": {"$gte": day}})
        due = await db.outreach.find({"status": "approved", "channel": "email", "scheduled_at": {"$ne": None, "$lte": _now()}},
                                     {"_id": 0}).sort("scheduled_at", 1).limit(max(0, st["daily_cap"] - sent_today)).to_list(500)
        for m in due:
            stats["sent" if await _send_one(m, st) else "failed"] += 1
    await db.settings.update_one({"key": "outreach_last"}, {"$set": {"at": now_utc_str(), "stats": stats, "trigger": trigger}}, upsert=True)
    return stats
