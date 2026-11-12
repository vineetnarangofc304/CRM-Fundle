"""Email template library: AI-tailored, click-to-send or auto-send templates with attachments + video links."""
import html
import logging
from datetime import datetime, timedelta, timezone
import os
import re
import time

from emergentintegrations.llm.chat import LlmChat, UserMessage

from core.db import db
from core.storage import get_object
from core.utils import next_id, now_utc_str

logger = logging.getLogger(__name__)
MAX_ATTACH_BYTES = 3 * 1024 * 1024

_P = "<p>Hi {{first_name}},</p>"
STARTERS = [
    {"kind": "intro", "name": "Intro email", "mode": "auto",
     "subject": "{{brand}} x Fundle.ai — retail intelligence across stores, malls and marketplaces",
     "body": _P + "<p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, "
             "stores, malls and marketplaces, so brands like {{brand}} see sales, payouts and customer behaviour in one place.</p>"
             "<p>Teams use us for marketplace reconciliation (MarketplaceMetrics), daily mall / MBO / SIS sales reporting "
             "(MallMetrics) and customer loyalty.</p><p>Would a 20-minute call next week be useful to see where it fits for {{brand}}?</p>"},
    {"kind": "marketplace", "name": "Marketplace emailer", "mode": "manual",
     "subject": "Is {{brand}} recovering every rupee from Amazon, Flipkart and Myntra?",
     "body": _P + "<p>Most brands selling on Amazon, Flipkart, Myntra, AJIO and JioMart lose 2–5% of revenue to commission "
             "mismatches, unclaimed returns and fee errors that nobody has time to chase.</p>"
             "<p>MarketplaceMetrics by Fundle matches every order to its payout, checks fees against agreed rates, flags "
             "differences, tracks claims to recovery and shows true profit by SKU, with a Morning Finance Brief for your team.</p>"
             "<p>Happy to run a quick reconciliation check on a month of {{brand}}'s marketplace data. Open to a 20-minute call?</p>"},
    {"kind": "loyalty", "name": "Loyalty emailer", "mode": "manual",
     "subject": "Bringing {{brand}}'s customers back more often",
     "body": _P + "<p>Fundle.ai runs AI-powered loyalty for retail and consumer brands: points and rewards that work across "
             "stores and online, WhatsApp-first member engagement and personalised offers that lift repeat purchase.</p>"
             "<p>It plugs into your POS and e-commerce and can go live across all {{brand}} stores quickly.</p>"
             "<p>Could we take 20 minutes to show what repeat-purchase uplift looks like for a brand like yours?</p>"},
    {"kind": "mbo", "name": "MBO emailer", "mode": "manual",
     "subject": "Daily sell-through from every MBO counter for {{brand}}",
     "body": _P + "<p>When {{brand}} sells through multi-brand outlets, sales data usually arrives late, in different formats, "
             "and is hard to reconcile.</p><p>MallMetrics by Fundle automates daily sales reporting from each MBO partner, "
             "reconciles it against invoices and uses AI to show which stores, styles and regions are driving sell-through.</p>"
             "<p>Worth a 20-minute look at how this would work across your MBO network?</p>"},
    {"kind": "sis", "name": "SIS emailer", "mode": "manual",
     "subject": "Shop-in-shop sales visibility for {{brand}}, every morning",
     "body": _P + "<p>Shop-in-shop counters in department stores and malls are great for reach, but daily sales, "
             "reconciliation and settlements with the host store take a lot of manual effort.</p>"
             "<p>MallMetrics by Fundle gives {{brand}} automated daily sales reports for every SIS counter, tenant sales "
             "reconciliation and AI insights on what's driving each location.</p>"
             "<p>Open to a quick 20-minute call to see it on your SIS data?</p>"},
]
_H = "<p>Dear {{first_name}}:</p>"
_ASK = "<p>Would you be open to a 25-minute live demo and discussion this week or early next week?</p>"
ABHINAV_SIG = ("Regards,\nAbhinav Khanna\nCBO | Fundle.AI\nM: 9910530372\nhttps://fundle.ai\n"
               "LinkedIn: https://www.linkedin.com/in/abhinav-khanna-54683959/")
AUDIENCES = {
    "all": {},
    "d2c": {"segment": "D2C / E-commerce"},
    "stores_100": {"segment": {"$ne": "D2C / E-commerce"}, "store_count": {"$gte": 100}},
    "stores_11_99": {"segment": {"$ne": "D2C / E-commerce"}, "store_count": {"$gte": 11, "$lt": 100}},
    "stores_1_10": {"segment": {"$ne": "D2C / E-commerce"}, "$or": [{"store_count": {"$lt": 11}}, {"store_count": None}]},
}
INTRO_V2 = [
    {"kind": "intro_d2c", "audience": "d2c", "name": "Intro — D2C brands",
     "subject": "{{brand}} x Fundle.AI | D2C AI Loyalty & Marketplace Reconciliation Demo",
     "body": _H + "<p>I’m reaching out from Fundle.AI. We help D2C brands automate website-led loyalty, reward every shopper, "
             "and drive repeat purchases through referrals, gift vouchers, lifecycle marketing, and omnichannel rewards.</p>"
             "<p>Our Agentic AI capabilities also automate SEO, social media content creation, website customer support through "
             "AI chatbots, and marketplace reconciliation—augmenting your existing business processes.</p>"
             "<p>Would you be open to a 25-minute demo and discussion this week or early next?</p>"
             "<p>I will wait to hear back from you to take this initiative forward.</p>"},
    {"kind": "intro_100", "audience": "stores_100", "name": "Intro — 100+ stores",
     "subject": "{{brand}} x Fundle.AI | Unified Enterprise AI, Loyalty & Retail OS Demo Meeting",
     "body": _H + "<p>I’m reaching out from Fundle.AI. Our AI-powered Retail OS unifies CRM, loyalty, campaigns, customer service, "
             "and online-offline touchpoints on one platform, eliminating data silos and enabling end-to-end automation.</p>"
             "<p>Fundle Brain converts analytics into conversational intelligence, while our AI-powered CEO Cockpit identifies "
             "business gaps and recommends actions to drive incremental growth.</p><p>Key capabilities include:</p><ul>"
             "<li><b>Enterprise Loyalty &amp; CLM:</b> Omnichannel online-to-offline/offline-to-online earn-and-burn rewards, NPS and "
             "feedback, Digital Invoice, Choose Your Reward, Referrals, Gift vouchers, lifecycle marketing. Employee Discount.</li>"
             "<li><b>AI Calling &amp; Lead Management</b> and embedded DPDP Consent Management.</li>"
             "<li><b>Agentic AI:</b> SEO, social media, and AgenticAI Website customer support.</li>"
             "<li><b>Marketplace Reconciliation</b> for sales, settlements, inventory, and returns.</li>"
             "<li><b>MBO/SIS Sales Automation</b> for secondary sales, store performance, replenishment, and discount compliance.</li>"
             "</ul>" + _ASK},
    {"kind": "intro_11_99", "audience": "stores_11_99", "name": "Intro — 11 to 99 stores",
     "subject": "{{brand}} x Fundle.AI | AI-Powered Loyalty & Omnichannel Growth Demo Meeting",
     "body": _H + "<p>I’m reaching out from Fundle.AI. We help retail brands automate loyalty by integrating with their POS and "
             "online commerce platforms to enable seamless online-to-offline and offline-to-online (O2O) earn-and-burn rewards.</p>"
             "<p>Our AI-powered platform enables you to:</p><ul>"
             "<li><b>Automate Loyalty &amp; Rewards:</b> Points, referrals, gift vouchers, and personalised rewards.</li>"
             "<li><b>Track Performance in Real Time:</b> Live analytics and reports to understand customer behaviour and business performance.</li>"
             "<li><b>Automate Customer Engagement:</b> An in-built campaign manager for SMS, WhatsApp, and RCS campaigns.</li>"
             "<li><b>Drive Repeat Purchases:</b> Deliver targeted campaigns and personalised offers to improve retention and customer lifetime value.</li>"
             "</ul><p>Would you be open to a 25-minute live demo and discussion this week or early next?</p>"},
    {"kind": "intro_1_10", "audience": "stores_1_10", "name": "Intro — 1 to 10 stores (and unknown)",
     "subject": "{{brand}} x Fundle.AI | Automate Loyalty & Grow Repeat Sales",
     "body": _H + "<p>I’m reaching out from Fundle.AI. We help emerging retail brands launch and automate their loyalty &amp; "
             "customer engagement without the complexity of enterprise systems.</p>"
             "<p>Our AI-powered loyalty platform integrates with your POS to help you:</p><ul>"
             "<li><b>Reward Every Customer:</b> Launch points, referrals, gift vouchers, and personalised rewards.</li>"
             "<li><b>Drive Repeat Sales:</b> Run targeted SMS, WhatsApp, and RCS campaigns through an in-built campaign manager.</li>"
             "<li><b>Track Performance:</b> Access real-time reports and customer insights to understand what drives repeat purchases.</li>"
             "</ul>" + _ASK + "<p>I will wait to hear back from you to take this initiative forward.</p>"},
]
_FU_PITCH = {
    "d2c": "website-led loyalty, referrals and marketplace reconciliation for D2C brands",
    "stores_100": "a unified AI Retail OS — loyalty, CRM, campaigns and marketplace reconciliation on one platform",
    "stores_11_99": "POS-integrated O2O loyalty with an in-built SMS, WhatsApp and RCS campaign manager",
    "stores_1_10": "simple POS-integrated loyalty and repeat-sale campaigns, without enterprise complexity",
}
_FU_NAME = {"d2c": "D2C brands", "stores_100": "100+ stores", "stores_11_99": "11 to 99 stores", "stores_1_10": "1 to 10 stores (and unknown)"}
FOLLOW_UPS = [x for a, pitch in _FU_PITCH.items() for x in (
    {"kind": f"fu2_{a}", "audience": a, "step": 2, "wait_days": 3, "name": f"Follow-up 1 — {_FU_NAME[a]}",
     "subject": "Re: {{intro_subject}}",
     "body": "<p>Dear {{first_name}},</p><p>Just following up on my note below. We help brands like {{brand}} with "
             f"{pitch}.</p><p>Would a 25-minute live demo this week or early next work for you? Happy to fit around your calendar.</p>"},
    {"kind": f"fu3_{a}", "audience": a, "step": 3, "wait_days": 7, "name": f"Follow-up 2 — {_FU_NAME[a]}",
     "subject": "Re: {{intro_subject}}",
     "body": "<p>Dear {{first_name}},</p><p>I don’t want to crowd your inbox, so this will be my last note for now.</p>"
             "<p>If improving repeat purchases and customer loyalty at {{brand}} is a priority this quarter, I’d be glad to "
             "show you a 25-minute walkthrough. If someone else on your team handles this, could you point me to them?</p>"
             "<p>Thank you for your time.</p>"},
)]
TAILOR_SYS = ("You personalise B2B email templates. Keep the template's offer, structure, links and length; "
              "fill placeholders; add at most one specific, true line about the recipient's company. "
              "No hype, no emojis, no invented stats. Reply with JSON only.")


def first_name(name: str) -> str:
    return (name or "there").strip().split(" ")[0] or "there"


def merge(text: str, p: dict, c: dict) -> str:
    vals = {"first_name": first_name(c.get("name")), "name": c.get("name") or "", "role": c.get("role") or "",
            "brand": p.get("brand_name") or "", "company": p.get("brand_name") or "", "category": p.get("category") or ""}
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: vals.get(m.group(1), m.group(0)), text or "")


async def ai_text(system: str, prompt: str) -> dict:
    from core import prospector as pe
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"tpl-{time.time_ns()}",
                   system_message=system).with_model("gemini", pe.FLASH)
    return pe._json(str(await chat.send_message(UserMessage(text=prompt))), "obj") or {}


async def tailor(t: dict, p: dict, c: dict) -> tuple[str, str]:
    """AI-personalised subject/body; falls back to plain placeholder merge if AI is unavailable."""
    from core.outreach import clean_html
    subj, body = merge(t["subject"], p, c), merge(t["body"], p, c)
    if t.get("ai_tailor") is False:
        return subj[:200], clean_html(body)[:6000]
    prompt = (f"Recipient: {c.get('name')} ({c.get('role') or 'leader'}) at {p.get('brand_name')} — {p.get('category')}, "
              f"{p.get('country')}. About the company: {(p.get('description') or '')[:600]}\n\n"
              f"Template subject: {subj}\nTemplate body (HTML):\n{body}\n\n"
              'Return JSON: {"subject": str, "body": str (simple HTML <p> paragraphs, no signature)}')
    try:
        d = await ai_text(TAILOR_SYS, prompt)
        if d.get("subject") and d.get("body"):
            return d["subject"][:200], clean_html(d["body"])[:6000]
    except Exception as e:
        logger.warning(f"tailor failed for {p.get('brand_name')}: {e}")
    return subj[:200], clean_html(body)[:6000]


async def seed_starters(force: bool = False) -> int:
    if not force and await db.email_templates.count_documents({}):
        return 0
    n = 0
    for s in STARTERS:
        if await db.email_templates.find_one({"kind": s["kind"], "is_deleted": {"$ne": True}}):
            continue
        await db.email_templates.insert_one({**s, "id": await next_id("email_template"), "attachments": [],
                                             "video_links": [], "active": True, "is_deleted": False,
                                             "created_at": now_utc_str(), "updated_at": now_utc_str()})
        n += 1
    return n


async def seed_intro_v2():
    """One-time: add the 4 store-count intro emails (auto, sent as written) and retire the old generic intro."""
    if await db.settings.find_one({"key": "tpl_intro_v2"}):
        return
    await db.settings.insert_one({"key": "tpl_intro_v2", "at": now_utc_str()})
    for s in INTRO_V2:
        if not await db.email_templates.find_one({"kind": s["kind"], "is_deleted": {"$ne": True}}):
            await db.email_templates.insert_one({**s, "id": await next_id("email_template"), "mode": "auto", "ai_tailor": False,
                                                 "attachments": [], "video_links": [], "active": True, "is_deleted": False,
                                                 "created_at": now_utc_str(), "updated_at": now_utc_str()})
    await db.email_templates.update_many({"kind": "intro"}, {"$set": {"mode": "manual", "updated_at": now_utc_str()}})
    await db.mailboxes.update_many({"smtp_user": {"$regex": "^abhinav"}, "signature": {"$in": [None, ""]}}, {"$set": {"signature": ABHINAV_SIG}})
    await db.mailboxes.update_many({"smtp_user": {"$regex": "^abhinav"}, "signature": {"$exists": False}}, {"$set": {"signature": ABHINAV_SIG}})


async def seed_followups_v1():
    """One-time: add day-3 / day-7 follow-ups per store group (auto, sent as written) and default the signature to Abhinav's."""
    if await db.settings.find_one({"key": "tpl_followups_v1"}):
        return
    await db.settings.insert_one({"key": "tpl_followups_v1", "at": now_utc_str()})
    for s in FOLLOW_UPS:
        if not await db.email_templates.find_one({"kind": s["kind"], "is_deleted": {"$ne": True}}):
            await db.email_templates.insert_one({**s, "id": await next_id("email_template"), "mode": "auto", "ai_tailor": False,
                                                 "attachments": [], "video_links": [], "active": True, "is_deleted": False,
                                                 "created_at": now_utc_str(), "updated_at": now_utc_str()})
    st = await db.settings.find_one({"key": "outreach"}) or {}
    if not (st.get("signature") or "").strip():
        await db.settings.update_one({"key": "outreach"}, {"$set": {"signature": ABHINAV_SIG}}, upsert=True)


async def run_followups(limit: int) -> int:
    """Queue follow-up N to the same contact `wait_days` after the intro was sent, unless the brand replied or it bounced."""
    from core.outreach import _now
    made = 0
    fus = await db.email_templates.find({"mode": "auto", "active": True, "is_deleted": {"$ne": True}, "step": {"$gt": 1}},
                                        {"_id": 0}).sort("step", 1).to_list(50)
    intro_ids = [t["id"] async for t in db.email_templates.find(
        {"$or": [{"audience": {"$nin": [None, "all"]}}, {"kind": {"$regex": "^intro"}}], "step": {"$not": {"$gt": 1}}}, {"id": 1})]
    for t in fus:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=t.get("wait_days") or 3)).strftime("%Y-%m-%d %H:%M:%S")
        q = {"kind": "template", "template_id": {"$in": intro_ids}, "status": "sent", "sent_at": {"$lte": cutoff},
             "replied_at": {"$exists": False}, f"fu_step{t['step']}": {"$exists": False}}
        if t["step"] > 2:
            q[f"fu_step{t['step'] - 1}"] = {"$exists": True, "$ne": "skip"}
        aud = AUDIENCES.get(t.get("audience") or "all") or {}
        pids = [x["id"] async for x in db.prospects.find({"$and": [{"replied": {"$ne": True}}, aud]}, {"id": 1})]
        q["prospect_id"] = {"$in": pids}
        async for o in db.outreach.find(q, {"_id": 0}).sort("sent_at", 1):
            if made >= limit:
                return made
            p = await db.prospects.find_one({"id": o["prospect_id"]}, {"_id": 0})
            if not p or o["to"].lower() in {e.lower() for e in p.get("bounced_emails") or []}:
                await db.outreach.update_one({"id": o["id"]}, {"$set": {f"fu_step{t['step']}": "skip"}})
                continue
            if t["step"] > 2 and not await db.outreach.find_one({"seq_id": o["seq_id"], "step": t["step"] - 1, "status": "sent"}):
                continue
            await db.outreach.update_one({"id": o["id"]}, {"$set": {f"fu_step{t['step']}": t["id"]}})
            c = next((x for x in p.get("contacts") or [] if (x.get("email") or "").lower() == o["to"].lower()),
                     {"name": o.get("contact_name"), "role": o.get("contact_role"), "email": o["to"]})
            subject, body = await tailor({**t, "subject": t["subject"].replace("{{intro_subject}}", o["subject"])}, p, c)
            now = _now()
            await db.outreach.insert_one({
                "id": await next_id("outreach"), "seq_id": o["seq_id"], "channel": "email", "step": t["step"], "kind": "template",
                "template_id": t["id"], "template_name": t["name"], "tpl_version": t.get("updated_at"), "prospect_id": p["id"],
                "lead_id": o.get("lead_id"), "brand": o["brand"], "category": o.get("category"), "fit_score": p.get("fit_score"),
                "contact_name": c.get("name"), "contact_role": c.get("role"), "to": o["to"], "email_status": c.get("email_status"),
                "subject": subject, "body": body, "attachments": t.get("attachments") or [], "video_links": t.get("video_links") or [],
                "sent_via_pref": o.get("sent_via"), "intro_subject": o["subject"],
                "status": "approved", "created_by": "Auto follow-up", "created_at": now, "scheduled_at": now,
                "approved_by": "Auto follow-up", "approved_at": now})
            await db.prospects.update_one({"id": p["id"]}, {"$addToSet": {"tpl_sent": t["id"]}})
            made += 1
    return made


async def load_attachments(items: list) -> list:
    out = []
    for a in items or []:
        f = await db.outreach_files.find_one({"id": a["file_id"], "is_deleted": {"$ne": True}})
        if f:
            data, _ = await get_object(f["storage_path"])
            out.append({"filename": f["filename"], "content_type": f["content_type"], "data": data})
    return out


def video_block(links: list) -> str:
    links = [v for v in links or [] if v]
    if not links:
        return ""
    items = "".join(f"<li><a href=\"{html.escape(v, quote=True)}\">{html.escape(v)}</a></li>" for v in links)
    return f"<p><b>Watch:</b></p><ul>{items}</ul>"


async def create_message(t: dict, p: dict, c: dict, status: str, by: str) -> dict:
    """Create one tailored email from a template for a prospect contact."""
    from core.outreach import _now
    subject, body = await tailor(t, p, c)
    if not p.get("lead_id"):
        from core import prospector as pe
        p = {**p, "lead_id": await pe.upsert_lead(p)}
    now = _now()
    doc = {"id": await next_id("outreach"), "seq_id": await next_id("outreach_seq"), "channel": "email", "step": 1,
           "kind": "template", "template_id": t["id"], "template_name": t["name"], "tpl_version": t.get("updated_at"), "prospect_id": p["id"],
           "lead_id": p.get("lead_id"), "brand": p["brand_name"], "category": p.get("category"),
           "fit_score": p.get("fit_score"), "contact_name": c.get("name"), "contact_role": c.get("role"),
           "to": c.get("email"), "email_status": c.get("email_status"), "linkedin": c.get("linkedin"),
           "subject": subject, "body": body, "attachments": t.get("attachments") or [],
           "video_links": t.get("video_links") or [], "status": status, "created_by": by, "created_at": now,
           "scheduled_at": now if status == "approved" else None,
           **({"approved_by": by, "approved_at": now} if status == "approved" else {})}
    await db.outreach.insert_one(dict(doc))
    await db.prospects.update_one({"id": p["id"]}, {"$addToSet": {"tpl_sent": t["id"]}})
    return doc


async def run_auto(limit: int) -> int:
    """Auto-mode templates: queue a tailored email for every brand with an email that hasn't had that template."""
    from core.outreach import pick_contact
    await seed_intro_v2()
    await seed_followups_v1()
    made = await run_followups(limit)
    tpls = await db.email_templates.find({"mode": "auto", "active": True, "is_deleted": {"$ne": True},
                                          "step": {"$not": {"$gt": 1}}}, {"_id": 0}).to_list(50)
    # A brand gets only one intro: any audience-specific template (or the old generic intro) counts.
    intro_ids = [t["id"] async for t in db.email_templates.find(
        {"$or": [{"audience": {"$in": [k for k in AUDIENCES if k != "all"]}}, {"kind": {"$regex": "^intro"}}],
         "step": {"$not": {"$gt": 1}}}, {"id": 1})]
    for t in tpls:
        if made >= limit:
            break
        aud = t.get("audience") or "all"
        is_intro = t["id"] in intro_ids
        q = {"tpl_sent": {"$nin": intro_ids} if is_intro else {"$ne": t["id"]}, "replied": {"$ne": True},
             "contacts": {"$elemMatch": {"email": {"$nin": [None, ""]}}}}
        f = AUDIENCES.get(aud, {})
        cands = await db.prospects.find({"$and": [q, f]} if f else q, {"_id": 0}
                                        ).sort("fit_score", -1).limit(limit - made).to_list(limit)
        for p in cands:
            c = pick_contact(p)
            if c:
                await create_message(t, p, c, "approved", "Auto-send")
                made += 1
    return made
