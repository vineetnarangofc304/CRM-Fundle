"""Email template library: AI-tailored, click-to-send or auto-send templates with attachments + video links."""
import html
import logging
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
    made = 0
    tpls = await db.email_templates.find({"mode": "auto", "active": True, "is_deleted": {"$ne": True}}, {"_id": 0}).to_list(50)
    for t in tpls:
        if made >= limit:
            break
        cands = await db.prospects.find({"tpl_sent": {"$ne": t["id"]}, "replied": {"$ne": True},
                                         "contacts": {"$elemMatch": {"email": {"$nin": [None, ""]}}}}, {"_id": 0}
                                        ).sort("fit_score", -1).limit(limit - made).to_list(limit)
        for p in cands:
            c = pick_contact(p)
            if c:
                await create_message(t, p, c, "approved", "Auto-send")
                made += 1
    return made
