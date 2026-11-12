"""Email-finder agent + loyalty-fit score for prospects."""
import asyncio
import re
import time

import dns.resolver

from core.db import db
from core.utils import log_message, now_utc_str

CONTACT_SYS = ("You are a B2B contact researcher. Run SEVERAL Google searches, using all of these sources: "
               "(1) LinkedIn profiles via Google, e.g. site:linkedin.com/in \"<brand>\" CMO OR \"Head of Marketing\" OR CEO, "
               "and the company's LinkedIn page People section; (2) press releases (PR Newswire, Business Wire, PTI, ANI); "
               "(3) articles and interviews in business papers and magazines (Economic Times, ET Retail, ET BrandEquity, "
               "Business Standard, Mint, Financial Express, Afaqs, exchange4media, Inc42, YourStory, Entrackr, Forbes India, "
               "Gulf News, Khaleej Times, Arabian Business for UAE), plus 'appointed as' / 'joins as' / 'elevated to' "
               "appointment news; (4) the company website team/about/contact pages; (5) RocketReach/SignalHire/ZoomInfo/"
               "Apollo/Lusha snippets. Prefer people currently in the role (check recent dates) and give the LinkedIn "
               "URL whenever found. Find decision makers and their WORK emails. "
               "Never invent an email: only return one you saw published, or mark it as a pattern guess. Reply with JSON only.")


def fit_score(p: dict) -> int:
    """0-100 fit for a loyalty platform: store footprint, segment, no existing loyalty, reachable contacts."""
    s = 0
    stores = p.get("store_count") or 0
    s += 40 if stores >= 200 else 32 if stores >= 50 else 24 if stores >= 15 else 14 if stores >= 3 else 0
    s += {"Offline Retail": 20, "Omnichannel": 22, "D2C / E-commerce": 14}.get(p.get("segment"), 6)
    loyal = (p.get("has_loyalty_program") or "").lower()
    s += 15 if (not loyal or "none" in loyal or "no " in loyal) else 6
    contacts = p.get("contacts") or []
    if any(c.get("email") and c.get("email_status") == "published" for c in contacts):
        s += 15
    elif any(c.get("email") for c in contacts):
        s += 8
    if p.get("ceo_linkedin") or any(c.get("linkedin") for c in contacts):
        s += 5
    if p.get("ceo_name"):
        s += 3
    return min(s, 100)


def _domain(p: dict):
    for v in (p.get("website"), p.get("contact_email")):
        v = (v or "").lower()
        m = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})", v) or re.search(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", v)
        if m:
            return m.group(1)
    return None


async def _mx_ok(domain: str) -> bool:
    def look():
        try:
            return bool(dns.resolver.resolve(domain, "MX", lifetime=6))
        except Exception:
            return False
    return await asyncio.to_thread(look) if domain else False


async def find_contacts(p: dict) -> dict:
    from core import prospector as pe
    dom = _domain(p)
    prompt = (f"Brand: \"{p['brand_name']}\" ({p['category']}, {p['country']}). Website domain: {dom or 'unknown'}. "
              f"CEO (if known): {p.get('ceo_name') or 'unknown'}.\n"
              "Find up to 4 decision makers for buying a customer-loyalty / CRM platform: the CEO / MD / Founder and the "
              "head of Marketing / CMO / Head of CRM, Loyalty or Customer Experience (also Head of Retail / Digital if no CMO).\n"
              "Search LinkedIn profiles via Google AND recent news/press releases/magazine interviews and appointment "
              "announcements to confirm names and current roles; put the best source URL in evidence_url.\n"
              "Also find the company's email address FORMAT (e.g. firstname.lastname@domain) if published anywhere.\n"
              'Return JSON: {"email_pattern": str|null, "contacts": [{"name": str, "role": str, "linkedin": str|null, '
              '"email": str|null, "email_status": "published"|"pattern"|null, "evidence_url": str|null}]}')
    t = time.time()
    try:
        d = pe._json(await pe._ask(pe.PRO, CONTACT_SYS, prompt), "obj") or {}
    except pe.AiCreditsOut as e:
        await db.prospects.update_one({"id": p["id"]}, {"$unset": {"contacts_status": 1}})
        await pe._log_run("contacts", pe.PRO, False, e, time.time() - t, brand=p["brand_name"])
        raise
    except Exception as e:
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"contacts_status": "failed", "contacts_error": str(e)[:200],
                                                                 "contacts_failed_at": now_utc_str()}})
        await pe._log_run("contacts", pe.PRO, False, e, time.time() - t, brand=p["brand_name"])
        return {"ok": False}
    mx = await _mx_ok(dom)
    contacts = []
    bounced = {e.lower() for e in p.get("bounced_emails") or []}
    for c in (d.get("contacts") or [])[:6]:
        if not isinstance(c, dict) or not (c.get("name") or "").strip():
            continue
        email = (c.get("email") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}", email) or email in bounced:
            email = None
        status = c.get("email_status") if c.get("email_status") in ("published", "pattern") else ("pattern" if email else None)
        li = c.get("linkedin") if isinstance(c.get("linkedin"), str) and "linkedin.com/" in c.get("linkedin") else None
        contacts.append({"name": c["name"].strip()[:80], "role": (c.get("role") or "")[:80], "linkedin": pe._url(li),
                         "email": email, "email_status": status if email else None,
                         "domain_accepts_mail": mx if email else None, "evidence_url": pe._url(c.get("evidence_url"))})
    upd = {"contacts": contacts, "email_pattern": d.get("email_pattern"), "contacts_status": "done", "contacts_priority": 0,
           "contacts_at": now_utc_str(), "mx_ok": mx}
    upd["fit_score"] = fit_score({**p, **upd})
    await db.prospects.update_one({"id": p["id"]}, {"$set": upd, "$unset": {"contacts_error": 1}})
    best = next((c for c in contacts if c.get("email") and c["email_status"] == "published"), None) or \
        next((c for c in contacts if c.get("email")), None)
    p = {**p, **upd}
    p["lead_id"] = await pe.upsert_lead(p)
    found = "; ".join(f"{c['name']} ({c['role'] or '—'}){' · ' + c['email'] if c.get('email') else ''}" for c in contacts) or "none found"
    await log_message(p["lead_id"], f"🔎 AI contact search: {found}", author={"id": 0, "name": "AI Prospecting"})
    if best:
        lead = await db.leads.find_one({"id": p["lead_id"]}, {"_id": 0, "email_from": 1})
        if lead is not None and (not lead.get("email_from") or lead["email_from"].lower() in bounced):
            await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"email_from": best["email"],
                                                                      "email_lc": best["email"]}})
    if p.get("lead_id"):
        await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"fit_score": upd["fit_score"]}})
    await pe._log_run("contacts", pe.PRO, True, f"{len(contacts)} contacts, {sum(1 for c in contacts if c.get('email'))} emails",
                      time.time() - t, brand=p["brand_name"])
    return {"ok": True}


async def next_contacts_job():
    """Researched brands first; then any discovered brand, so contacts don't wait for deep research."""
    for f in ({"contacts_priority": 1, "status": {"$ne": "enriching"}}, {"status": "enriched"}, {"status": "discovered"}):
        p = await db.prospects.find_one_and_update(
            {**f, "contacts_status": {"$exists": False}},
            {"$set": {"contacts_status": "running", "contacts_started_at": now_utc_str()}},
            sort=[("contacts_priority", -1), ("store_count", -1), ("id", 1)], projection={"_id": 0})
        if p:
            return p
    return None
