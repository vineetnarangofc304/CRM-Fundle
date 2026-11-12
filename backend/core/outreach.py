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
DEFAULTS = {"enabled": True, "min_score": 0, "daily_cap": 200, "drafts_per_run": 30, "gaps": [3, 7],
            "auto_send": True, "cc": "", "ai_sequence": False,
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


async def _send_one(m: dict, st: dict, pool: list | None = None, force: bool = False) -> bool:
    from core import mailboxes as mb
    from core import email_templates as et
    if pool is None:
        pool = await mb.senders(st, _now()[:10])
    sender = await mb.pick(m.get("prospect_id"), pool, force)
    if not sender:
        await db.outreach.update_one({"id": m["id"]}, {"$set": {"status": "approved"}})
        return False
    try:
        files = await et.load_attachments(m.get("attachments"))
    except Exception as e:
        files, res = None, {"ok": False, "error": f"Attachment unavailable: {str(e)[:150]}"}
    if files is not None:
        body = _with_signature(m["body"] + et.video_block(m.get("video_links")), st)
        res = await mb.send(sender, m["to"], m["subject"], body, cc=st.get("cc") or None, attachments=files)
    if not res.get("ok"):
        tries = m.get("send_attempts", 0) + 1
        retry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        await db.outreach.update_one({"id": m["id"]}, {"$set": {
            "error": res.get("error"), "send_attempts": tries,
            **({"status": "failed"} if tries >= 3 else {"status": "approved", "scheduled_at": retry})}})
        if tries >= 3:
            await db.outreach.update_many({"seq_id": m["seq_id"], "channel": "email", "step": {"$gt": m["step"]},
                                           "status": {"$in": ["approved", "pending_approval"]}},
                                          {"$set": {"status": "stopped", "stop_reason": f"Email {m['step']} failed to send"}})
        return False
    now = _now()
    sender["remaining"] -= 1
    await db.outreach.update_one({"id": m["id"]}, {"$set": {"status": "sent", "sent_at": now, "sent_via": sender["id"],
                                                            "sent_from": sender["email"]}, "$unset": {"error": 1}})
    gaps = st["gaps"]
    if m["step"] < 3:
        await db.outreach.update_one({"seq_id": m["seq_id"], "channel": "email", "step": m["step"] + 1}, {"$set": {
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=gaps[m["step"] - 1])).strftime("%Y-%m-%d %H:%M:%S")}})
    if m.get("lead_id"):
        what = f"“{m['template_name']}” email" if m.get("template_name") else f"Outreach email {m['step']}/3"
        extra = "".join(f" · 📎 {a['filename']}" for a in m.get("attachments") or []) + (" · 🎬 video link" if m.get("video_links") else "")
        await log_message(m["lead_id"], f"📧 {what} sent to {m['contact_name']} &lt;{m['to']}&gt; from {sender['email']}: <b>{m['subject']}</b>{extra}",
                          author={"id": 0, "name": m.get("approved_by") or "Outreach Agent"})
    return True


async def _stop_engaged():
    """Stop sequences whose lead has been worked by the team (moved beyond first contact)."""
    seqs = await db.outreach.distinct("lead_id", {"status": {"$in": ["approved", "pending_approval"]}, "channel": "email",
                                                  "kind": {"$ne": "template"}})
    if not seqs:
        return
    engaged = await db.leads.distinct("id", {"id": {"$in": seqs}, "lead_stage": {"$in": ["Contacted", "Converted", "Closed"]}})
    if engaged:
        await db.outreach.update_many({"lead_id": {"$in": engaged}, "kind": {"$ne": "template"},
                                       "status": {"$in": ["approved", "pending_approval"]}},
                                      {"$set": {"status": "stopped", "stop_reason": "Lead engaged by team"}})


async def auto_approve() -> int:
    """Auto-send mode: approve pending AI-sequence drafts; step 1 goes out now, later steps follow the gaps."""
    now = _now()
    seq = {"channel": "email", "kind": {"$ne": "template"}}
    r1 = await db.outreach.update_many({"status": "pending_approval", "step": 1, **seq},
                                       {"$set": {"status": "approved", "approved_by": "Auto-send", "approved_at": now, "scheduled_at": now}})
    r2 = await db.outreach.update_many({"status": "pending_approval", "step": {"$gt": 1}, **seq},
                                       {"$set": {"status": "approved", "approved_by": "Auto-send", "approved_at": now}})
    if r1.modified_count or r2.modified_count:
        await db.prospects.update_many({"outreach_status": "pending_approval"}, {"$set": {"outreach_status": "approved"}})
    return r1.modified_count


async def _migrate_v2():
    if await db.settings.find_one({"key": "outreach_v2"}):
        return
    await db.settings.update_one({"key": "outreach"}, {"$set": {
        "auto_send": True, "min_score": 0, "daily_cap": 100, "drafts_per_run": 30, "cc": "vineet.narang@fundle.ai"}}, upsert=True)
    await db.settings.insert_one({"key": "outreach_v2", "done": True})


async def _migrate_v3():
    """Switch to the template library: seed starters, retire unsent AI-sequence emails (Intro template replaces them)."""
    from core import email_templates as et
    if await db.settings.find_one({"key": "outreach_v3"}):
        return
    await et.seed_starters()
    await db.settings.update_one({"key": "outreach"}, {"$set": {"ai_sequence": False}}, upsert=True)
    await db.outreach.update_many({"kind": {"$ne": "template"}, "channel": "email", "status": {"$in": ["pending_approval", "approved"]}},
                                  {"$set": {"status": "stopped", "stop_reason": "Replaced by template outreach"}})
    await db.settings.insert_one({"key": "outreach_v3", "done": True})


async def _migrate_v4():
    if await db.settings.find_one({"key": "outreach_v4"}):
        return
    await db.settings.update_one({"key": "outreach"}, {"$set": {"daily_cap": 200}}, upsert=True)
    await db.settings.insert_one({"key": "outreach_v4", "done": True})


async def run_outreach(trigger="cron") -> dict:
    await _migrate_v2()
    await _migrate_v3()
    await _migrate_v4()
    st = await get_settings()
    stats = {"drafted": 0, "sent": 0, "failed": 0}
    if not st["enabled"]:
        return {"skipped": "disabled"}
    now = datetime.now(timezone.utc)
    my_until = (now + timedelta(minutes=30)).isoformat()
    await db.settings.update_one({"key": "outreach_lock"}, {"$setOnInsert": {"until": ""}}, upsert=True)
    got = await db.settings.find_one_and_update(
        {"key": "outreach_lock", "until": {"$lt": now.isoformat()}}, {"$set": {"until": my_until}})
    if got is None:
        return {"skipped": "already running"}
    try:
        return await _run_outreach(st, stats, trigger)
    finally:
        await db.settings.update_one({"key": "outreach_lock", "until": my_until}, {"$set": {"until": ""}})


async def _run_outreach(st, stats, trigger) -> dict:
    from core import mailer
    from core import email_templates as et
    await db.outreach.update_many({"status": "sending"}, {"$set": {"status": "approved"}})
    await _stop_engaged()
    cands = []
    if st["ai_sequence"]:
        f = {"outreach_seq": {"$exists": False}, "contacts": {"$elemMatch": {"email": {"$nin": [None, ""]}}}}
        if st["min_score"] > 0:
            f["fit_score"] = {"$gte": st["min_score"]}
        cands = await db.prospects.find(f, {"_id": 0}
                                        ).sort("fit_score", -1).limit(st["drafts_per_run"]).to_list(st["drafts_per_run"])
    for p in cands:
        c = pick_contact(p)
        if c:
            try:
                await draft_sequence(p, c)
                stats["drafted"] += 1
            except Exception as e:
                logger.warning(f"draft failed {p['brand_name']}: {e}")
    if st["auto_send"]:
        stats["auto_approved"] = await auto_approve()
    if await mailer.is_connected():
        from core import mailboxes as mb
        stats["auto_template"] = await et.run_auto(st["drafts_per_run"])
        pool = await mb.senders(st, _now()[:10])
        while any(s["remaining"] > 0 for s in pool):
            m = await db.outreach.find_one_and_update(
                {"status": "approved", "channel": "email", "scheduled_at": {"$ne": None, "$lte": _now()}},
                {"$set": {"status": "sending"}}, sort=[("scheduled_at", 1)], projection={"_id": 0})
            if not m:
                break
            stats["sent" if await _send_one(m, st, pool) else "failed"] += 1
    await db.settings.update_one({"key": "outreach_last"}, {"$set": {"at": now_utc_str(), "stats": stats, "trigger": trigger}}, upsert=True)
    return stats
